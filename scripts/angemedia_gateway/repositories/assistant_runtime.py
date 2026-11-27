"""助手运行、租约及事件的原子仓库；不执行模型或业务工具。"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from typing import Any
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from ..assistant_runtime_contract import (
    TERMINAL_RUN_STATUSES, RunAdmission, RunLease,
    RunBudgetExceeded, RunBusy, RunClosed, RunNotFound, RunRequestConflict, require_owned_run, require_run_owner,
)
from ..assistant_sanitizer import runtime_payload, sanitize_assistant_text
from ..assistant_run_access import RunAccess, RunCursorInvalid, RunUnavailable, authorize_run_access, validate_event_cursor
from ..assistant_run_views import public_event, public_run
from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso
from .. import config as C
from .assistant_sessions import add_assistant_message, require_active_session


def _positive(value: int, maximum: int, label: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label}必须是1到{maximum}的整数")
    return value


def _run(conn, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM assistant_runs WHERE id=? AND runtime_managed=1", (run_id,)).fetchone()
    if row is None:
        raise RunNotFound("运行不存在或不属于运行内核")
    return dict(row)


def _event(conn, run_id: str, event_type: str, payload: Any = None) -> None:
    conn.execute("UPDATE assistant_runs SET event_seq=event_seq+1,version=version+1,updated_at=? WHERE id=?", (now_iso(), run_id))
    conn.execute(
        "INSERT INTO assistant_run_events(run_id,sequence,event_type,payload_json,created_at) "
        "SELECT id,event_seq,?,?,? FROM assistant_runs WHERE id=?",
        (event_type, runtime_payload({} if payload is None else payload), now_iso(), run_id),
    )


def _revoke(conn, run_id: str) -> None:
    # 槽位保留递增代际，避免新一轮运行复用旧的fence值。
    conn.execute("UPDATE assistant_session_leases SET fence=fence+1,run_id=NULL,owner_token=NULL,expires_ms=NULL WHERE run_id=?", (run_id,))


def _end(conn, run_id: str, status: str, output: str = "{}") -> None:
    conn.execute("UPDATE assistant_runs SET status=?,output_json=?,completed_at=? WHERE id=?", (status, output, now_iso(), run_id))
    _revoke(conn, run_id)
    _event(conn, run_id, status)


class AssistantRunRepository:
    def __init__(self, *, clock: Callable[[], int] | None = None, db_path: Path | None = None,
                 account_only: bool = False):
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.db_path = Path(C.DB_FILE if db_path is None else db_path).resolve()
        self.account_only = account_only

    def _require_execution_session(self, conn, session_id):
        require_active_session(conn, session_id)
        if self.account_only and not conn.execute(
            "SELECT 1 FROM assistant_sessions s JOIN assistant_session_owners o ON o.session_id=s.id "
            "WHERE s.id=? AND s.access_scope='account'", (session_id,),
        ).fetchone():
            raise RunNotFound("此会话不属于账户运行服务")

    def admit(self, session_id: str, request_key: str, input_payload: Any, *, timeout_ms: int = 120000,
              max_model_calls: int = 8, max_tool_calls: int = 16,
              access: RunAccess | None = None, accept_new: bool = True, max_pending: int | None = None,
              admission_check=None) -> RunAdmission:
        try:
            key = UUID(str(request_key)).hex
        except ValueError as exc:
            raise ValueError("request_key必须是UUID") from exc
        timeout_ms = _positive(timeout_ms, 86400000, "运行时限")
        max_model_calls = _positive(max_model_calls, 1000, "模型调用预算")
        max_tool_calls = _positive(max_tool_calls, 10000, "工具调用预算")
        if max_pending is not None:
            max_pending = _positive(max_pending, 10000, "待处理运行容量")
        payload = runtime_payload(input_payload)
        with db_transaction(immediate=True, path=self.db_path) as conn:
            if access is not None:
                authorize_run_access(conn, access, session_id)
            self._require_execution_session(conn, session_id)
            row = conn.execute("SELECT id FROM assistant_runs WHERE session_id=? AND request_key=? AND runtime_managed=1", (session_id, key)).fetchone()
            if row:
                run = _run(conn, row["id"])
                if (run["input_json"], run["timeout_ms"], run["max_model_calls"], run["max_tool_calls"]) != (payload, timeout_ms, max_model_calls, max_tool_calls):
                    raise RunRequestConflict("同一请求标识不能更换内容或预算")
                return RunAdmission(run, False)
            if not accept_new:
                raise RunUnavailable("运行服务当前不接受新请求")
            if admission_check is not None:
                admission_check(conn)
            if max_pending is not None and conn.execute(
                "SELECT count(*) FROM assistant_runs WHERE runtime_managed=1 AND status IN ('queued','running','paused') "
                "AND (?=0 OR EXISTS(SELECT 1 FROM assistant_sessions s JOIN assistant_session_owners o ON o.session_id=s.id "
                "WHERE s.id=assistant_runs.session_id AND s.access_scope='account'))", (int(self.account_only),),
            ).fetchone()[0] >= max_pending:
                raise RunBusy("待处理运行容量已满")
            run_id, timestamp = uuid4().hex, now_iso()
            conn.execute(
                "INSERT INTO assistant_runs(id,session_id,status,input_json,output_json,timeline_json,created_at,updated_at,"
                "runtime_managed,request_key,timeout_ms,deadline_ms,max_model_calls,max_tool_calls) "
                "VALUES(?,?,'queued',?,'{}','[]',?,?,1,?,?,?,?,?)",
                (run_id, session_id, payload, timestamp, timestamp, key, timeout_ms, self.clock()+timeout_ms, max_model_calls, max_tool_calls),
            )
            _event(conn, run_id, "admitted")
            return RunAdmission(_run(conn, run_id), True)

    def get(self, run_id: str) -> dict[str, Any]:
        with closing(db_connect(path=self.db_path)) as conn:
            return _run(conn, run_id)

    def ready(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """重启后发现各会话可恢复的队首；候选仍必须通过claim原子校验。"""
        limit = _positive(limit, 500, "恢复候选数量")
        with closing(db_connect(path=self.db_path)) as conn:
            rows = conn.execute(
                "SELECT r.* FROM assistant_runs r JOIN assistant_sessions s ON s.id=r.session_id "
                "LEFT JOIN assistant_session_leases l ON l.session_id=r.session_id "
                "WHERE r.runtime_managed=1 AND s.status='active' AND r.status IN ('queued','running','paused') "
                "AND (?=0 OR (s.access_scope='account' AND EXISTS(SELECT 1 FROM assistant_session_owners o WHERE o.session_id=s.id))) "
                "AND r.deadline_ms>? AND (l.owner_token IS NULL OR l.expires_ms<=?) "
                "AND NOT EXISTS(SELECT 1 FROM assistant_runs previous WHERE previous.session_id=r.session_id "
                "AND previous.runtime_managed=1 AND previous.status IN ('queued','running','paused') AND previous.rowid<r.rowid) "
                "ORDER BY r.rowid LIMIT ?", (int(self.account_only), self.clock(), self.clock(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim(self, run_id: str, *, lease_ms: int = 30000) -> RunLease:
        lease_ms = _positive(lease_ms, 300000, "租约时长")
        with db_transaction(immediate=True, path=self.db_path) as conn:
            run = _run(conn, run_id)
            self._require_execution_session(conn, run["session_id"])
            now = self.clock()
            if run["status"] in TERMINAL_RUN_STATUSES or now >= run["deadline_ms"]:
                raise RunClosed("运行已结束或到达总体时限")
            head = conn.execute(
                "SELECT id FROM assistant_runs WHERE session_id=? AND runtime_managed=1 "
                "AND status IN ('queued','running','paused') ORDER BY rowid LIMIT 1", (run["session_id"],),
            ).fetchone()
            if head["id"] != run_id:
                raise RunBusy("会话中有更早的未完成运行")
            conn.execute("INSERT OR IGNORE INTO assistant_session_leases(session_id) VALUES(?)", (run["session_id"],))
            slot = conn.execute("SELECT * FROM assistant_session_leases WHERE session_id=?", (run["session_id"],)).fetchone()
            if slot["owner_token"] and slot["expires_ms"] > now:
                raise RunBusy("会话已有执行持有者")
            token, fence = uuid4().hex, slot["fence"]+1
            conn.execute("UPDATE assistant_session_leases SET fence=?,run_id=?,owner_token=?,expires_ms=? WHERE session_id=?",
                         (fence, run_id, token, min(now+lease_ms, run["deadline_ms"]), run["session_id"]))
            conn.execute("UPDATE assistant_runs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?", (now_iso(), run_id))
            _event(conn, run_id, "started" if run["started_at"] is None else "resumed")
            return RunLease(run_id, run["session_id"], fence, token)

    def require_execution(self, conn, lease: RunLease) -> dict[str, Any]:
        """调用方持有本库事务，用于将租约复核与业务写入合为一次原子提交。"""
        run = _run(conn, lease.run_id)
        self._require_execution_session(conn, lease.session_id)
        slot = conn.execute("SELECT * FROM assistant_session_leases WHERE session_id=?", (lease.session_id,)).fetchone()
        require_owned_run(run, slot, lease, self.clock())
        return run

    @contextmanager
    def write_guard(self, lease: RunLease):
        """只包围短事务写入；禁止在持有写锁时调用模型或外部网络。"""
        with db_transaction(immediate=True, path=self.db_path) as conn:
            yield conn, self.require_execution(conn, lease)

    def renew(self, lease: RunLease, *, lease_ms: int = 30000) -> None:
        lease_ms = _positive(lease_ms, 300000, "租约时长")
        with self.write_guard(lease) as (conn, run):
            conn.execute("UPDATE assistant_session_leases SET expires_ms=? WHERE session_id=?",
                         (min(self.clock()+lease_ms, run["deadline_ms"]), lease.session_id))

    def pause(self, lease: RunLease) -> None:
        with self.write_guard(lease) as (conn, run):
            conn.execute("UPDATE assistant_runs SET status='paused' WHERE id=?", (lease.run_id,))
            _revoke(conn, lease.run_id)
            _event(conn, lease.run_id, "paused")

    def reserve_call(self, lease: RunLease, kind: str) -> None:
        if kind not in {"model", "tool"}:
            raise ValueError("调用种类仅允许model或tool")
        exhausted = False
        with self.write_guard(lease) as (conn, run):
            field, maximum = ("model_calls", "max_model_calls") if kind == "model" else ("tool_calls", "max_tool_calls")
            if run[field] >= run[maximum]:
                _end(conn, lease.run_id, "budget_exceeded", runtime_payload({"budget": kind}))
                exhausted = True
            else:
                conn.execute(f"UPDATE assistant_runs SET {field}={field}+1 WHERE id=?", (lease.run_id,))
                _event(conn, lease.run_id, "call_reserved", {"kind": kind, "count": run[field]+1})
        # 终态先提交再通知调用者，异常不能把预算终止回滚。
        if exhausted:
            raise RunBudgetExceeded("运行调用预算已耗尽")

    @staticmethod
    def _message(conn, lease: RunLease, role: str, content: str) -> dict[str, Any]:
        if role not in {"user", "assistant"}:
            raise ValueError("消息角色不受支持")
        message_id = uuid5(UUID(lease.run_id), role).hex
        text = sanitize_assistant_text(content, limit=8000)
        if not text:
            raise ValueError("消息不能为空")
        row = conn.execute("SELECT * FROM assistant_messages WHERE id=?", (message_id,)).fetchone()
        if row:
            if row["content"] != text or row["role"] != role or row["session_id"] != lease.session_id:
                raise RunRequestConflict("运行消息已发布且内容不同")
            message = dict(row)
            message["safe_payload"] = json.loads(message.pop("safe_payload_json") or "{}")
            return message
        message = add_assistant_message(message_id, lease.session_id, role, text, conn=conn)
        _event(conn, lease.run_id, "message_published", {"message_id": message_id, "role": role})
        return message

    def publish_message(self, lease: RunLease, role: str, content: str) -> dict[str, Any]:
        with self.write_guard(lease) as (conn, _run_state):
            return self._message(conn, lease, role, content)

    def context_history(self, lease: RunLease) -> list[dict[str, Any]]:
        """从同一运行库取最近上下文，排除本轮已幂等发布的用户消息。"""
        current = uuid5(UUID(lease.run_id), "user").hex
        with self.write_guard(lease) as (conn, _run_state):
            rows = conn.execute(
                "SELECT role,content FROM assistant_messages WHERE session_id=? AND id<>? "
                "ORDER BY created_at DESC,rowid DESC LIMIT 24", (lease.session_id, current),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def append_event(self, lease: RunLease, event_type: str, payload: Any = None) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event_type) or event_type in {
            *TERMINAL_RUN_STATUSES, "admitted", "started", "resumed", "paused", "message_published", "call_reserved"
        }:
            raise ValueError("事件类型非法或属于运行控制保留类型")
        encoded = runtime_payload({} if payload is None else payload)
        with self.write_guard(lease) as (conn, _run_state):
            _event(conn, lease.run_id, event_type, json.loads(encoded))

    def finish(self, lease: RunLease, *, status: str = "succeeded", output: Any = None, answer: str | None = None) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "refused"}:
            raise ValueError("完成状态不受支持")
        encoded = runtime_payload({} if output is None else output)
        with self.write_guard(lease) as (conn, _run_state):
            if answer is not None:
                self._message(conn, lease, "assistant", answer)
            _end(conn, lease.run_id, status, encoded)
            return _run(conn, lease.run_id)

    def cancel(self, run_id: str, *, access: RunAccess | None = None) -> dict[str, Any]:
        with db_transaction(immediate=True, path=self.db_path) as conn:
            run = _run(conn, run_id)
            if access is not None:
                authorize_run_access(conn, access, run["session_id"])
            require_active_session(conn, run["session_id"])
            if run["status"] not in TERMINAL_RUN_STATUSES:
                conn.execute("UPDATE assistant_runs SET cancel_requested_at=? WHERE id=?", (now_iso(), run_id))
                _end(conn, run_id, "canceled")
            return _run(conn, run_id)

    def snapshot(self, run_id: str, *, access: RunAccess, after: int = 0, limit: int = 100) -> dict[str, Any]:
        """同一快照复核登录/归属并读取状态、答案和事件，不跨事务拼装结果。"""
        validate_event_cursor(after, limit)
        with db_transaction(path=self.db_path) as conn:
            run = _run(conn, run_id)
            authorize_run_access(conn, access, run["session_id"])
            if after > run["event_seq"]:
                raise RunCursorInvalid("游标超过当前运行事件末尾")
            rows = conn.execute(
                "SELECT * FROM assistant_run_events WHERE run_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
                (run_id, after, run["event_seq"], limit),
            ).fetchall()
            answer = conn.execute("SELECT content FROM assistant_messages WHERE id=? AND session_id=?",
                                  (uuid5(UUID(run_id), "assistant").hex, run["session_id"])).fetchone()
            events = [public_event(row) for row in rows]
            cursor = events[-1]["sequence"] if events else after
            return {"run": public_run(run, answer["content"] if answer else None), "events": events,
                    "next_after": cursor, "has_more": cursor < run["event_seq"]}

    def activity(self, session_id: str, *, access: RunAccess) -> str | None:
        """先返回尚未结束的队首，否则返回最近终态；页面重开不依赖浏览器缓存。"""
        with db_transaction(path=self.db_path) as conn:
            authorize_run_access(conn, access, session_id)
            row = conn.execute(
                "SELECT id FROM assistant_runs WHERE session_id=? AND runtime_managed=1 "
                "ORDER BY CASE WHEN status IN ('queued','running','paused') THEN 0 ELSE 1 END, "
                "CASE WHEN status IN ('queued','running','paused') THEN created_at END ASC, created_at DESC,rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return row['id'] if row else None

    def timeout_owned(self, lease: RunLease) -> None:
        """总体单调时限到达后终止本代执行；允许到期收尾，绝不能改动接管后的运行。"""
        with db_transaction(immediate=True, path=self.db_path) as conn:
            run = _run(conn, lease.run_id)
            require_active_session(conn, lease.session_id)
            slot = conn.execute("SELECT * FROM assistant_session_leases WHERE session_id=?", (lease.session_id,)).fetchone()
            require_run_owner(run, slot, lease)
            _end(conn, lease.run_id, "timed_out")

    def expire_due(self, *, session_id: str | None = None, limit: int = 100) -> int:
        limit = _positive(limit, 1000, "过期扫描数量")
        with db_transaction(immediate=True, path=self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM assistant_runs WHERE runtime_managed=1 AND status IN ('queued','running','paused') "
                "AND (?=0 OR EXISTS(SELECT 1 FROM assistant_sessions s JOIN assistant_session_owners o ON o.session_id=s.id "
                "WHERE s.id=assistant_runs.session_id AND s.access_scope='account')) "
                "AND deadline_ms<=? AND (? IS NULL OR session_id=?) ORDER BY deadline_ms LIMIT ?",
                (int(self.account_only), self.clock(), session_id, session_id, limit),
            ).fetchall()
            for row in rows:
                _end(conn, row["id"], "timed_out")
            return len(rows)

    def events(self, run_id: str, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if type(after) is not int or after < 0:
            raise ValueError("事件游标必须是非负整数")
        limit = _positive(limit, 500, "事件数量")
        with closing(db_connect(path=self.db_path)) as conn:
            _run(conn, run_id)
            rows = conn.execute("SELECT * FROM assistant_run_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?", (run_id, after, limit)).fetchall()
        return [{"run_id": row["run_id"], "sequence": row["sequence"], "event_type": row["event_type"],
                 "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows]

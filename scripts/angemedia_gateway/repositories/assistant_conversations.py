"""账户会话仓库；授权与数据访问共用事务，不接触模型或HTTP。"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any
from uuid import UUID, uuid4, uuid5

from .. import config as C
from ..assistant_sanitizer import sanitize_assistant_text
from ..db.connection import db_transaction
from ..helpers import now_iso
from ..assistant_runtime_contract import RunRequestConflict
from .assistant_sessions import archive_assistant_session


class ConversationNotFound(LookupError):
    """不存在、已归档和不属于当前账户统一视为不可见。"""


def require_conversation_owner(conn: sqlite3.Connection, session_id: str, username: str,
                               *, active: bool = True) -> sqlite3.Row:
    """供后续运行服务复用；调用者须持有与操作共用的事务。"""
    row = conn.execute(
        "SELECT s.id,s.title,s.status,s.created_at,s.updated_at FROM assistant_sessions s "
        "JOIN assistant_session_owners o ON o.session_id=s.id "
        "WHERE s.id=? AND o.username=? AND s.access_scope='account' AND (?=0 OR s.status='active')",
        (session_id, username, int(active)),
    ).fetchone()
    if row is None:
        raise ConversationNotFound("助手会话不存在")
    return row


def _session(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "title": sanitize_assistant_text(row["title"], limit=120)}


class AssistantConversationRepository:
    def __init__(self, *, db_path: Path | None = None):
        self.db_path = Path(C.DB_FILE if db_path is None else db_path).resolve()

    def create(self, *, username: str, title: str, request_key: str | None = None,
               source_session_id: str | None = None) -> dict[str, Any]:
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError("会话标题须为1到120个字符")
        title = sanitize_assistant_text(title, limit=120)
        # 客户端键确定创建资源；复制键以来源会话为作用域，重试不能重复导入消息。
        identity = UUID(request_key) if request_key else uuid4()
        session_id = uuid5(identity, 'legacy:' + source_session_id).hex if source_session_id else identity.hex
        timestamp = now_iso()
        with db_transaction(immediate=True, path=self.db_path) as conn:
            # 授权依赖检查之后账户可能已改名；不能以旧主体创建孤立记录。
            if not conn.execute("SELECT 1 FROM admin_users WHERE username=?", (username,)).fetchone():
                raise ConversationNotFound("助手会话不存在")
            if conn.execute("SELECT 1 FROM assistant_sessions WHERE id=?", (session_id,)).fetchone():
                existing = require_conversation_owner(conn, session_id, username)
                if existing['title'] != title:
                    raise RunRequestConflict("创建意图不同")
                return _session(existing)
            source = []
            if source_session_id:
                if not conn.execute("SELECT 1 FROM assistant_sessions WHERE id=? AND access_scope='shared' AND status='active'",
                                    (source_session_id,)).fetchone():
                    raise ConversationNotFound("旧会话不存在")
                source = conn.execute("SELECT role,content,created_at FROM assistant_messages WHERE session_id=? "
                                      "AND role IN ('user','assistant') ORDER BY created_at DESC,rowid DESC LIMIT 100",
                                      (source_session_id,)).fetchall()
            conn.execute(
                "INSERT INTO assistant_sessions(id,title,status,created_at,updated_at,access_scope) "
                "VALUES(?,?,'active',?,?,'account')", (session_id, title, timestamp, timestamp),
            )
            conn.execute("INSERT INTO assistant_session_owners(session_id,username) VALUES(?,?)", (session_id, username))
            for message in reversed(source):
                conn.execute("INSERT INTO assistant_messages(id,session_id,role,content,created_at,safe_payload_json) VALUES(?,?,?,?,?,'{}')",
                             (uuid4().hex, session_id, message['role'],
                              sanitize_assistant_text(message['content'], limit=8000), message['created_at']))
            return _session(require_conversation_owner(conn, session_id, username))

    def list(self, *, username: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("会话分页参数无效")
        with db_transaction(path=self.db_path) as conn:
            where = ("FROM assistant_sessions s JOIN assistant_session_owners o ON o.session_id=s.id "
                     "WHERE o.username=? AND s.access_scope='account' AND s.status='active'")
            total = conn.execute("SELECT count(*) " + where, (username,)).fetchone()[0]
            rows = conn.execute("SELECT s.id,s.title,s.status,s.created_at,s.updated_at " + where +
                                " ORDER BY s.updated_at DESC,s.created_at DESC,s.id DESC LIMIT ? OFFSET ?",
                                (username, limit, offset)).fetchall()
            return {"items": [_session(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def detail(self, session_id: str, *, username: str, message_limit: int = 50,
               before: str | None = None) -> dict[str, Any]:
        if type(message_limit) is not int or not 1 <= message_limit <= 100:
            raise ValueError("消息条数须为1到100的整数")
        with db_transaction(path=self.db_path) as conn:
            session = _session(require_conversation_owner(conn, session_id, username))
            total = conn.execute("SELECT count(*) FROM assistant_messages WHERE session_id=?", (session_id,)).fetchone()[0]
            boundary, params = '', [session_id]
            if before is not None:
                anchor = conn.execute("SELECT created_at,rowid FROM assistant_messages WHERE session_id=? AND id=?",
                                      (session_id, before)).fetchone()
                if anchor is None:
                    raise ConversationNotFound("消息不存在")
                boundary = "AND (created_at < ? OR (created_at = ? AND rowid < ?)) "
                params.extend((anchor['created_at'], anchor['created_at'], anchor['rowid']))
            rows = conn.execute(
                "SELECT id,session_id,role,content,created_at FROM assistant_messages WHERE session_id=? "
                + boundary + "ORDER BY created_at DESC,rowid DESC LIMIT ?", (*params, message_limit + 1),
            ).fetchall()
            has_older = len(rows) > message_limit
            rows = rows[:message_limit]
            return {"session": session, "messages": [
                {**dict(row), "content": sanitize_assistant_text(row["content"], limit=8000)} for row in reversed(rows)
            ], "message_total": total, "message_limit": message_limit, "has_older": has_older,
                "before": rows[-1]['id'] if rows else None}

    def archive(self, session_id: str, *, username: str) -> None:
        with db_transaction(immediate=True, path=self.db_path) as conn:
            # 重复归档幂等，归属记录继续保留，不能成为共享或被另一账户认领。
            require_conversation_owner(conn, session_id, username, active=False)
            archive_assistant_session(conn, session_id)

"""Assistant session, message, and run persistence."""
from __future__ import annotations

from contextlib import closing
from typing import Any
import sqlite3

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso, safe_json


def create_assistant_session(session_id: str, title: str) -> dict[str, Any]:
    timestamp = now_iso()
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO assistant_sessions(id,title,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (session_id, title[:120] or "AngeMedia Assistant", "active", timestamp, timestamp),
        )
    return get_assistant_session(session_id) or {}


def touch_assistant_session(session_id: str, *, title: str | None = None) -> None:
    updates = ["updated_at = ?"]
    values: list[Any] = [now_iso()]
    if title:
        updates.append("title = ?")
        values.append(title[:120])
    values.append(session_id)
    with closing(db_connect()) as conn:
        conn.execute(f"UPDATE assistant_sessions SET {', '.join(updates)} WHERE id = ? AND access_scope='shared' AND status='active'", values)


def get_assistant_session(session_id: str, *, reject_private: bool = False) -> dict[str, Any] | None:
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT id,title,status,created_at,updated_at,access_scope FROM assistant_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    if row["access_scope"] != "shared":
        if reject_private:
            # 不能把账户会话的继续提问悄悄改存进新的共享会话。
            raise ValueError("此会话不能通过旧助手接口继续")
        return None
    return {key: row[key] for key in ("id", "title", "status", "created_at", "updated_at")} if row["status"] == "active" else None


def list_assistant_sessions(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit or 20), 100))
    bounded_offset = max(0, int(offset or 0))
    with closing(db_connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM assistant_sessions WHERE status = 'active' AND access_scope='shared'").fetchone()[0]
        rows = conn.execute(
            """
            SELECT id,title,status,created_at,updated_at
            FROM assistant_sessions
            WHERE status = 'active' AND access_scope='shared'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ? OFFSET ?
            """,
            (bounded_limit, bounded_offset),
        ).fetchall()
    return {"total": total, "limit": bounded_limit, "offset": bounded_offset, "items": [dict(row) for row in rows]}


def require_active_session(conn: sqlite3.Connection, session_id: str, *, legacy_only: bool = False) -> None:
    """写入时确认会话仍活跃，归档不能被迟到的运行重新写入。"""
    if not conn.execute("SELECT 1 FROM assistant_sessions WHERE id=? AND status='active' "
                        "AND (?=0 OR access_scope='shared')", (session_id, int(legacy_only))).fetchone():
        raise ValueError("助手会话已归档或不存在")


def archive_assistant_session(conn: sqlite3.Connection, session_id: str) -> None:
    """内部原子归档操作；调用者须在同一写事务内完成访问授权。"""
    conn.execute("UPDATE assistant_sessions SET status='archived',updated_at=? WHERE id=?", (now_iso(), session_id))
    conn.execute("DELETE FROM assistant_session_leases WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM assistant_messages WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM assistant_runs WHERE session_id=?", (session_id,))


def delete_assistant_session(session_id: str) -> bool:
    """归档、撤销租约及清除消息/运行在同一事务完成。"""
    with db_transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM assistant_sessions WHERE id=? AND access_scope='shared'", (session_id,)).fetchone():
            return False
        archive_assistant_session(conn, session_id)
    return True


def add_assistant_message(
    message_id: str,
    session_id: str,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
    *, conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    timestamp = now_iso()

    def insert(connection: sqlite3.Connection) -> dict[str, Any]:
        # 运行内核传入的事务已校验租约；旧入口自行打开事务时只允许共享会话。
        require_active_session(connection, session_id, legacy_only=conn is None)
        connection.execute(
            """
            INSERT INTO assistant_messages(id,session_id,role,content,safe_payload_json,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (message_id, session_id, role, content[:8000], safe_json(payload or {}), timestamp),
        )
        connection.execute("UPDATE assistant_sessions SET updated_at = ? WHERE id = ?", (timestamp, session_id))
        return {
            "id": message_id,
            "session_id": session_id,
            "role": role,
            "content": content[:8000],
            "safe_payload": payload or {},
            "created_at": timestamp,
        }

    if conn is not None:
        return insert(conn)
    with db_transaction(immediate=True) as connection:
        return insert(connection)


def list_assistant_messages(session_id: str, limit: int = 50, *, recent: bool = False) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit or 50), 100))
    with closing(db_connect()) as conn:
        rows = conn.execute(
            f"""
            SELECT id,session_id,role,content,safe_payload_json,created_at
            FROM assistant_messages
            WHERE session_id = ?
              AND EXISTS(SELECT 1 FROM assistant_sessions s WHERE s.id=assistant_messages.session_id AND s.access_scope='shared' AND s.status='active')
            ORDER BY created_at {"DESC" if recent else "ASC"}, rowid {"DESC" if recent else "ASC"}
            LIMIT ?
            """,
            (session_id, bounded_limit),
        ).fetchall()
    if recent:
        rows = list(reversed(rows))
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("safe_payload_json", None)
        items.append(item)
    return items


def add_assistant_run(
    run_id: str,
    session_id: str,
    status: str,
    skill_id: str | None,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    if status not in {"succeeded", "failed", "refused"}:
        raise ValueError("旧助手记录只接受终态")
    timestamp = now_iso()
    with db_transaction(immediate=True) as conn:
        require_active_session(conn, session_id, legacy_only=True)
        conn.execute(
            """
            INSERT INTO assistant_runs(
                id,session_id,status,skill_id,input_json,output_json,timeline_json,created_at,completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                session_id,
                status,
                skill_id,
                safe_json(input_payload),
                safe_json(output_payload),
                safe_json(timeline),
                timestamp,
                timestamp,
            ),
        )
    return {
        "id": run_id,
        "session_id": session_id,
        "status": status,
        "skill_id": skill_id,
        "timeline": timeline,
        "created_at": timestamp,
        "completed_at": timestamp,
    }

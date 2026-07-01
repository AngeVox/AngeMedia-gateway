"""Assistant session, message, and run persistence."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ..db.connection import db_connect
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
        conn.execute(f"UPDATE assistant_sessions SET {', '.join(updates)} WHERE id = ?", values)


def get_assistant_session(session_id: str) -> dict[str, Any] | None:
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT id,title,status,created_at,updated_at FROM assistant_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def list_assistant_sessions(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit or 20), 100))
    bounded_offset = max(0, int(offset or 0))
    with closing(db_connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM assistant_sessions WHERE status = 'active'").fetchone()[0]
        rows = conn.execute(
            """
            SELECT id,title,status,created_at,updated_at
            FROM assistant_sessions
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ? OFFSET ?
            """,
            (bounded_limit, bounded_offset),
        ).fetchall()
    return {"total": total, "limit": bounded_limit, "offset": bounded_offset, "items": [dict(row) for row in rows]}


def add_assistant_message(
    message_id: str,
    session_id: str,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = now_iso()
    with closing(db_connect()) as conn:
        conn.execute(
            """
            INSERT INTO assistant_messages(id,session_id,role,content,safe_payload_json,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (message_id, session_id, role, content[:8000], safe_json(payload or {}), timestamp),
        )
        conn.execute("UPDATE assistant_sessions SET updated_at = ? WHERE id = ?", (timestamp, session_id))
    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content[:8000],
        "safe_payload": payload or {},
        "created_at": timestamp,
    }


def list_assistant_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit or 50), 100))
    with closing(db_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id,session_id,role,content,safe_payload_json,created_at
            FROM assistant_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (session_id, bounded_limit),
        ).fetchall()
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
    timestamp = now_iso()
    with closing(db_connect()) as conn:
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

"""Append-only persistence for sanitized job lifecycle events."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any
from uuid import uuid4

from ..db.connection import db_connect
from ..helpers import now_iso
from ..job_sanitizer import sanitized_json


def append_job_event(
    job_id: str,
    event_type: str,
    payload: Any = None,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    stage: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    event_id = uuid4().hex
    created_at = now_iso()
    payload_json = sanitized_json(payload) if payload is not None else None

    def insert(connection: sqlite3.Connection) -> dict[str, Any]:
        connection.execute(
            "INSERT INTO job_events("
            "id,job_id,event_type,from_status,to_status,stage,payload_json,created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (event_id, job_id, event_type, from_status, to_status, stage, payload_json, created_at),
        )
        row = connection.execute("SELECT * FROM job_events WHERE id=?", (event_id,)).fetchone()
        return dict(row)

    if conn is not None:
        return insert(conn)
    with closing(db_connect()) as connection:
        return insert(connection)


def list_job_events(job_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM job_events WHERE job_id=? ORDER BY created_at,id LIMIT ?",
            (job_id, max(1, min(int(limit), 500))),
        ).fetchall()
    return [dict(row) for row in rows]

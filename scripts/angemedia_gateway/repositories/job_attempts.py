"""Persistence for sanitized worker attempt summaries."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any
from uuid import uuid4

from ..db.connection import db_connect
from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text, sanitized_json


def create_job_attempt(
    *,
    job_id: str,
    attempt_number: int,
    stage: str,
    worker_kind: str | None = None,
    status: str = "running",
    started_at: str | None = None,
    completed_at: str | None = None,
    retry_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    detail: Any = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    attempt_id = uuid4().hex
    detail_json = sanitized_json(detail) if detail is not None else None
    safe_error = sanitize_error_text(error_message)

    def insert(connection: sqlite3.Connection) -> dict[str, Any]:
        connection.execute(
            "INSERT INTO job_attempts("
            "id,job_id,attempt_number,stage,worker_kind,status,started_at,completed_at,"
            "retry_at,error_code,error_message,detail_json"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                attempt_id, job_id, int(attempt_number), stage,
                sanitize_error_text(worker_kind, limit=128), status,
                started_at or now_iso(), completed_at, retry_at,
                sanitize_error_text(error_code, limit=128), safe_error, detail_json,
            ),
        )
        row = connection.execute("SELECT * FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()
        return dict(row)

    if conn is not None:
        return insert(conn)
    with closing(db_connect()) as connection:
        return insert(connection)


def list_job_attempts(job_id: str) -> list[dict[str, Any]]:
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM job_attempts WHERE job_id=? ORDER BY attempt_number", (job_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_job_attempt(
    job_id: str,
    attempt_number: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    def select(connection: sqlite3.Connection) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM job_attempts WHERE job_id=? AND attempt_number=?",
            (job_id, int(attempt_number)),
        ).fetchone()
        return dict(row) if row else None

    if conn is not None:
        return select(conn)
    with closing(db_connect()) as connection:
        return select(connection)


def get_running_stage_attempt(
    job_id: str,
    stage: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    def select(connection: sqlite3.Connection) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM job_attempts WHERE job_id=? AND stage=? AND status='running' "
            "ORDER BY attempt_number DESC LIMIT 1",
            (job_id, stage),
        ).fetchone()
        return dict(row) if row else None

    if conn is not None:
        return select(conn)
    with closing(db_connect()) as connection:
        return select(connection)


def finish_job_attempt(
    *,
    job_id: str,
    attempt_number: int,
    status: str,
    completed_at: str,
    retry_at: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    detail: Any = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Finalize one claimed attempt without exposing provider details."""
    if status not in {"succeeded", "failed", "canceled"}:
        raise ValueError("attempt completion status is invalid")
    def update(connection: sqlite3.Connection) -> dict[str, Any] | None:
        cursor = connection.execute(
            "UPDATE job_attempts SET status=?,completed_at=?,retry_at=?,error_code=?,error_message=?,detail_json=? "
            "WHERE job_id=? AND attempt_number=? AND status='running'",
            (
                status, completed_at, retry_at, sanitize_error_text(error_code, limit=128),
                sanitize_error_text(error_message),
                sanitized_json(detail) if detail is not None else None,
                job_id, int(attempt_number),
            ),
        )
        return get_job_attempt(job_id, attempt_number, conn=connection)

    if conn is not None:
        return update(conn)
    with closing(db_connect()) as connection:
        return update(connection)

"""Transactional outbox persistence, independent of any broker implementation."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any
from uuid import uuid4

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text, sanitized_json
from ..queue.settings import WORKER_TASK_NAME


class OutboxClaimLost(RuntimeError):
    pass


def create_job_dispatch(
    *,
    job_id: str,
    topic: str,
    payload: Any,
    available_at: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    if topic != WORKER_TASK_NAME:
        raise ValueError("unsupported queue task topic")
    dispatch_id = uuid4().hex
    now = now_iso()
    payload_json = sanitized_json(payload)

    def insert(connection: sqlite3.Connection) -> dict[str, Any]:
        connection.execute(
            "INSERT INTO job_dispatches("
            "id,job_id,topic,payload_json,status,available_at,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (dispatch_id, job_id, topic, payload_json, "pending", available_at or now, now, now),
        )
        row = connection.execute("SELECT * FROM job_dispatches WHERE id=?", (dispatch_id,)).fetchone()
        return dict(row)

    if conn is not None:
        return insert(conn)
    with closing(db_connect()) as connection:
        return insert(connection)


def get_job_dispatch(dispatch_id: str) -> dict[str, Any] | None:
    with closing(db_connect()) as conn:
        row = conn.execute("SELECT * FROM job_dispatches WHERE id=?", (dispatch_id,)).fetchone()
    return dict(row) if row else None


def list_pending_dispatches(*, limit: int = 100, available_before: str | None = None) -> list[dict[str, Any]]:
    cutoff = available_before or now_iso()
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM job_dispatches WHERE status='pending' AND available_at<=? "
            "ORDER BY available_at,created_at LIMIT ?",
            (cutoff, max(1, min(int(limit), 500))),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_pending_dispatches(
    *,
    claim_token: str,
    claim_expires_at: str,
    limit: int = 100,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Atomically lease due outbox rows for one future dispatcher process."""
    if not claim_token or not claim_expires_at:
        raise ValueError("claim_token and claim_expires_at are required")
    cutoff = now or now_iso()
    bounded_limit = max(1, min(int(limit), 500))
    with db_transaction(immediate=True) as conn:
        ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT id FROM job_dispatches WHERE "
                "(status='pending' AND available_at<=?) OR "
                "(status='publishing' AND claim_expires_at IS NOT NULL AND claim_expires_at<=?) "
                "ORDER BY available_at,created_at LIMIT ?",
                (cutoff, cutoff, bounded_limit),
            ).fetchall()
        ]
        claimed: list[dict[str, Any]] = []
        for dispatch_id in ids:
            conn.execute(
                "UPDATE job_dispatches SET status='publishing',claim_token=?,claim_expires_at=?,"
                "attempt_count=attempt_count+1,updated_at=?,version=version+1 WHERE id=?",
                (claim_token, claim_expires_at, cutoff, dispatch_id),
            )
            row = conn.execute("SELECT * FROM job_dispatches WHERE id=?", (dispatch_id,)).fetchone()
            claimed.append(dict(row))
        return claimed


def mark_dispatch_published(
    dispatch_id: str,
    *,
    claim_token: str,
    broker_message_id: str | None = None,
) -> dict[str, Any]:
    now = now_iso()
    with db_transaction(immediate=True) as conn:
        cursor = conn.execute(
            "UPDATE job_dispatches SET status='published',published_at=?,broker_message_id=?,"
            "claim_token=NULL,claim_expires_at=NULL,last_error=NULL,updated_at=?,version=version+1 "
            "WHERE id=? AND status='publishing' AND claim_token=?",
            (now, sanitize_error_text(broker_message_id, limit=256), now, dispatch_id, claim_token),
        )
        if cursor.rowcount != 1:
            raise OutboxClaimLost(f"outbox claim lost: {dispatch_id}")
        row = conn.execute("SELECT * FROM job_dispatches WHERE id=?", (dispatch_id,)).fetchone()
        return dict(row)


def release_dispatch(
    dispatch_id: str,
    *,
    claim_token: str,
    error_message: str,
    available_at: str | None = None,
    terminal: bool = False,
) -> dict[str, Any]:
    now = now_iso()
    status = "failed" if terminal else "pending"
    with db_transaction(immediate=True) as conn:
        cursor = conn.execute(
            "UPDATE job_dispatches SET status=?,available_at=?,last_error=?,"
            "claim_token=NULL,claim_expires_at=NULL,updated_at=?,version=version+1 "
            "WHERE id=? AND status='publishing' AND claim_token=?",
            (
                status, available_at or now, sanitize_error_text(error_message), now,
                dispatch_id, claim_token,
            ),
        )
        if cursor.rowcount != 1:
            raise OutboxClaimLost(f"outbox claim lost: {dispatch_id}")
        row = conn.execute("SELECT * FROM job_dispatches WHERE id=?", (dispatch_id,)).fetchone()
        return dict(row)

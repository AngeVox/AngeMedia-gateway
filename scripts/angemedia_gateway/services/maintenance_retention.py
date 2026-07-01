"""Safe retention cleanup for long-running Studio installations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing
from typing import Any

import sqlite3

from ..db.connection import db_connect, db_transaction

CONFIRM_PHRASE = "CLEAN_OLD_RECORDS"
TERMINAL_JOB_STATUSES = ("succeeded", "failed", "canceled")


class RetentionPolicyError(ValueError):
    """Raised when a retention request is malformed or unsafe."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


def normalize_retention_request(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload or {}
    older_than_days = _bounded_int(data.get("older_than_days", 30), "older_than_days", 1, 3650)
    limit = _bounded_int(data.get("limit", 500), "limit", 1, 5000)
    return {
        "older_than_days": older_than_days,
        "limit": limit,
        "include_jobs": bool(data.get("include_jobs", True)),
        "include_assistant_sessions": bool(data.get("include_assistant_sessions", True)),
        "cutoff": _cutoff_iso(older_than_days),
    }


def retention_preview(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = normalize_retention_request(payload)
    with closing(db_connect()) as conn:
        return _build_preview(conn, policy)


def retention_cleanup(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    if str(data.get("confirm") or "").strip() != CONFIRM_PHRASE:
        raise RetentionPolicyError("confirm_required", "confirm")
    policy = normalize_retention_request(data)
    with db_transaction(immediate=True) as conn:
        before = _build_preview(conn, policy)
        deleted = _delete_candidates(conn, policy)
    return {**before, "deleted": deleted}


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RetentionPolicyError("invalid_filter", field) from exc
    if number < minimum or number > maximum:
        raise RetentionPolicyError("invalid_filter", field)
    return number


def _cutoff_iso(older_than_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()


def _build_preview(conn: sqlite3.Connection, policy: dict[str, Any]) -> dict[str, Any]:
    job_ids = _candidate_job_ids(conn, policy)
    session_ids = _candidate_session_ids(conn, policy)
    return {
        "cutoff": policy["cutoff"],
        "older_than_days": policy["older_than_days"],
        "limit": policy["limit"],
        "jobs": _count_job_records(conn, job_ids),
        "assistant": _count_assistant_records(conn, session_ids),
        "media_files_deleted": 0,
        "assets_deleted": 0,
        "requires_confirm": CONFIRM_PHRASE,
    }


def _delete_candidates(conn: sqlite3.Connection, policy: dict[str, Any]) -> dict[str, int]:
    job_ids = _candidate_job_ids(conn, policy)
    session_ids = _candidate_session_ids(conn, policy)
    job_counts = _delete_job_records(conn, job_ids)
    assistant_counts = _delete_assistant_records(conn, session_ids)
    return {
        **job_counts,
        **assistant_counts,
        "assets_deleted": 0,
        "media_files_deleted": 0,
    }


def _candidate_job_ids(conn: sqlite3.Connection, policy: dict[str, Any]) -> list[str]:
    if not policy["include_jobs"]:
        return []
    placeholders = ",".join("?" for _ in TERMINAL_JOB_STATUSES)
    rows = conn.execute(
        "SELECT id FROM jobs "
        f"WHERE status IN ({placeholders}) AND updated_at < ? "
        "ORDER BY updated_at ASC, created_at ASC, id ASC LIMIT ?",
        (*TERMINAL_JOB_STATUSES, policy["cutoff"], policy["limit"]),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _candidate_session_ids(conn: sqlite3.Connection, policy: dict[str, Any]) -> list[str]:
    if not policy["include_assistant_sessions"]:
        return []
    rows = conn.execute(
        "SELECT id FROM assistant_sessions WHERE updated_at < ? "
        "ORDER BY updated_at ASC, created_at ASC, id ASC LIMIT ?",
        (policy["cutoff"], policy["limit"]),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _count_job_records(conn: sqlite3.Connection, job_ids: list[str]) -> dict[str, int]:
    return {
        "jobs": len(job_ids),
        "events": _count_by_ids(conn, "job_events", "job_id", job_ids),
        "attempts": _count_by_ids(conn, "job_attempts", "job_id", job_ids),
        "dispatches": _count_by_ids(conn, "job_dispatches", "job_id", job_ids),
        "assets_to_unlink": _count_by_ids(conn, "assets", "job_id", job_ids),
        "generations_to_unlink": _count_by_ids(conn, "generations", "job_id", job_ids),
    }


def _count_assistant_records(conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, int]:
    return {
        "sessions": len(session_ids),
        "messages": _count_by_ids(conn, "assistant_messages", "session_id", session_ids),
        "runs": _count_by_ids(conn, "assistant_runs", "session_id", session_ids),
    }


def _delete_job_records(conn: sqlite3.Connection, job_ids: list[str]) -> dict[str, int]:
    if not job_ids:
        return {
            "deleted_jobs": 0,
            "deleted_events": 0,
            "deleted_attempts": 0,
            "deleted_dispatches": 0,
            "unlinked_assets": 0,
            "unlinked_generations": 0,
        }
    return {
        "unlinked_assets": _update_null_by_ids(conn, "assets", "job_id", job_ids),
        "unlinked_generations": _update_null_by_ids(conn, "generations", "job_id", job_ids),
        "deleted_events": _delete_by_ids(conn, "job_events", "job_id", job_ids),
        "deleted_attempts": _delete_by_ids(conn, "job_attempts", "job_id", job_ids),
        "deleted_dispatches": _delete_by_ids(conn, "job_dispatches", "job_id", job_ids),
        "deleted_jobs": _delete_by_ids(conn, "jobs", "id", job_ids),
    }


def _delete_assistant_records(conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, int]:
    if not session_ids:
        return {
            "deleted_assistant_sessions": 0,
            "deleted_assistant_messages": 0,
            "deleted_assistant_runs": 0,
        }
    return {
        "deleted_assistant_messages": _delete_by_ids(conn, "assistant_messages", "session_id", session_ids),
        "deleted_assistant_runs": _delete_by_ids(conn, "assistant_runs", "session_id", session_ids),
        "deleted_assistant_sessions": _delete_by_ids(conn, "assistant_sessions", "id", session_ids),
    }


def _count_by_ids(conn: sqlite3.Connection, table: str, column: str, ids: list[str]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    row = conn.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {column} IN ({placeholders})", ids).fetchone()
    return int(row["total"] if row is not None else 0)


def _delete_by_ids(conn: sqlite3.Connection, table: str, column: str, ids: list[str]) -> int:
    placeholders = ",".join("?" for _ in ids)
    return int(conn.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", ids).rowcount)


def _update_null_by_ids(conn: sqlite3.Connection, table: str, column: str, ids: list[str]) -> int:
    placeholders = ",".join("?" for _ in ids)
    return int(conn.execute(f"UPDATE {table} SET {column}=NULL WHERE {column} IN ({placeholders})", ids).rowcount)

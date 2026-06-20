"""Durable jobs repository with versioned compare-and-swap updates."""
from __future__ import annotations

import sqlite3
import re
from collections.abc import Iterable
from contextlib import closing
from typing import Any
from uuid import uuid4

from ..db.connection import db_connect, db_transaction
from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text, sanitize_json_text

ACTIVE_JOB_STATUSES = ("queued", "running")
TERMINAL_JOB_STATUSES = ("succeeded", "failed", "canceled")
VALID_JOB_STATUSES = set(ACTIVE_JOB_STATUSES + TERMINAL_JOB_STATUSES)
VALID_JOB_STAGES = {
    "admitted", "image_generate", "video_submit", "video_poll", "asset_import", "finalize",
}
JOB_STAGES_BY_KIND = {
    "image": {"admitted", "image_generate", "asset_import", "finalize"},
    "video": {"admitted", "video_submit", "video_poll", "asset_import", "finalize"},
}
ALLOWED_STAGE_TRANSITIONS = {
    "admitted": {"admitted", "image_generate", "video_submit", "finalize"},
    "image_generate": {"image_generate", "asset_import", "finalize"},
    "video_submit": {"video_submit", "video_poll", "finalize"},
    "video_poll": {"video_poll", "asset_import", "finalize"},
    "asset_import": {"asset_import", "finalize"},
    "finalize": {"finalize"},
}
_REQUEST_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")
ALLOWED_STATUS_TRANSITIONS = {
    "queued": {"queued", "running", "failed", "canceled"},
    "running": {"running", "succeeded", "failed", "canceled"},
    "succeeded": {"succeeded"},
    "failed": {"failed"},
    "canceled": {"canceled"},
}


class JobNotFoundError(LookupError):
    pass


class InvalidJobTransitionError(RuntimeError):
    pass


class StaleJobVersionError(RuntimeError):
    pass


_JOB_COLUMNS = (
    "id,kind,status,provider,model,prompt,input_json,output_json,"
    "error_code,error_message,external_task_id,"
    "created_at,updated_at,started_at,completed_at,duration_ms,"
    "request_hash,request_hash_version,"
    "error_category,human_hint,retryable,gateway_stage,"
    "stage,payload_schema_version,priority,scheduled_at,next_retry_at,"
    "attempt_count,max_attempts,claim_token,claim_expires_at,worker_kind,"
    "provider_status,cancel_requested_at,version"
)


def _job_from_connection(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def insert_job(
    conn: sqlite3.Connection,
    *,
    kind: str,
    status: str = "queued",
    provider: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    input_json: str | None = None,
    output_json: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    external_task_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    request_hash: str | None = None,
    request_hash_version: int | None = None,
    error_category: str | None = None,
    human_hint: str | None = None,
    retryable: int = 0,
    gateway_stage: str | None = None,
    stage: str | None = None,
    payload_schema_version: int = 1,
    priority: int = 0,
    scheduled_at: str | None = None,
    next_retry_at: str | None = None,
    attempt_count: int = 0,
    max_attempts: int = 3,
    claim_token: str | None = None,
    claim_expires_at: str | None = None,
    worker_kind: str | None = None,
    provider_status: str | None = None,
    cancel_requested_at: str | None = None,
    version: int = 0,
    job_id: str | None = None,
) -> dict[str, Any]:
    if kind not in JOB_STAGES_BY_KIND:
        raise sqlite3.IntegrityError(f"invalid job kind: {kind}")
    if status not in VALID_JOB_STATUSES:
        raise sqlite3.IntegrityError(f"invalid job status: {status}")
    effective_stage = stage or ("finalize" if status in TERMINAL_JOB_STATUSES else "admitted")
    if effective_stage not in VALID_JOB_STAGES:
        raise ValueError(f"invalid job stage: {effective_stage}")
    if effective_stage not in JOB_STAGES_BY_KIND.get(kind, set()):
        raise ValueError(f"stage {effective_stage} is not valid for {kind} jobs")
    if request_hash is not None and not _REQUEST_HASH_RE.fullmatch(str(request_hash)):
        raise ValueError("request_hash must be a SHA-256 hex digest")
    if (request_hash is None) != (request_hash_version is None):
        raise ValueError("request_hash and request_hash_version must be provided together")
    now = now_iso()
    identifier = job_id or uuid4().hex
    conn.execute(
        "INSERT INTO jobs("
        "id,kind,status,provider,model,prompt,input_json,output_json,error_code,error_message,"
        "external_task_id,created_at,updated_at,started_at,completed_at,duration_ms,"
        "request_hash,request_hash_version,error_category,human_hint,retryable,gateway_stage,"
        "stage,payload_schema_version,priority,scheduled_at,next_retry_at,attempt_count,max_attempts,"
        "claim_token,claim_expires_at,worker_kind,provider_status,cancel_requested_at,version"
        ") VALUES(" + ",".join("?" for _ in range(35)) + ")",
        (
            identifier, kind, status, sanitize_error_text(provider, limit=256),
            sanitize_error_text(model, limit=256), sanitize_error_text(prompt, limit=8000),
            sanitize_json_text(input_json), sanitize_json_text(output_json),
            sanitize_error_text(error_code, limit=128),
            sanitize_error_text(error_message), sanitize_error_text(external_task_id, limit=256),
            now, now, started_at, completed_at, duration_ms, request_hash, request_hash_version,
            sanitize_error_text(error_category, limit=128), sanitize_error_text(human_hint),
            int(bool(retryable)), sanitize_error_text(gateway_stage, limit=128), effective_stage,
            int(payload_schema_version), int(priority), scheduled_at, next_retry_at,
            int(attempt_count), int(max_attempts), claim_token, claim_expires_at,
            sanitize_error_text(worker_kind, limit=128),
            sanitize_error_text(provider_status, limit=128), cancel_requested_at, int(version),
        ),
    )
    return _job_from_connection(conn, identifier) or {}


def create_job(**kwargs: Any) -> dict[str, Any]:
    """Compatibility entrypoint for synchronous flows; failures are never swallowed."""
    with closing(db_connect()) as conn:
        return insert_job(conn, **kwargs)


def get_job(job_id: str) -> dict[str, Any] | None:
    with closing(db_connect()) as conn:
        return _job_from_connection(conn, job_id)


def get_job_by_external_task_id(external_task_id: str, *, kind: str | None = None) -> dict[str, Any] | None:
    if not external_task_id:
        return None
    conditions = ["external_task_id = ?"]
    params: list[Any] = [external_task_id]
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    with closing(db_connect()) as conn:
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE {' AND '.join(conditions)} "
            "ORDER BY created_at DESC LIMIT 1",
            params,
        ).fetchone()
    return dict(row) if row is not None else None


def find_recent_job_by_request_hash(
    *,
    kind: str,
    request_hash: str | None,
    request_hash_version: int | None,
    statuses: Iterable[str],
    created_after: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    if not request_hash or request_hash_version is None:
        return None
    status_values = [str(status) for status in statuses if status]
    if not status_values:
        return None
    placeholders = ",".join("?" for _ in status_values)
    conditions = [
        "kind = ?", "request_hash = ?", "request_hash_version = ?",
        f"status IN ({placeholders})",
    ]
    params: list[Any] = [kind, request_hash, int(request_hash_version), *status_values]
    if created_after:
        conditions.append("created_at >= ?")
        params.append(created_after)
    sql = (
        f"SELECT {_JOB_COLUMNS} FROM jobs WHERE {' AND '.join(conditions)} "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if conn is not None:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None
    with closing(db_connect()) as connection:
        row = connection.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def list_jobs(
    *, kind: str | None = None, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    conditions: list[str] = []
    params: list[Any] = []
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])
    with closing(db_connect()) as conn:
        rows = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def transition_job(
    job_id: str,
    *,
    expected_version: int,
    status: str,
    stage: str | None = None,
    event_type: str = "status_changed",
    **fields: Any,
) -> dict[str, Any]:
    from .job_events import append_job_event

    with db_transaction(immediate=True) as conn:
        existing = _job_from_connection(conn, job_id)
        if existing is None:
            raise JobNotFoundError(job_id)
        if int(existing["version"]) != int(expected_version):
            raise StaleJobVersionError(
                f"job {job_id} version is {existing['version']}, expected {expected_version}"
            )
        current_status = str(existing["status"])
        if status not in ALLOWED_STATUS_TRANSITIONS.get(current_status, set()):
            raise InvalidJobTransitionError(f"illegal job transition: {current_status} -> {status}")
        effective_stage = stage
        if effective_stage is None:
            effective_stage = "finalize" if status in TERMINAL_JOB_STATUSES else str(existing["stage"])
        if effective_stage not in VALID_JOB_STAGES:
            raise InvalidJobTransitionError(f"invalid job stage: {effective_stage}")
        current_stage = str(existing["stage"])
        if effective_stage not in JOB_STAGES_BY_KIND.get(str(existing["kind"]), set()):
            raise InvalidJobTransitionError(
                f"stage {effective_stage} is not valid for {existing['kind']} jobs"
            )
        if effective_stage not in ALLOWED_STAGE_TRANSITIONS.get(current_stage, set()):
            raise InvalidJobTransitionError(
                f"illegal job stage transition: {current_stage} -> {effective_stage}"
            )

        allowed_fields = {
            "provider", "model", "output_json", "error_code", "error_message",
            "external_task_id", "started_at", "completed_at", "duration_ms",
            "error_category", "human_hint", "retryable", "gateway_stage", "scheduled_at",
            "next_retry_at", "attempt_count", "max_attempts", "claim_token",
            "claim_expires_at", "worker_kind", "provider_status", "cancel_requested_at",
        }
        updates: dict[str, Any] = {
            key: value for key, value in fields.items() if key in allowed_fields and value is not None
        }
        if "output_json" in updates:
            updates["output_json"] = sanitize_json_text(updates["output_json"])
        for key in (
            "provider", "model", "error_code", "error_message", "external_task_id",
            "error_category", "human_hint", "gateway_stage", "worker_kind", "provider_status",
        ):
            if key in updates:
                updates[key] = sanitize_error_text(updates[key], limit=1000)
        updates.update({"status": status, "stage": effective_stage, "updated_at": now_iso()})
        assignments = ",".join(f"{name}=?" for name in updates)
        params = [*updates.values(), job_id, int(expected_version)]
        cursor = conn.execute(
            f"UPDATE jobs SET {assignments},version=version+1 WHERE id=? AND version=?", params
        )
        if cursor.rowcount != 1:
            raise StaleJobVersionError(f"job {job_id} was updated concurrently")
        append_job_event(
            job_id,
            event_type,
            {
                "version": int(expected_version) + 1,
                "error_code": updates.get("error_code"),
                "provider_status": updates.get("provider_status"),
            },
            from_status=current_status,
            to_status=status,
            stage=effective_stage,
            conn=conn,
        )
        return _job_from_connection(conn, job_id) or {}


def update_job_status(job_id: str, *, status: str, expected_version: int | None = None, **fields: Any) -> dict[str, Any] | None:
    existing = get_job(job_id)
    if existing is None:
        return None
    version = int(existing["version"]) if expected_version is None else int(expected_version)
    try:
        return transition_job(job_id, expected_version=version, status=status, **fields)
    except JobNotFoundError:
        return None


def fail_job(job_id: str, error_code: str, error_message: str) -> dict[str, Any] | None:
    return update_job_status(job_id, status="failed", error_code=error_code, error_message=error_message)

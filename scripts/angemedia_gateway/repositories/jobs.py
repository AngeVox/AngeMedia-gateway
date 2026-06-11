"""Jobs 相关 DB helper。"""
from __future__ import annotations

from contextlib import closing
from collections.abc import Iterable
from typing import Any

from ..db.connection import db_connect
from ..helpers import now_iso


_JOB_COLUMNS = (
    "id,kind,status,provider,model,prompt,input_json,output_json,"
    "error_code,error_message,external_task_id,"
    "created_at,updated_at,started_at,completed_at,duration_ms,"
    "request_hash,request_hash_version,"
    "error_category,human_hint,retryable,gateway_stage"
)


def create_job(
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
) -> dict[str, Any]:
    """创建 job，返回完整 job dict。"""
    from uuid import uuid4
    job_id = uuid4().hex
    now = now_iso()
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO jobs("
            "id,kind,status,provider,model,prompt,input_json,output_json,"
            "error_code,error_message,external_task_id,"
            "created_at,updated_at,started_at,completed_at,duration_ms,"
            "request_hash,request_hash_version"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, kind, status, provider, model, prompt,
                input_json, output_json, error_code, error_message,
                external_task_id, now, now, started_at, completed_at,
                duration_ms, request_hash, request_hash_version,
            ),
        )
    return get_job(job_id) or {}


def get_job(job_id: str) -> dict[str, Any] | None:
    """按 ID 查询单个 job，不存在返回 None。"""
    with closing(db_connect()) as conn:
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_job_by_external_task_id(external_task_id: str, *, kind: str | None = None) -> dict[str, Any] | None:
    """按 external_task_id 查询最新 job，可选 kind 限定。"""
    if not external_task_id:
        return None
    if kind:
        sql = f"SELECT {_JOB_COLUMNS} FROM jobs WHERE external_task_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1"
        params = (external_task_id, kind)
    else:
        sql = f"SELECT {_JOB_COLUMNS} FROM jobs WHERE external_task_id = ? ORDER BY created_at DESC LIMIT 1"
        params = (external_task_id,)
    with closing(db_connect()) as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return dict(row)


def find_recent_job_by_request_hash(
    *,
    kind: str,
    request_hash: str | None,
    request_hash_version: int | None,
    statuses: Iterable[str],
    created_after: str | None = None,
) -> dict[str, Any] | None:
    """Find newest matching in-flight job for request-driven admission."""
    if not request_hash or request_hash_version is None:
        return None
    status_values = [str(status) for status in statuses if status]
    if not status_values:
        return None
    placeholders = ",".join("?" for _ in status_values)
    conditions = [
        "kind = ?",
        "request_hash = ?",
        "request_hash_version = ?",
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
    with closing(db_connect()) as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return dict(row)


def list_jobs(
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """按 created_at DESC 列出 job，支持 kind/status 过滤。"""
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
    sql = f"SELECT {_JOB_COLUMNS} FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with closing(db_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def update_job_status(
    job_id: str,
    *,
    status: str,
    provider: str | None = None,
    model: str | None = None,
    output_json: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    external_task_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    error_category: str | None = None,
    human_hint: str | None = None,
    retryable: int | None = None,
    gateway_stage: str | None = None,
) -> dict[str, Any] | None:
    """更新 job 状态及相关字段，自动刷新 updated_at。"""
    existing = get_job(job_id)
    if existing is None:
        return None
    now = now_iso()
    new_provider = provider if provider is not None else existing.get("provider")
    new_model = model if model is not None else existing.get("model")
    new_output_json = output_json if output_json is not None else existing.get("output_json")
    new_error_code = error_code if error_code is not None else existing.get("error_code")
    new_error_message = error_message if error_message is not None else existing.get("error_message")
    new_external_task_id = external_task_id if external_task_id is not None else existing.get("external_task_id")
    new_started_at = started_at if started_at is not None else existing.get("started_at")
    new_completed_at = completed_at if completed_at is not None else existing.get("completed_at")
    new_duration_ms = duration_ms if duration_ms is not None else existing.get("duration_ms")
    new_error_category = error_category if error_category is not None else existing.get("error_category")
    new_human_hint = human_hint if human_hint is not None else existing.get("human_hint")
    new_retryable = retryable if retryable is not None else (existing.get("retryable") or 0)
    new_gateway_stage = gateway_stage if gateway_stage is not None else existing.get("gateway_stage")
    with closing(db_connect()) as conn:
        conn.execute(
            "UPDATE jobs SET status=?,provider=?,model=?,output_json=?,"
            "error_code=?,error_message=?,external_task_id=?,"
            "started_at=?,completed_at=?,duration_ms=?,"
            "error_category=?,human_hint=?,retryable=?,gateway_stage=?,"
            "updated_at=? "
            "WHERE id=?",
            (
                status, new_provider, new_model, new_output_json,
                new_error_code, new_error_message, new_external_task_id,
                new_started_at, new_completed_at, new_duration_ms,
                new_error_category, new_human_hint, new_retryable, new_gateway_stage,
                now,
                job_id,
            ),
        )
    return get_job(job_id)


def fail_job(job_id: str, error_code: str, error_message: str) -> dict[str, Any] | None:
    """标记 job 为 failed。"""
    return update_job_status(job_id, status="failed", error_code=error_code, error_message=error_message)

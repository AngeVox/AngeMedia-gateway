"""Admin Jobs task-center query routes."""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query

from ..runtime import require_admin_auth
from ..security import redact_secret_text, validate_task_id
from ..job_sanitizer import sanitize_error_text, sanitize_job_value
from ..repositories.assets import list_assets
from ..repositories.generations import get_generation_by_job_id
from ..repositories.job_attempts import list_job_attempts
from ..repositories.job_dispatches import list_job_dispatches
from ..repositories.job_events import list_job_events
from ..repositories.jobs import count_jobs, get_job, list_jobs

router = APIRouter()

VALID_KINDS = {"image", "video"}
VALID_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}
VALID_SORTS = {"created_at_desc", "created_at_asc", "updated_at_desc", "updated_at_asc"}
SUMMARY_SCALAR_KEYS = {
    "provider", "model", "request_model", "response_format", "size", "quality",
    "style", "mode", "input_mode", "operation", "provider_status", "task_id",
    "history_id", "image_count", "asset_count", "has_url", "has_b64_json",
    "has_asset", "localized", "duration_ms",
}
SENSITIVE_SUMMARY_KEYS = {
    "api_key", "authorization", "token", "access_token", "secret", "password",
    "raw", "raw_body", "raw_response", "provider_body", "request_hash",
    "signed_url", "local_path", "filesystem_path", "bytes", "b64_json",
}


def _normalize_retryable(value):
    """Normalize retryable from DB int (0/1) to API bool."""
    if value is None:
        return None
    return bool(value)


LIST_COLUMNS = (
    "id,kind,status,provider,model,prompt,"
    "created_at,updated_at,started_at,completed_at,duration_ms,"
    "external_task_id,error_code,error_message,"
    "error_category,human_hint,retryable,gateway_stage"
)


def _validate_list_params(
    kind: Optional[str],
    status: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    limit: int,
    offset: int,
    sort: str,
) -> None:
    if kind is not None and kind not in VALID_KINDS:
        _bad_filter("kind", f"无效的 kind 参数，允许值：{', '.join(sorted(VALID_KINDS))}")
    if status is not None and status not in VALID_STATUSES:
        _bad_filter("status", f"无效的 status 参数，允许值：{', '.join(sorted(VALID_STATUSES))}")
    if provider is not None and len(provider) > 256:
        _bad_filter("provider", "provider 不能超过 256 个字符")
    if model is not None and len(model) > 256:
        _bad_filter("model", "model 不能超过 256 个字符")
    if limit < 1 or limit > 100:
        _bad_filter("limit", "limit 必须在 1-100 之间")
    if offset < 0:
        _bad_filter("offset", "offset 不能为负数")
    if sort not in VALID_SORTS:
        _bad_filter("sort", f"无效的 sort 参数，允许值：{', '.join(sorted(VALID_SORTS))}")


def _bad_filter(field: str, message: str) -> None:
    raise HTTPException(
        status_code=400,
        detail={"message": message, "code": "invalid_jobs_filter", "field": field},
    )


def _job_list_item(job: dict[str, Any]) -> dict[str, Any]:
    """从 job dict 中提取列表所需字段（不含 input_json/output_json），脱敏 error_message。"""
    item = {col: job.get(col) for col in LIST_COLUMNS.split(",")}
    if item.get("error_message"):
        item["error_message"] = redact_secret_text(str(item["error_message"]))
    item["retryable"] = _normalize_retryable(item.get("retryable"))
    item["provider_status"] = _provider_status(job)
    return item


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {"summary": sanitize_error_text(str(value), limit=300)}
    sanitized = sanitize_job_value(parsed)
    return sanitized if isinstance(sanitized, dict) else {"summary": sanitized}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in SENSITIVE_SUMMARY_KEYS)


def _controlled_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    path = parsed.path if parsed.scheme or parsed.netloc else text
    if parsed.query or parsed.fragment:
        return ""
    return path if path.startswith(("/generated/", "/uploads/")) else ""


def _safe_task_id(value: Any) -> str:
    try:
        return validate_task_id(str(value or ""))
    except ValueError:
        return ""


def _summary_from_mapping(value: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, item in value.items():
        if _is_sensitive_key(key):
            continue
        if key in {"url", "asset_url", "result_url"}:
            path = _controlled_path(item)
            if path:
                summary[key] = path
            continue
        if key == "task_id":
            safe = _safe_task_id(item)
            if safe:
                summary[key] = safe
            continue
        if key in SUMMARY_SCALAR_KEYS and isinstance(item, (str, int, float, bool)) or item is None:
            summary[key] = item
    return summary


def _input_summary(raw: Any) -> dict[str, Any]:
    payload = _json_object(raw)
    request = payload.get("request") if isinstance(payload.get("request"), dict) else payload
    summary = _summary_from_mapping(request)
    route = payload.get("route")
    if isinstance(route, dict):
        for key in ("provider", "model", "mode", "custom_provider_id"):
            value = route.get(key)
            if value:
                summary[f"route_{key}"] = sanitize_error_text(str(value), limit=160)
    references = 0
    for key in ("image", "images"):
        value = request.get(key) if isinstance(request, dict) else None
        if isinstance(value, list):
            references += len(value)
        elif value:
            references += 1
    if references:
        summary["reference_count"] = references
    return summary


def _output_summary(raw: Any) -> dict[str, Any]:
    payload = _json_object(raw)
    summary = _summary_from_mapping(payload)
    data = payload.get("data")
    if isinstance(data, list):
        summary["item_count"] = len(data)
        urls = [
            path for path in (_controlled_path(item.get("url")) for item in data if isinstance(item, dict))
            if path
        ]
        if urls:
            summary["url"] = urls[0]
    return summary


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "event_type": sanitize_error_text(event.get("event_type"), limit=128),
        "from_status": event.get("from_status"),
        "to_status": event.get("to_status"),
        "stage": sanitize_error_text(event.get("stage"), limit=128),
        "payload": _json_object(event.get("payload_json")),
        "created_at": event.get("created_at"),
    }


def _safe_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": attempt.get("id"),
        "attempt_number": attempt.get("attempt_number"),
        "stage": sanitize_error_text(attempt.get("stage"), limit=128),
        "worker_kind": sanitize_error_text(attempt.get("worker_kind"), limit=128),
        "status": attempt.get("status"),
        "started_at": attempt.get("started_at"),
        "completed_at": attempt.get("completed_at"),
        "retry_at": attempt.get("retry_at"),
        "error_code": sanitize_error_text(attempt.get("error_code"), limit=128),
        "error_message": sanitize_error_text(attempt.get("error_message")),
        "detail": _json_object(attempt.get("detail_json")),
    }


def _safe_asset(asset: dict[str, Any]) -> dict[str, Any] | None:
    url_path = _controlled_path(asset.get("url_path"))
    if not url_path:
        return None
    return {
        "id": asset.get("id"),
        "filename": sanitize_error_text(asset.get("filename"), limit=256),
        "media_type": asset.get("media_type"),
        "source": asset.get("source"),
        "url_path": url_path,
        "size": asset.get("size"),
        "provider": sanitize_error_text(asset.get("provider"), limit=256),
        "model": sanitize_error_text(asset.get("model"), limit=256),
        "duration_ms": asset.get("duration_ms"),
        "created_at": asset.get("created_at"),
    }


def _safe_generation(job_id: str) -> dict[str, Any] | None:
    generation = get_generation_by_job_id(job_id)
    if generation is None:
        return None
    result_url = _controlled_path(generation.get("result_url"))
    task_id = _safe_task_id(generation.get("task_id"))
    return {
        "id": generation.get("id"),
        "media_type": generation.get("media_type"),
        "status": generation.get("status"),
        "result_url": result_url or None,
        "task_id": task_id or None,
        "provider": sanitize_error_text(generation.get("provider"), limit=256),
        "request_model": sanitize_error_text(generation.get("request_model"), limit=256),
        "input_mode": sanitize_error_text(generation.get("input_mode"), limit=128),
        "duration_ms": generation.get("duration_ms"),
        "started_at": generation.get("started_at"),
        "completed_at": generation.get("completed_at"),
        "created_at": generation.get("created_at"),
        "updated_at": generation.get("updated_at"),
    }


def _dispatch_summary(job_id: str) -> dict[str, Any]:
    dispatches = list_job_dispatches(job_id, limit=100)
    counts: dict[str, int] = {}
    for item in dispatches:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    latest = dispatches[-1] if dispatches else None
    return {
        "total": len(dispatches),
        "by_status": counts,
        "latest": None if latest is None else {
            "id": latest.get("id"),
            "status": latest.get("status"),
            "available_at": latest.get("available_at"),
            "published_at": latest.get("published_at"),
            "attempt_count": latest.get("attempt_count"),
            "last_error": sanitize_error_text(latest.get("last_error")),
        },
    }


def _job_detail(job: dict[str, Any]) -> dict[str, Any]:
    assets = [
        safe for safe in (_safe_asset(item) for item in list_assets(job_id=str(job["id"]), limit=100, offset=0))
        if safe is not None
    ]
    return {
        "job_id": job.get("id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "prompt_summary": sanitize_error_text(job.get("prompt"), limit=400),
        "provider": sanitize_error_text(job.get("provider"), limit=256),
        "model": sanitize_error_text(job.get("model"), limit=256),
        "provider_status": _provider_status(job),
        "external_task_id": _safe_task_id(job.get("external_task_id")) or None,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "duration_ms": job.get("duration_ms"),
        "error_code": sanitize_error_text(job.get("error_code"), limit=128),
        "error_message": sanitize_error_text(job.get("error_message")),
        "error_category": sanitize_error_text(job.get("error_category"), limit=128),
        "human_hint": sanitize_error_text(job.get("human_hint")),
        "retryable": _normalize_retryable(job.get("retryable")),
        "gateway_stage": sanitize_error_text(job.get("gateway_stage"), limit=128),
        "cancel_requested_at": job.get("cancel_requested_at"),
        "input_summary": _input_summary(job.get("input_json")),
        "output_summary": _output_summary(job.get("output_json")),
        "assets": assets,
        "generation": _safe_generation(str(job["id"])),
        "events": [_safe_event(item) for item in list_job_events(str(job["id"]), limit=100)],
        "attempts": [_safe_attempt(item) for item in list_job_attempts(str(job["id"]))],
        "dispatch_summary": _dispatch_summary(str(job["id"])),
        "controls": {
            "cancel": {"enabled": False, "reason": "cancel_not_enabled"},
            "retry": {"enabled": False, "reason": "retry_not_enabled"},
        },
    }


def _provider_status(job: dict[str, Any]) -> str | None:
    if job.get("provider_status"):
        return str(job["provider_status"])[:64]
    raw = job.get("output_json")
    if raw:
        try:
            payload = json.loads(str(raw))
            if isinstance(payload, dict):
                status = payload.get("provider_status") or payload.get("status")
                if status:
                    return str(status)[:64]
        except (TypeError, ValueError):
            pass
    if job.get("kind") != "video":
        return None
    status = str(job.get("status") or "")
    return "completed" if status == "succeeded" else ("submitted" if status in {"queued", "running"} else status)


@router.get("/v1/jobs", dependencies=[Depends(require_admin_auth)])
@router.get("/v1/admin/jobs", dependencies=[Depends(require_admin_auth)])
async def list_jobs_endpoint(
    kind: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: str = Query("created_at_desc"),
) -> dict[str, Any]:
    """查询 job 列表。"""
    _validate_list_params(kind, status, provider, model, limit, offset, sort)
    jobs = list_jobs(
        kind=kind, status=status, provider=provider, model=model,
        limit=limit, offset=offset, sort=sort,
    )
    return {
        "object": "list",
        "data": [_job_list_item(j) for j in jobs],
        "limit": limit,
        "offset": offset,
        "total": count_jobs(kind=kind, status=status, provider=provider, model=model),
        "sort": sort,
    }


@router.get("/v1/jobs/{job_id}", dependencies=[Depends(require_admin_auth)])
@router.get("/v1/admin/jobs/{job_id}", dependencies=[Depends(require_admin_auth)])
async def get_job_endpoint(job_id: str) -> dict[str, Any]:
    """查询单个 job 详情。"""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    return {"data": _job_detail(job)}

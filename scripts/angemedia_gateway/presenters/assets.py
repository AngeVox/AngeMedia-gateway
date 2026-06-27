"""Safe asset summaries for Admin/Studio APIs."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..job_sanitizer import sanitize_error_text
from ..repositories.generations import get_generation_by_job_id
from ..repositories.jobs import get_job
from ..security import validate_task_id


def controlled_asset_path(value: Any) -> str | None:
    """Return a UI-safe generated/upload path, or None for remote/local/signed values."""
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    path = parsed.path if parsed.scheme or parsed.netloc else text
    if parsed.query or parsed.fragment:
        return None
    if path.startswith(("/generated/", "/uploads/")):
        return path
    return None


def safe_asset_summary(asset: dict[str, Any]) -> dict[str, Any]:
    """Whitelist an asset row and attach safe job/generation summaries."""
    job_id = str(asset.get("job_id") or "").strip() or None
    return {
        "id": sanitize_error_text(asset.get("id"), limit=128),
        "filename": sanitize_error_text(asset.get("filename"), limit=256),
        "url_path": controlled_asset_path(asset.get("url_path")),
        "media_type": sanitize_error_text(asset.get("media_type"), limit=64),
        "source": sanitize_error_text(asset.get("source"), limit=64),
        "size": asset.get("size"),
        "prompt": sanitize_error_text(asset.get("prompt"), limit=600),
        "model": sanitize_error_text(asset.get("model"), limit=256),
        "provider": sanitize_error_text(asset.get("provider"), limit=256),
        "duration_ms": asset.get("duration_ms"),
        "created_at": asset.get("created_at"),
        "job_id": job_id,
        "job": safe_job_link(job_id) if job_id else None,
        "generation": safe_generation_link(job_id) if job_id else None,
    }


def safe_job_link(job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    job = get_job(job_id)
    if job is None:
        return None
    return {
        "job_id": job.get("id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "stage": sanitize_error_text(job.get("stage"), limit=128),
        "provider": sanitize_error_text(job.get("provider"), limit=256),
        "model": sanitize_error_text(job.get("model"), limit=256),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "duration_ms": job.get("duration_ms"),
    }


def safe_generation_link(job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    generation = get_generation_by_job_id(job_id)
    if generation is None:
        return None
    return {
        "id": generation.get("id"),
        "media_type": sanitize_error_text(generation.get("media_type"), limit=64),
        "status": sanitize_error_text(generation.get("status"), limit=64),
        "result_url": controlled_asset_path(generation.get("result_url")),
        "task_id": _safe_task_id(generation.get("task_id")),
        "provider": sanitize_error_text(generation.get("provider"), limit=256),
        "request_model": sanitize_error_text(generation.get("request_model"), limit=256),
        "input_mode": sanitize_error_text(generation.get("input_mode"), limit=128),
        "duration_ms": generation.get("duration_ms"),
        "started_at": generation.get("started_at"),
        "completed_at": generation.get("completed_at"),
        "created_at": generation.get("created_at"),
        "updated_at": generation.get("updated_at"),
    }


def _safe_task_id(value: Any) -> str | None:
    if not value:
        return None
    try:
        return validate_task_id(str(value))
    except ValueError:
        return None


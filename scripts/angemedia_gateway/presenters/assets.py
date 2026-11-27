"""Safe asset summaries for Admin/Studio APIs."""
from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import urlsplit

from ..job_sanitizer import sanitize_error_text
from ..repositories.generations import get_generation_by_job_id, get_generations_by_job_ids
from ..repositories.image_layers import layer_asset_group
from ..repositories.jobs import get_job, get_jobs_by_ids
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


def safe_asset_summary(asset: dict[str, Any], *, job_rows=None, generation_rows=None) -> dict[str, Any]:
    """Whitelist an asset row and attach safe job/generation summaries."""
    job_id = str(asset.get("job_id") or "").strip() or None
    group = layer_asset_group(asset)
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
        **({"layer_group": group} if group else {}),
        "job": _safe_job(job_rows.get(job_id)) if job_rows is not None else safe_job_link(job_id),
        "generation": _safe_generation(generation_rows.get(job_id)) if generation_rows is not None else safe_generation_link(job_id),
    }


def safe_asset_summaries(assets: list[dict[str, Any]], *, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """一页关联信息各读一次，仍逐项经过原安全投影，不把原始行传给客户端。"""
    job_ids = sorted({str(asset['job_id']).strip() for asset in assets if asset.get('job_id')})
    jobs = get_jobs_by_ids(job_ids, conn=conn)
    generations = get_generations_by_job_ids(job_ids, conn=conn)
    return [safe_asset_summary(asset, job_rows=jobs, generation_rows=generations) for asset in assets]


def safe_job_link(job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    return _safe_job(get_job(job_id))


def _safe_job(job) -> dict[str, Any] | None:
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
    return _safe_generation(get_generation_by_job_id(job_id))


def _safe_generation(generation) -> dict[str, Any] | None:
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


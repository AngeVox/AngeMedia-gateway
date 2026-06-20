"""Video generation orchestration."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..helpers import now_iso, safe_json
from ..media import localize_video_result
from ..repositories.generations import record_generation
from ..repositories.jobs import get_job_by_external_task_id
from ..repositories.settings import builtin_provider_enabled
from ..repositories.video_tasks import upsert_video_task
from ..reference_images import UnsafeImageReference, validate_gateway_image_reference
from ..request_hash_builders import build_video_request_hash_payload
from ..schemas import VideoRequest
from ..security import redact_secret_text, validate_task_id
from .generation_assets import save_generated_asset
from .job_lifecycle import JobLifecycle
from .request_dedupe import VIDEO_ADMISSION_STATUSES, duplicate_response_if_in_flight, request_hash_fields


class VideoProviderDisabled(RuntimeError):
    """Video provider is disabled in current runtime config."""


class InvalidVideoReference(ValueError):
    """Video reference input is not a safe gateway-owned image."""


def _validate_reference_sources(req: VideoRequest) -> None:
    references = ([req.image] if req.image else []) + list(req.images or [])
    try:
        for reference in references:
            validate_gateway_image_reference(reference)
    except UnsafeImageReference as error:
        raise InvalidVideoReference("reference image must be an uploaded or generated image asset") from error


async def create_video(
    req: VideoRequest,
    *,
    agnes_video_provider: Any,
    builtin_provider_enabled_func: Callable[[str], bool] = builtin_provider_enabled,
    localize_video_result_func: Callable[..., Any] = localize_video_result,
    record_generation_func: Callable[..., str] = record_generation,
    upsert_video_task_func: Callable[..., None] = upsert_video_task,
    save_generated_asset_func: Callable[..., None] = save_generated_asset,
    job_lifecycle: JobLifecycle | None = None,
) -> dict[str, Any]:
    lifecycle = job_lifecycle or JobLifecycle()
    if not builtin_provider_enabled_func("agnes_video"):
        raise VideoProviderDisabled("Agnes 视频渠道已停用，请在管理后台恢复后再生成")
    _validate_reference_sources(req)

    started_at = now_iso()
    started = time.perf_counter()
    request_hash: str | None = None
    request_hash_version: int | None = None
    if not req.wait_for_completion:
        request_hash, request_hash_version = request_hash_fields(
            build_video_request_hash_payload(req, provider="agnes_video")
        )
        duplicate_response = duplicate_response_if_in_flight(
            kind="video",
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            statuses=VIDEO_ADMISSION_STATUSES,
        )
        if duplicate_response is not None:
            return duplicate_response
    try:
        if req.wait_for_completion:
            result = await agnes_video_provider.generate_video(req)
            result = await localize_video_result_func(result)
            status = str(result.get("status") or "completed")
        else:
            result = await agnes_video_provider.submit_task(req)
            status = str(result.get("status") or "submitted")
    except UnsafeImageReference as error:
        raise InvalidVideoReference("reference image cannot be safely materialized") from error

    duration_ms = int((time.perf_counter() - started) * 1000)
    result["provider"] = "agnes_video"
    result["model"] = req.model
    result["duration_ms"] = duration_ms
    task_id = str(result.get("task_id") or result.get("id") or "")
    if task_id:
        upsert_video_task_func(task_id, req.prompt, req.model, status, result, duration_ms=duration_ms)

    job_id: str | None = None
    if task_id and not req.wait_for_completion:
        job_id = _create_video_job(
            req,
            task_id=task_id,
            request_hash=request_hash,
            request_hash_version=request_hash_version,
            started_at=started_at,
            job_lifecycle=lifecycle,
        )

    record_id = record_generation_func(
        media_type="video",
        prompt=req.prompt,
        enhanced_prompt=None,
        model=req.model,
        status=status,
        result=result,
        task_id=task_id or None,
        provider="agnes_video",
        request_model=req.model,
        input_mode=req.mode or ("image" if req.image or req.images else "text"),
        duration_ms=duration_ms,
        started_at=started_at,
    )
    save_generated_asset_func(
        media_type="video",
        result=result,
        prompt=req.prompt,
        model=req.model,
        provider="agnes_video",
        duration_ms=duration_ms,
    )
    result["history_id"] = record_id
    if job_id:
        result["job_id"] = job_id
    return result


async def get_video(
    task_id: str,
    *,
    agnes_video_provider: Any,
    localize_video_result_func: Callable[..., Any] = localize_video_result,
    upsert_video_task_func: Callable[..., None] = upsert_video_task,
    get_job_by_external_task_id_func: Callable[..., dict[str, Any] | None] = get_job_by_external_task_id,
    save_generated_asset_func: Callable[..., None] = save_generated_asset,
    job_lifecycle: JobLifecycle | None = None,
) -> dict[str, Any]:
    lifecycle = job_lifecycle or JobLifecycle()
    safe_task_id = validate_task_id(task_id)
    result = await agnes_video_provider.poll_task(safe_task_id)
    result = await localize_video_result_func(result)
    upsert_video_task_func(
        safe_task_id,
        str(result.get("prompt") or ""),
        str(result.get("model") or "agnes-video-v2.0"),
        str(result.get("status") or "unknown"),
        result,
        duration_ms=int(result.get("duration_ms") or 0),
    )

    poll_status = str(result.get("status") or "").lower()
    job_id: str | None = None
    try:
        job = get_job_by_external_task_id_func(safe_task_id, kind="video")
    except Exception:
        job = None
        lifecycle.logger.warning("查询 video job 失败: task_id=%s", safe_task_id)
    if job:
        job_id = job["id"]
        if poll_status in {"completed", "succeeded", "done"}:
            output_summary = safe_json({
                "provider": result.get("provider", "agnes_video"),
                "model": result.get("model", ""),
                "task_id": safe_task_id,
                "status": poll_status,
                "has_video_url": bool(result.get("video_url")),
                "localized": result.get("localized") is True,
                "has_local_path": bool(result.get("local_path")),
            })
            lifecycle.mark_succeeded(
                job_id,
                kind="video",
                output_json=output_summary,
                completed_at=now_iso(),
            )
        elif poll_status in {"failed", "error", "cancelled", "canceled"}:
            lifecycle.mark_failed(
                job_id,
                kind="video",
                error_code="video_generation_failed",
                error_message=redact_secret_text(str(result.get("error") or result.get("message") or poll_status))[:500],
                completed_at=now_iso(),
            )

    if poll_status in {"completed", "succeeded", "done"} and result.get("local_path"):
        try:
            save_generated_asset_func(
                media_type="video",
                result=result,
                prompt=str((job or {}).get("prompt") or result.get("prompt") or ""),
                model=str((job or {}).get("model") or result.get("model") or ""),
                provider=str((job or {}).get("provider") or result.get("provider") or "agnes_video"),
                duration_ms=int(result.get("duration_ms") or 0),
                job_id=job_id,
            )
        except Exception:
            lifecycle.logger.warning("poll completed 写入 video asset 失败: task_id=%s", safe_task_id)

    if job_id:
        result["job_id"] = job_id
    return result


def _create_video_job(
    req: VideoRequest,
    *,
    task_id: str,
    request_hash: str | None,
    request_hash_version: int | None,
    started_at: str,
    job_lifecycle: JobLifecycle,
) -> str:
    input_summary: dict[str, Any] = {
        "model": req.model,
        "mode": req.mode,
        "height": req.height,
        "width": req.width,
        "num_frames": req.num_frames,
        "frame_rate": req.frame_rate,
        "wait_for_completion": req.wait_for_completion,
        "has_image": bool(req.image or req.images),
        "image_count": len(req.images) if req.images else (1 if req.image else 0),
    }
    return job_lifecycle.create(
        kind="video",
        status="running",
        provider="agnes_video",
        model=req.model,
        prompt=req.prompt,
        external_task_id=task_id,
        input_json=safe_json(input_summary),
        started_at=started_at,
        request_hash=request_hash,
        request_hash_version=request_hash_version,
    )

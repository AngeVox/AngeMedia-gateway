"""Media generation business orchestration."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from .. import config as C
from ..media import localize_image_result, localize_video_result, maybe_to_b64
from ..providers.base import BackendUnavailable, RateLimited
from ..providers.custom import generate_custom_openai_image
from ..routing import resolve_chain
from ..schemas import ImageRequest, VideoRequest
from ..security import redact_secret_text, validate_task_id
from ..state import (
    builtin_provider_enabled,
    create_job,
    get_custom_provider,
    get_job_by_external_task_id,
    now_iso,
    record_generation,
    safe_json,
    save_asset,
    update_job_status,
    upsert_video_task,
)
from ..runtime import PROVIDERS, agnes_video

log = logging.getLogger("angemedia-gateway")


class CustomProviderNotFound(RuntimeError):
    """Requested custom provider does not exist."""


class NoImageProviderAvailable(RuntimeError):
    """No enabled image provider can handle the request."""


class ImageProvidersFailed(RuntimeError):
    """All image providers in the resolved chain failed."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("all image providers failed")
        self.errors = errors


class VideoProviderDisabled(RuntimeError):
    """Video provider is disabled in current runtime config."""


def _generated_local_paths(result: dict[str, Any], media_type: str) -> list[str]:
    if media_type == "video":
        local_path = str(result.get("local_path") or "")
        return [local_path] if local_path else []
    data = result.get("data")
    if not isinstance(data, list):
        return []
    paths: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or "")
        if local_path:
            paths.append(local_path)
    return paths


def _generated_output_file(local_path: str) -> Path | None:
    if not local_path:
        return None
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        resolved = path.resolve()
        resolved.relative_to(C.OUTPUT_DIR.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _generated_output_files(result: dict[str, Any], media_type: str) -> list[Path]:
    files: list[Path] = []
    for local_path in _generated_local_paths(result, media_type):
        path = _generated_output_file(local_path)
        if path is not None:
            files.append(path)
    return files


def _save_generated_asset(
    *,
    media_type: str,
    result: dict[str, Any],
    prompt: str,
    model: str | None,
    provider: str | None,
    duration_ms: int,
    job_id: str | None = None,
) -> None:
    for path in _generated_output_files(result, media_type):
        filename = path.name
        save_asset(
            id=uuid.uuid4().hex,
            filename=filename,
            storage_area="output",
            relative_path=filename,
            url_path=f"/generated/{filename}",
            media_type=media_type,
            source="generated",
            size=path.stat().st_size,
            prompt=prompt,
            model=model,
            provider=provider,
            duration_ms=duration_ms,
            job_id=job_id,
        )


def _safe_output_json(result: dict[str, Any]) -> str:
    """构建最小 output_json 摘要，不存储完整 b64 内容。"""
    data = result.get("data")
    has_url = False
    has_b64 = False
    image_count = 0
    if isinstance(data, list):
        image_count = len(data)
        for item in data:
            if isinstance(item, dict):
                if item.get("url"):
                    has_url = True
                if item.get("b64_json"):
                    has_b64 = True
    summary: dict[str, Any] = {
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "history_id": result.get("history_id", ""),
        "image_count": image_count,
        "has_url": has_url,
        "has_b64_json": has_b64,
    }
    if has_url:
        first = data[0] if isinstance(data, list) and data else {}
        if isinstance(first, dict) and first.get("url"):
            summary["url"] = first["url"]
    return safe_json(summary)


class MediaService:
    async def create_image(self, req: ImageRequest) -> dict[str, Any]:
        if req.model and req.model.startswith("custom:"):
            return await self._create_custom_image(req)
        return await self._create_builtin_image(req)

    async def _create_custom_image(self, req: ImageRequest) -> dict[str, Any]:
        provider_id = req.model.split(":", 1)[1] if req.model else ""
        provider = get_custom_provider(provider_id, include_secret=True)
        if provider is None:
            raise CustomProviderNotFound(f"自定义渠道不存在：{provider_id}")

        job_id: str | None = None
        try:
            job_id = create_job(
                kind="image", status="queued", prompt=req.prompt,
                input_json=safe_json({"model": req.model, "size": req.size, "response_format": req.response_format}),
            )["id"]
        except Exception:
            log.warning("创建 image job 失败（不影响生成）")

        started_at = now_iso()
        started = time.perf_counter()
        if job_id:
            try:
                update_job_status(job_id, status="running", provider=f"custom:{provider_id}", model=provider.get("default_model"), started_at=started_at)
            except Exception:
                log.warning("更新 image job running 状态失败: job_id=%s", job_id)
        try:
            result = await generate_custom_openai_image(req, provider)
            if req.response_format == "url":
                result = await localize_image_result(result, f"custom_{provider_id}", provider.get("default_model", "custom"))
            elif req.response_format == "b64_json":
                result = await maybe_to_b64(result, req.response_format)

            duration_ms = int((time.perf_counter() - started) * 1000)
            result["provider"] = f"custom:{provider_id}"
            result["model"] = str(provider.get("default_model") or f"custom:{provider_id}")
            result["duration_ms"] = duration_ms
            record_id = record_generation(
                media_type="image",
                prompt=req.prompt,
                enhanced_prompt=None,
                model=f"custom:{provider_id}",
                status="completed",
                result=result,
                provider=f"custom:{provider_id}",
                request_model=req.model,
                input_mode="custom_provider",
                duration_ms=duration_ms,
                started_at=started_at,
            )
            _save_generated_asset(
                media_type="image",
                result=result,
                prompt=req.prompt,
                model=f"custom:{provider_id}",
                provider=f"custom:{provider_id}",
                duration_ms=duration_ms,
                job_id=job_id,
            )
            result["history_id"] = record_id
            if job_id:
                try:
                    update_job_status(
                        job_id, status="succeeded",
                        output_json=_safe_output_json(result),
                        completed_at=now_iso(), duration_ms=duration_ms,
                    )
                except Exception:
                    log.warning("更新 image job succeeded 状态失败: job_id=%s", job_id)
                result["job_id"] = job_id
            return result
        except Exception:
            if job_id:
                try:
                    update_job_status(
                        job_id, status="failed",
                        error_code="image_generation_failed",
                        error_message="custom provider 调用失败",
                        completed_at=now_iso(),
                    )
                except Exception:
                    log.warning("更新 image job failed 状态失败: job_id=%s", job_id)
            raise

    async def _create_builtin_image(self, req: ImageRequest) -> dict[str, Any]:
        chain = resolve_chain(req.model)
        if not chain:
            raise NoImageProviderAvailable("当前没有可用图片渠道：所选模型已停用或默认链路全部停用")

        job_id: str | None = None
        try:
            job_id = create_job(
                kind="image", status="queued", prompt=req.prompt,
                input_json=safe_json({"model": req.model, "size": req.size, "response_format": req.response_format}),
            )["id"]
        except Exception:
            log.warning("创建 image job 失败（不影响生成）")

        errors: list[str] = []
        for target in chain:
            backend = target.provider
            model = target.model
            provider = PROVIDERS.get(backend)
            if provider is None:
                errors.append(f"{backend}/{model}: unknown provider")
                continue

            started_at = now_iso()
            if job_id:
                try:
                    update_job_status(job_id, status="running", provider=backend, model=model, started_at=started_at)
                except Exception:
                    log.warning("更新 image job running 状态失败: job_id=%s", job_id)
            try:
                started = time.perf_counter()
                result = await provider.generate(req, target)
                if req.response_format == "url":
                    result = await localize_image_result(result, backend, model)
                elif backend != "pollinations":
                    result = await maybe_to_b64(result, req.response_format)

                duration_ms = int((time.perf_counter() - started) * 1000)
                result["provider"] = backend
                result["model"] = model
                result["request_model"] = req.model or ""
                result["duration_ms"] = duration_ms
                record_id = record_generation(
                    media_type="image",
                    prompt=req.prompt,
                    enhanced_prompt=None,
                    model=model,
                    status="completed",
                    result=result,
                    provider=backend,
                    request_model=req.model or "",
                    input_mode="default_chain" if not req.model else "explicit_model",
                    duration_ms=duration_ms,
                    started_at=started_at,
                )
                _save_generated_asset(
                    media_type="image",
                    result=result,
                    prompt=req.prompt,
                    model=model,
                    provider=backend,
                    duration_ms=duration_ms,
                    job_id=job_id,
                )
                result["history_id"] = record_id
                if job_id:
                    try:
                        update_job_status(
                            job_id, status="succeeded",
                            output_json=_safe_output_json(result),
                            completed_at=now_iso(), duration_ms=duration_ms,
                        )
                    except Exception:
                        log.warning("更新 image job succeeded 状态失败: job_id=%s", job_id)
                    result["job_id"] = job_id
                log.info("%s succeeded: model=%s", backend, model)
                return result
            except RateLimited as exc:
                message = f"{backend}/{model}: {exc}"
                log.warning(message)
                errors.append(message)
                continue
            except BackendUnavailable as exc:
                message = f"{backend}/{model}: {exc}"
                log.warning(message)
                errors.append(message)
                continue
            except Exception as exc:
                message = f"{backend}/{model}: unexpected {type(exc).__name__}: {exc}"
                log.exception(message)
                errors.append(message)
                continue

        if job_id:
            try:
                update_job_status(
                    job_id, status="failed",
                    error_code="all_providers_failed",
                    error_message=redact_secret_text("; ".join(errors))[:500],
                    completed_at=now_iso(),
                )
            except Exception:
                log.warning("更新 image job failed 状态失败: job_id=%s", job_id)
        raise ImageProvidersFailed(errors)

    async def create_video(self, req: VideoRequest) -> dict[str, Any]:
        if not builtin_provider_enabled("agnes_video"):
            raise VideoProviderDisabled("Agnes 视频渠道已停用，请在管理后台恢复后再生成")

        started_at = now_iso()
        started = time.perf_counter()
        if req.wait_for_completion:
            result = await agnes_video.generate_video(req)
            result = await localize_video_result(result)
            status = str(result.get("status") or "completed")
        else:
            result = await agnes_video.submit_task(req)
            status = str(result.get("status") or "submitted")

        duration_ms = int((time.perf_counter() - started) * 1000)
        result["provider"] = "agnes_video"
        result["model"] = req.model
        result["duration_ms"] = duration_ms
        task_id = str(result.get("task_id") or result.get("id") or "")
        if task_id:
            upsert_video_task(task_id, req.prompt, req.model, status, result, duration_ms=duration_ms)

        # ── job 记录（旁路，仅异步提交路径）───────────────
        job_id: str | None = None
        if task_id and not req.wait_for_completion:
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
            try:
                job_id = create_job(
                    kind="video",
                    status="running",
                    provider="agnes_video",
                    model=req.model,
                    prompt=req.prompt,
                    external_task_id=task_id,
                    input_json=safe_json(input_summary),
                    started_at=started_at,
                )["id"]
            except Exception:
                log.warning("创建 video job 失败（不影响生成）")
        # ── end job ─────────────────────────────────────

        record_id = record_generation(
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
        _save_generated_asset(
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

    async def get_video(self, task_id: str) -> dict[str, Any]:
        safe_task_id = validate_task_id(task_id)
        result = await agnes_video.poll_task(safe_task_id)
        result = await localize_video_result(result)
        upsert_video_task(
            safe_task_id,
            str(result.get("prompt") or ""),
            str(result.get("model") or "agnes-video-v2.0"),
            str(result.get("status") or "unknown"),
            result,
            duration_ms=int(result.get("duration_ms") or 0),
        )

        # ── poll 后更新 job（旁路，不阻断主流程）────────
        poll_status = str(result.get("status") or "").lower()
        job_id: str | None = None
        try:
            job = get_job_by_external_task_id(safe_task_id, kind="video")
        except Exception:
            job = None
            log.warning("查询 video job 失败: task_id=%s", safe_task_id)
        if job:
            job_id = job["id"]
            if poll_status in {"completed", "succeeded", "done"}:
                output_summary = safe_json({
                    "provider": result.get("provider", "agnes_video"),
                    "model": result.get("model", ""),
                    "task_id": safe_task_id,
                    "status": poll_status,
                    "has_video_url": bool(result.get("video_url")),
                    "video_url": result.get("video_url", ""),
                })
                try:
                    update_job_status(
                        job_id, status="succeeded",
                        output_json=output_summary,
                        completed_at=now_iso(),
                    )
                except Exception:
                    log.warning("更新 video job succeeded 状态失败: job_id=%s", job_id)
            elif poll_status in {"failed", "error", "cancelled", "canceled"}:
                try:
                    update_job_status(
                        job_id, status="failed",
                        error_code="video_generation_failed",
                        error_message=redact_secret_text(str(result.get("error") or result.get("message") or poll_status))[:500],
                        completed_at=now_iso(),
                    )
                except Exception:
                    log.warning("更新 video job failed 状态失败: job_id=%s", job_id)
            # queued/submitted/running/processing → 不更新，保持 running
        # ── end job poll update ─────────────────────────

        if job_id:
            result["job_id"] = job_id
        return result

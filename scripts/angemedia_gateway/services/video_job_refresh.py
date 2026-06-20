"""On-demand Agnes video job polling and local asset import."""
from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from ..error_diagnostics import classify_provider_error
from ..helpers import now_iso, safe_json
from ..media import localize_video_result
from ..repositories.assets import list_assets
from ..repositories.jobs import get_job, update_job_status
from ..repositories.video_tasks import upsert_video_task
from ..runtime import agnes_video
from ..security import redact_secret_text, validate_task_id
from .generation_assets import save_generated_asset

log = logging.getLogger("angemedia-gateway")

ACTIVE_JOB_STATUSES = {"queued", "running"}
COMPLETED_PROVIDER_STATUSES = {"completed", "succeeded", "success", "done"}
FAILED_PROVIDER_STATUSES = {"failed", "error", "cancelled", "canceled"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "canceled"}
DEFAULT_MIN_POLL_INTERVAL_SECONDS = 10.0


class VideoJobRefreshError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stored_provider_status(job: dict[str, Any]) -> str:
    raw = job.get("output_json")
    if raw:
        try:
            import json

            payload = json.loads(str(raw))
            if isinstance(payload, dict):
                value = payload.get("provider_status") or payload.get("status")
                if value:
                    return str(value).lower()[:64]
        except (TypeError, ValueError):
            pass
    status = str(job.get("status") or "unknown").lower()
    if status == "succeeded":
        return "completed"
    return "submitted" if status in ACTIVE_JOB_STATUSES else status


def _output_summary(
    *,
    task_id: str,
    provider_status: str,
    localized: bool,
    asset_count: int,
) -> str:
    return safe_json({
        "task_id": task_id,
        "provider_status": provider_status,
        "localized": localized,
        "has_asset": asset_count > 0,
        "asset_count": asset_count,
    })


def _normalized_poll_result(raw: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Whitelist provider fields so upstream data cannot impersonate local state."""
    result: dict[str, Any] = {
        "task_id": task_id,
        "status": str(raw.get("status") or "unknown")[:64],
    }
    video_url = raw.get("video_url")
    if isinstance(video_url, str) and video_url:
        result["video_url"] = video_url
    if raw.get("duration_ms") is not None:
        try:
            result["duration_ms"] = max(0, int(raw["duration_ms"]))
        except (TypeError, ValueError):
            pass
    for field in ("error", "message"):
        if raw.get(field):
            result[field] = redact_secret_text(str(raw[field]))[:500]
    return result


class VideoJobRefreshService:
    """Poll one eligible video job once; never loops or schedules background work."""

    def __init__(
        self,
        *,
        poll_task_func: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        localize_video_result_func: Callable[..., Awaitable[dict[str, Any]]] = localize_video_result,
        get_job_func: Callable[[str], dict[str, Any] | None] = get_job,
        update_job_status_func: Callable[..., dict[str, Any] | None] = update_job_status,
        upsert_video_task_func: Callable[..., None] = upsert_video_task,
        save_generated_asset_func: Callable[..., None] = save_generated_asset,
        list_assets_func: Callable[..., list[dict[str, Any]]] = list_assets,
        now_func: Callable[[], datetime] = _utc_now,
        min_poll_interval_seconds: float = DEFAULT_MIN_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.poll_task_func = poll_task_func or agnes_video.poll_task
        self.localize_video_result_func = localize_video_result_func
        self.get_job_func = get_job_func
        self.update_job_status_func = update_job_status_func
        self.upsert_video_task_func = upsert_video_task_func
        self.save_generated_asset_func = save_generated_asset_func
        self.list_assets_func = list_assets_func
        self.now_func = now_func
        self.min_poll_interval_seconds = max(0.0, float(min_poll_interval_seconds))

    async def refresh(self, job_id: str) -> dict[str, Any]:
        job = self.get_job_func(job_id)
        if job is None:
            raise VideoJobRefreshError(404, "Job 不存在")
        if job.get("kind") != "video":
            raise VideoJobRefreshError(400, "只有视频 Job 支持状态刷新")
        if job.get("provider") != "agnes_video":
            return self._response(job, refresh_status="unsupported", polled=False)
        if str(job.get("status") or "") in TERMINAL_JOB_STATUSES:
            assets = self.list_assets_func(job_id=job_id, limit=1, offset=0)
            return self._response(
                job,
                refresh_status="terminal",
                polled=False,
                asset_url=self._asset_url(assets),
            )
        if str(job.get("status") or "") not in ACTIVE_JOB_STATUSES:
            return self._response(job, refresh_status="unsupported", polled=False)

        try:
            task_id = validate_task_id(str(job.get("external_task_id") or ""))
        except ValueError as exc:
            raise VideoJobRefreshError(409, "视频 Job 缺少有效的 provider task id") from exc
        retry_after = self._retry_after_seconds(job)
        if retry_after > 0:
            return self._response(
                job,
                refresh_status="throttled",
                polled=False,
                retry_after_seconds=retry_after,
            )

        try:
            raw_result = await self.poll_task_func(task_id)
        except Exception as exc:
            safe_error = redact_secret_text(str(exc))[:500]
            classification = classify_provider_error(safe_error)
            self.update_job_status_func(
                job_id,
                status="running",
                error_code="video_poll_failed",
                error_message=safe_error,
                error_category=classification["error_category"],
                human_hint=classification["human_hint"],
                retryable=1,
                gateway_stage="provider_poll",
            )
            log.warning("video job refresh poll failed: job_id=%s error_type=%s", job_id, type(exc).__name__)
            raise VideoJobRefreshError(502, "视频任务状态刷新失败，请稍后重试") from exc

        result = _normalized_poll_result(raw_result, task_id)

        provider_status = str(result.get("status") or "unknown").lower()[:64]
        if provider_status in COMPLETED_PROVIDER_STATUSES and not result.get("local_path"):
            result = await self.localize_video_result_func(result, force=True)

        self.upsert_video_task_func(
            task_id,
            str(job.get("prompt") or ""),
            str(job.get("model") or "agnes-video-v2.0"),
            provider_status,
            result,
            duration_ms=int(result.get("duration_ms") or job.get("duration_ms") or 0),
        )

        if provider_status in FAILED_PROVIDER_STATUSES:
            return self._mark_failed(job, provider_status, result)
        if provider_status in COMPLETED_PROVIDER_STATUSES:
            return self._import_completed(job, provider_status, result)
        return self._mark_running(job, provider_status)

    def _mark_running(self, job: dict[str, Any], provider_status: str) -> dict[str, Any]:
        updated = self.update_job_status_func(
            job["id"],
            status="running",
            output_json=_output_summary(
                task_id=str(job.get("external_task_id") or ""),
                provider_status=provider_status,
                localized=False,
                asset_count=0,
            ),
            error_code="",
            error_message="",
            error_category="",
            human_hint="",
            retryable=0,
            gateway_stage="provider_poll",
        ) or job
        return self._response(updated, provider_status=provider_status, refresh_status="polled", polled=True)

    def _mark_failed(
        self,
        job: dict[str, Any],
        provider_status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        safe_error = redact_secret_text(
            str(result.get("error") or result.get("message") or provider_status)
        )[:500]
        updated = self.update_job_status_func(
            job["id"],
            status="failed",
            output_json=_output_summary(
                task_id=str(job.get("external_task_id") or ""),
                provider_status=provider_status,
                localized=False,
                asset_count=0,
            ),
            error_code="video_generation_failed",
            error_message=safe_error,
            error_category="provider_failure",
            human_hint="视频服务商返回失败，请检查参数后重试。",
            retryable=0,
            gateway_stage="provider_poll",
            completed_at=now_iso(),
        ) or job
        return self._response(updated, provider_status=provider_status, refresh_status="failed", polled=True)

    def _import_completed(
        self,
        job: dict[str, Any],
        provider_status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        local_path = str(result.get("local_path") or "")
        assets: list[dict[str, Any]] = []
        import_error = str(result.get("localize_error") or "")
        if local_path:
            try:
                self.save_generated_asset_func(
                    media_type="video",
                    result=result,
                    prompt=str(job.get("prompt") or ""),
                    model=str(job.get("model") or ""),
                    provider="agnes_video",
                    duration_ms=int(result.get("duration_ms") or job.get("duration_ms") or 0),
                    job_id=job["id"],
                )
                assets = self.list_assets_func(job_id=job["id"], limit=1, offset=0)
            except Exception as exc:
                import_error = redact_secret_text(str(exc))[:500]
                log.warning("video job asset import failed: job_id=%s error_type=%s", job["id"], type(exc).__name__)

        if not assets:
            safe_error = redact_secret_text(import_error or "视频已完成，但本地下载尚未成功")[:500]
            updated = self.update_job_status_func(
                job["id"],
                status="running",
                output_json=_output_summary(
                    task_id=str(job.get("external_task_id") or ""),
                    provider_status=provider_status,
                    localized=False,
                    asset_count=0,
                ),
                error_code="video_download_pending",
                error_message=safe_error,
                error_category="download_failure",
                human_hint="视频已生成，下载暂未完成；稍后再次刷新状态。",
                retryable=1,
                gateway_stage="download",
            ) or job
            return self._response(
                updated,
                provider_status=provider_status,
                refresh_status="download_pending",
                polled=True,
            )

        updated = self.update_job_status_func(
            job["id"],
            status="succeeded",
            output_json=_output_summary(
                task_id=str(job.get("external_task_id") or ""),
                provider_status=provider_status,
                localized=True,
                asset_count=len(assets),
            ),
            error_code="",
            error_message="",
            error_category="",
            human_hint="",
            retryable=0,
            gateway_stage="asset_import",
            completed_at=now_iso(),
        ) or job
        return self._response(
            updated,
            provider_status=provider_status,
            refresh_status="completed",
            polled=True,
            asset_url=self._asset_url(assets),
        )

    def _retry_after_seconds(self, job: dict[str, Any]) -> int:
        updated_at = _parse_datetime(job.get("updated_at"))
        if updated_at is None or self.min_poll_interval_seconds <= 0:
            return 0
        elapsed = max(0.0, (self.now_func() - updated_at).total_seconds())
        return max(0, math.ceil(self.min_poll_interval_seconds - elapsed))

    @staticmethod
    def _asset_url(assets: list[dict[str, Any]]) -> str | None:
        if not assets:
            return None
        url = str(assets[0].get("url_path") or "")
        return url if url.startswith("/generated/") else None

    @staticmethod
    def _response(
        job: dict[str, Any],
        *,
        refresh_status: str,
        polled: bool,
        provider_status: str | None = None,
        retry_after_seconds: int = 0,
        asset_url: str | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": str(job.get("id") or ""),
            "status": str(job.get("status") or "unknown"),
            "provider_status": provider_status or _stored_provider_status(job),
            "refresh_status": refresh_status,
            "polled": polled,
            "retry_after_seconds": retry_after_seconds,
            "asset_url": asset_url,
        }

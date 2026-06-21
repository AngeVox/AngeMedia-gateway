"""Provider-only video submit and single-poll execution boundaries."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text
from ..repositories.settings import builtin_provider_enabled
from ..schemas import VideoRequest
from ..security import validate_task_id


class VideoProviderDisabled(RuntimeError):
    pass


class InvalidVideoProviderResult(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoSubmitResult:
    task_id: str
    provider_status: str
    duration_ms: int
    started_at: str


@dataclass(frozen=True)
class VideoPollResult:
    task_id: str
    provider_status: str
    video_url: str | None = None
    duration_ms: int = 0
    error_message: str | None = None


class VideoExecutionService:
    """Calls Agnes without creating jobs, publishing work, or persisting responses."""

    def __init__(
        self,
        *,
        provider: Any,
        provider_enabled_func: Callable[[str], bool] = builtin_provider_enabled,
    ) -> None:
        self.provider = provider
        self.provider_enabled_func = provider_enabled_func

    def _require_enabled(self) -> None:
        if not self.provider_enabled_func("agnes_video"):
            raise VideoProviderDisabled("Agnes video provider is disabled")

    async def submit(self, request: VideoRequest) -> VideoSubmitResult:
        self._require_enabled()
        started_at = now_iso()
        started = time.perf_counter()
        raw = await self.provider.submit_task(request)
        task_id = validate_task_id(str(raw.get("task_id") or raw.get("id") or ""))
        status = str(raw.get("status") or "queued").lower()[:64]
        return VideoSubmitResult(
            task_id=task_id,
            provider_status=status,
            duration_ms=int((time.perf_counter() - started) * 1000),
            started_at=started_at,
        )

    async def poll(self, task_id: str) -> VideoPollResult:
        self._require_enabled()
        safe_task_id = validate_task_id(task_id)
        raw = await self.provider.poll_task(safe_task_id)
        status = str(raw.get("status") or "unknown").lower()[:64]
        video_url = raw.get("video_url")
        if not isinstance(video_url, str) or not video_url:
            video_url = None
        duration_ms = 0
        try:
            duration_ms = max(0, int(raw.get("duration_ms") or 0))
        except (TypeError, ValueError):
            pass
        error = raw.get("error") or raw.get("message")
        return VideoPollResult(
            task_id=safe_task_id,
            provider_status=status,
            video_url=video_url,
            duration_ms=duration_ms,
            error_message=sanitize_error_text(str(error)) if error else None,
        )


def build_runtime_video_executor() -> VideoExecutionService:
    from ..runtime import agnes_video

    return VideoExecutionService(provider=agnes_video)

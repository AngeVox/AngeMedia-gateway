"""SSRF-safe, replay-aware video localization boundary."""
from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .. import config as C
from ..media import localize_video_result
from ..security import validate_public_http_url, validate_task_id
from .generation_assets import generated_output_file
from .video_execution import VideoPollResult

_VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv"})
TRUSTED_VIDEO_OUTPUT_HOSTS = frozenset({"platform-outputs.agnes-ai.space"})


@dataclass(frozen=True)
class VideoAssetImportResult:
    result: dict[str, Any]
    duration_ms: int


class VideoAssetImportService:
    def __init__(
        self,
        *,
        localize_video_result_func: Callable[..., Awaitable[dict[str, Any]]] = localize_video_result,
        validate_public_url_func: Callable[[str], str] = validate_public_http_url,
    ) -> None:
        self.localize_video_result_func = localize_video_result_func
        self.validate_public_url_func = validate_public_url_func

    def _existing_result(self, task_id: str, status: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
        for pattern in (f"video-agnes-{digest}.*", f"video_agnes_{digest}.*"):
            for path in C.OUTPUT_DIR.glob(pattern):
                if path.suffix.lower() not in _VIDEO_EXTENSIONS:
                    continue
                safe_path = generated_output_file(str(path))
                if safe_path is not None:
                    return {
                        "task_id": task_id,
                        "status": status,
                        "video_url": f"{C.PUBLIC_BASE_URL}/generated/{safe_path.name}",
                        "local_path": str(safe_path),
                        "localized": True,
                    }
        return None

    async def import_completed(
        self,
        task_id: str,
        poll_result: VideoPollResult,
    ) -> VideoAssetImportResult:
        safe_task_id = validate_task_id(task_id)
        existing = self._existing_result(safe_task_id, poll_result.provider_status)
        if existing is not None:
            return VideoAssetImportResult(existing, poll_result.duration_ms)

        remote_url = str(poll_result.video_url or "").strip()
        parsed = urlparse(remote_url)
        if (
            not remote_url
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("video result URL is not safe for queued import")
        if (urlparse(remote_url).hostname or "").lower() not in TRUSTED_VIDEO_OUTPUT_HOSTS:
            self.validate_public_url_func(remote_url)
        localized = await self.localize_video_result_func(
            {
                "task_id": safe_task_id,
                "status": poll_result.provider_status,
                "video_url": remote_url,
                "duration_ms": poll_result.duration_ms,
            },
            force=True,
            trusted_hosts=TRUSTED_VIDEO_OUTPUT_HOSTS,
        )
        local_path = str(localized.get("local_path") or "")
        safe_file = generated_output_file(local_path)
        local_url = str(localized.get("video_url") or "")
        local_parsed = urlparse(local_url)
        if (
            localized.get("localized") is not True
            or safe_file is None
            or not local_parsed.path.startswith("/generated/")
            or local_parsed.query
            or local_parsed.fragment
        ):
            raise RuntimeError("video result could not be safely localized")
        result = {
            "task_id": safe_task_id,
            "status": poll_result.provider_status,
            "video_url": f"{C.PUBLIC_BASE_URL}/generated/{safe_file.name}",
            "local_path": str(safe_file),
            "localized": True,
            "duration_ms": poll_result.duration_ms,
        }
        return VideoAssetImportResult(result=result, duration_ms=poll_result.duration_ms)

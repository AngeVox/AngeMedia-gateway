"""Explicit no-network fakes for the Docker compose queue smoke gate."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .. import config as C
from ..helpers import now_iso
from ..schemas import ImageRequest, VideoRequest
from .image_execution import ImageExecutionPlan, ImageExecutionResult
from .video_asset_import import VideoAssetImportResult
from .video_execution import VideoPollResult, VideoSubmitResult

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2&\xb9\x00\x00\x00\x00IEND\xaeB`\x82"
)
_SMOKE_MP4_BYTES = b"queue smoke local video fixture\n"


def queue_smoke_enabled() -> bool:
    return os.getenv("QUEUE_SMOKE_FAKE_PROVIDERS", "").strip().lower() in {"1", "true", "yes", "on"}


def smoke_image_plan(req: ImageRequest) -> ImageExecutionPlan:
    model = str(req.model or "queue-smoke-image").strip() or "queue-smoke-image"
    return ImageExecutionPlan(mode="builtin", routes=(("queue_smoke", model),))


class FakeQueueSmokeImageExecutor:
    async def execute(self, req: ImageRequest, plan: ImageExecutionPlan) -> ImageExecutionResult:
        started_at = now_iso()
        started = time.perf_counter()
        C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"queue-smoke-image-{int(time.time() * 1000)}.png"
        path = C.OUTPUT_DIR / filename
        path.write_bytes(_PNG_1X1)
        model = plan.model or req.model or "queue-smoke-image"
        result = {
            "data": [{
                "url": f"{C.PUBLIC_BASE_URL}/generated/{filename}",
                "local_path": str(path),
            }],
            "provider": "queue_smoke",
            "model": model,
            "duration_ms": 1,
        }
        return ImageExecutionResult(
            result=result,
            provider="queue_smoke",
            model=model,
            request_model=req.model or "",
            input_mode="queue_smoke",
            duration_ms=max(1, int((time.perf_counter() - started) * 1000)),
            started_at=started_at,
        )


class FakeQueueSmokeVideoExecutor:
    async def submit(self, request: VideoRequest) -> VideoSubmitResult:
        return VideoSubmitResult(
            task_id=f"queue-smoke-video-{int(time.time() * 1000)}",
            provider_status="queued",
            duration_ms=1,
            started_at=now_iso(),
        )

    async def poll(self, task_id: str) -> VideoPollResult:
        return VideoPollResult(
            task_id=task_id,
            provider_status="completed",
            video_url=None,
            duration_ms=1,
        )


class FakeQueueSmokeVideoImporter:
    async def import_completed(self, task_id: str, poll_result: VideoPollResult) -> VideoAssetImportResult:
        C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{task_id}.mp4"
        path = C.OUTPUT_DIR / filename
        _write_local_video_fixture(path)
        return VideoAssetImportResult(
            result={
                "task_id": task_id,
                "status": "completed",
                "video_url": f"{C.PUBLIC_BASE_URL}/generated/{filename}",
                "local_path": str(path),
                "localized": True,
                "duration_ms": poll_result.duration_ms or 1,
            },
            duration_ms=poll_result.duration_ms or 1,
        )


def _write_local_video_fixture(path: Path) -> None:
    path.write_bytes(_SMOKE_MP4_BYTES)


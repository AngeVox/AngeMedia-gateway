"""Explicit mapping from durable stage names to worker handlers."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

StageHandler = Callable[[Any, dict[str, Any]], dict[str, Any]]


class JobStageRegistry:
    def __init__(self, handlers: Mapping[str, StageHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def get(self, stage: str) -> StageHandler | None:
        return self._handlers.get(stage)


def default_job_stage_registry(*, worker_kind: str = "celery") -> JobStageRegistry:
    from .image_job_worker import ImageJobWorker
    from .layer_image_admission import is_layer_image_job
    from .layer_image_worker import LayerImageWorker
    from .queue_smoke import (
        FakeQueueSmokeImageExecutor,
        FakeQueueSmokeVideoExecutor,
        FakeQueueSmokeVideoImporter,
        queue_smoke_enabled,
    )
    from ..pollinations_video import is_pollinations_video_job
    from .pollinations_video_worker import PollinationsVideoWorker
    from .video_job_worker import VideoJobWorker

    smoke = queue_smoke_enabled()
    if smoke:
        image_worker = ImageJobWorker(
            executor=FakeQueueSmokeImageExecutor(),
            worker_kind=worker_kind,
        )
        video_worker = VideoJobWorker(
            executor=FakeQueueSmokeVideoExecutor(),
            asset_importer=FakeQueueSmokeVideoImporter(),
            worker_kind=worker_kind,
        )
    else:
        image_worker = ImageJobWorker(worker_kind=worker_kind)
        video_worker = VideoJobWorker(worker_kind=worker_kind)
    pollinations_worker = None if smoke else PollinationsVideoWorker(worker_kind=worker_kind)
    layer_worker = LayerImageWorker(worker_kind=worker_kind) if not smoke else None

    def generate_image(message, job):
        if layer_worker is not None and is_layer_image_job(job):
            return layer_worker.handle_generate(message, job)
        return image_worker.handle(message, job)

    def submit_video(message, job):
        if pollinations_worker is not None and is_pollinations_video_job(job):
            return pollinations_worker.handle(message, job)
        return video_worker.handle_submit(message, job)

    def poll_video(message, job):
        if pollinations_worker is not None and is_pollinations_video_job(job):
            return pollinations_worker.handle(message, job)
        return video_worker.handle_poll(message, job)

    def import_assets(message, job):
        if pollinations_worker is not None and is_pollinations_video_job(job):
            return pollinations_worker.handle(message, job)
        if layer_worker is not None and job.get("kind") == "image":
            return layer_worker.handle_import(message, job)
        return video_worker.handle_asset_import(message, job)

    return JobStageRegistry({
        "image_generate": image_worker.handle if smoke else generate_image,
        "image_poll": image_worker.handle_poll,
        "image_import": image_worker.handle_import,
        "video_submit": video_worker.handle_submit if smoke else submit_video,
        "video_poll": video_worker.handle_poll if smoke else poll_video,
        "asset_import": import_assets,
    })

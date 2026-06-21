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


def default_job_stage_registry() -> JobStageRegistry:
    from .image_job_worker import ImageJobWorker

    worker = ImageJobWorker()
    return JobStageRegistry({"image_generate": worker.handle})

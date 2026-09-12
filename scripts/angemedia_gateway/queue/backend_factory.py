"""Queue backend selection without coupling callers to Celery imports."""
from __future__ import annotations

from typing import Any

from .settings import QueueSettings


def create_queue_backend(settings: QueueSettings | None = None) -> Any:
    queue_settings = settings or QueueSettings.from_env()
    if not queue_settings.enabled:
        raise RuntimeError("queue backend is disabled")
    if queue_settings.backend == "local":
        from .local_backend import LocalQueueBackend

        return LocalQueueBackend(settings=queue_settings)
    if queue_settings.backend == "celery":
        from .celery_app import celery_app
        from .celery_backend import CeleryQueueBackend

        return CeleryQueueBackend(app=celery_app, settings=queue_settings)
    raise RuntimeError("unsupported queue backend")

"""Secret-free queue configuration and broker diagnostics."""
from __future__ import annotations

from typing import Any

from .celery_backend import QueueUnavailable
from .settings import QueueSettings


def queue_diagnostics(backend: Any, settings: QueueSettings) -> dict[str, object]:
    result = settings.safe_summary()
    try:
        backend.healthcheck()
    except QueueUnavailable:
        result.update({"healthy": False, "error_code": "queue_broker_unavailable"})
    else:
        result.update({"healthy": True, "error_code": None})
    return result

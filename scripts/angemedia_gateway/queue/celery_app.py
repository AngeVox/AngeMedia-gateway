"""Celery application factory; contains no database or provider access."""
from __future__ import annotations

import os

from celery import Celery

from .settings import QueueSettings


def create_celery_app(settings: QueueSettings | None = None) -> Celery:
    queue_settings = settings or QueueSettings.from_env()
    if not queue_settings.enabled:
        raise RuntimeError("queue runtime is explicitly disabled")
    if any(os.getenv(name, "").strip() for name in ("CELERY_RESULT_BACKEND", "CELERY_BACKEND")):
        raise RuntimeError("Celery result backend must remain disabled")
    app = Celery("angemedia_gateway", broker=queue_settings.broker_url, backend=None)
    app.conf.update(
        result_backend=None,
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_default_queue=queue_settings.task_queue,
        task_default_delivery_mode=2,
        task_acks_late=True,
        task_acks_on_failure_or_timeout=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "visibility_timeout": queue_settings.visibility_timeout_seconds,
        },
        imports=("angemedia_gateway.worker.tasks",),
        enable_utc=True,
        timezone="UTC",
    )
    return app


celery_app = create_celery_app()

"""Thin Celery task wrappers around the worker domain runtime."""
from __future__ import annotations

from typing import Any

from ..queue.celery_app import celery_app
from ..queue.settings import WORKER_TASK_NAME
from ..services.worker_runtime import WorkerRuntime


def _get_runtime() -> WorkerRuntime:
    return WorkerRuntime()


@celery_app.task(
    name=WORKER_TASK_NAME,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_job_stage(message: Any) -> dict[str, Any]:
    return _get_runtime().handle(message)

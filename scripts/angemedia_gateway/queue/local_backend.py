"""In-process queue backend backed by the durable SQLite outbox.

The dispatcher calls this backend synchronously, so no Redis/Celery broker is
required. SQLite remains the source of truth and the existing worker runtime
continues to own stage execution and idempotency.
"""
from __future__ import annotations

import logging
from typing import Any

from .messages import JobStageMessage
from .settings import QueueSettings, WORKER_TASK_NAME

log = logging.getLogger("angemedia-gateway")


class LocalQueueBackend:
    """Synchronous delivery backend for single-process local queue mode."""

    def __init__(self, *, settings: QueueSettings, runtime: Any | None = None) -> None:
        if not settings.enabled or settings.backend != "local":
            raise RuntimeError("local queue backend is not enabled")
        self.settings = settings
        self._runtime = runtime

    def _get_runtime(self) -> Any:
        if self._runtime is None:
            from ..services.job_stage_registry import default_job_stage_registry
            from ..services.worker_runtime import WorkerRuntime

            self._runtime = WorkerRuntime(
                registry=default_job_stage_registry(worker_kind="local"),
                worker_kind="local",
            )
        return self._runtime

    def publish(self, *, topic: str, message: JobStageMessage) -> str:
        if topic != WORKER_TASK_NAME:
            raise ValueError("unapproved local queue task topic")
        if not isinstance(message, JobStageMessage):
            raise TypeError("LocalQueueBackend requires JobStageMessage")
        from ..services.local_worker_failure_recovery import LocalWorkerFailureRecovery

        recovery = LocalWorkerFailureRecovery()
        try:
            result = self._get_runtime().handle(message.to_dict())
        except Exception as exc:
            recovery_state = recovery.handle_unhandled(message, exc)
            log.error(
                "local worker execution failed: dispatch_id=%s error_type=%s recovery=%s",
                message.dispatch_id,
                type(exc).__name__,
                recovery_state,
            )
        else:
            if isinstance(result, dict) and result.get("status") in {"duplicate", "ambiguous", "inflight"}:
                recovery_state = recovery.handle_duplicate(message)
                log.warning(
                    "local worker ambiguous delivery resolved: dispatch_id=%s recovery=%s",
                    message.dispatch_id,
                    recovery_state,
                )
        return f"local-{message.dispatch_id}"

    def revoke(self, broker_message_id: str) -> None:
        # Local delivery is synchronous. Cancellation is enforced by the job
        # lifecycle before future stages are claimed, not by an external broker.
        return None

    def healthcheck(self) -> None:
        return None

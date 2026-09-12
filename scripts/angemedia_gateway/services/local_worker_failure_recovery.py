"""Local queue recovery policy for failures after worker delivery.

This service keeps brokerless recovery decisions out of the queue adapter. It
never calls Providers. Safe video stages are retried through the durable SQLite
outbox with a new attempt number; ambiguous stages are failed terminally to
avoid repeating potentially billable Provider work.
"""
from __future__ import annotations

from typing import Any

from ..db.connection import db_transaction
from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text
from ..queue.contracts import QueueDispatchEnvelope
from ..queue.messages import JobStageMessage
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.job_attempts import finish_job_attempt
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.job_events import append_job_event
from ..repositories.jobs import StaleJobVersionError, get_job, transition_job_in_connection

_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
_SAFE_VIDEO_STAGES = {"video_poll", "asset_import"}


class LocalWorkerFailureRecovery:
    """Classify and persist local-worker recovery without invoking Providers."""

    def handle_unhandled(self, message: JobStageMessage, error: Any) -> str:
        job = get_job(message.job_id)
        if job is None:
            return "missing"
        if str(job.get("status") or "") in _TERMINAL_STATUSES:
            return "terminal"
        if self._can_resume(job, message.stage) and message.attempt < int(job.get("max_attempts") or 1):
            return self._schedule_retry(job, message, error)
        return self._fail_terminal(job, message, error)

    def handle_duplicate(self, message: JobStageMessage) -> str:
        """Resolve an ambiguous duplicate after a local dispatcher crash/restart.

        Safe video duplicates are normally resumed inside WorkerRuntime before
        reaching this method. If a duplicate still surfaces here, failing it is
        safer than replaying Provider work.
        """
        job = get_job(message.job_id)
        if job is None:
            return "missing"
        if str(job.get("status") or "") in _TERMINAL_STATUSES:
            return "terminal"
        return self._fail_terminal(
            job,
            message,
            "local worker delivery was ambiguous after restart",
            error_code="local_worker_ambiguous_delivery",
        )

    @staticmethod
    def _can_resume(job: dict[str, Any], stage: str) -> bool:
        if str(job.get("kind") or "") != "video":
            return False
        if stage in _SAFE_VIDEO_STAGES:
            return True
        return stage == "video_submit" and bool(str(job.get("external_task_id") or "").strip())

    def _schedule_retry(self, job: dict[str, Any], message: JobStageMessage, error: Any) -> str:
        safe_error = sanitize_error_text(str(error)) or type(error).__name__
        retry_at = now_iso()
        next_attempt = message.attempt + 1
        try:
            with db_transaction(immediate=True) as conn:
                finish_job_attempt(
                    job_id=message.job_id,
                    attempt_number=message.attempt,
                    status="failed",
                    completed_at=retry_at,
                    retry_at=retry_at,
                    error_code="local_worker_unhandled_error",
                    error_message=safe_error,
                    detail={"recovery": "local_retry", "next_attempt": next_attempt},
                    conn=conn,
                )
                transition_job_in_connection(
                    conn,
                    message.job_id,
                    expected_version=int(job["version"]),
                    status="running",
                    stage=message.stage,
                    error_code="local_worker_recovery_pending",
                    error_message=safe_error,
                    error_category="local_worker_failure",
                    human_hint="Local worker recovery is retrying the existing video task without resubmitting it.",
                    retryable=1,
                    gateway_stage=message.stage,
                    next_retry_at=retry_at,
                )
                envelope = QueueDispatchEnvelope(
                    job_id=message.job_id,
                    job_kind=str(job.get("kind") or "video"),
                    stage=message.stage,
                    payload_schema_version=int(job.get("payload_schema_version") or 1),
                    attempt=next_attempt,
                )
                dispatch = create_job_dispatch(
                    job_id=message.job_id,
                    topic=WORKER_TASK_NAME,
                    payload=envelope.as_dict(),
                    available_at=retry_at,
                    conn=conn,
                )
                append_job_event(
                    message.job_id,
                    "local_worker_recovery_scheduled",
                    {
                        "attempt": message.attempt,
                        "next_attempt": next_attempt,
                        "dispatch_id": dispatch["id"],
                        "retryable": True,
                    },
                    to_status="running",
                    stage=message.stage,
                    conn=conn,
                )
        except StaleJobVersionError:
            return "stale"
        return "scheduled"

    def _fail_terminal(
        self,
        job: dict[str, Any],
        message: JobStageMessage,
        error: Any,
        *,
        error_code: str = "local_worker_unhandled_error",
    ) -> str:
        safe_error = sanitize_error_text(str(error)) or type(error).__name__
        completed_at = now_iso()
        try:
            with db_transaction(immediate=True) as conn:
                finish_job_attempt(
                    job_id=message.job_id,
                    attempt_number=message.attempt,
                    status="failed",
                    completed_at=completed_at,
                    error_code=error_code,
                    error_message=safe_error,
                    detail={"recovery": "terminal", "automatic_resubmit": False},
                    conn=conn,
                )
                transition_job_in_connection(
                    conn,
                    message.job_id,
                    expected_version=int(job["version"]),
                    status="failed",
                    stage="finalize",
                    error_code=error_code,
                    error_message=safe_error,
                    error_category="ambiguous_execution",
                    human_hint="The local worker did not automatically repeat Provider work to avoid duplicate generation or billing.",
                    retryable=0,
                    gateway_stage=message.stage,
                    completed_at=completed_at,
                )
                append_job_event(
                    message.job_id,
                    "local_worker_terminal_failure",
                    {
                        "attempt": message.attempt,
                        "automatic_resubmit": False,
                        "error_code": error_code,
                    },
                    to_status="failed",
                    stage="finalize",
                    conn=conn,
                )
        except StaleJobVersionError:
            return "stale"
        return "failed"

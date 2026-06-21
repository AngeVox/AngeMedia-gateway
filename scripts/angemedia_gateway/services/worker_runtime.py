"""Provider-free worker domain entrypoint for validated stage messages."""
from __future__ import annotations

from typing import Any

from ..db.connection import db_transaction
from ..helpers import now_iso
from ..queue.messages import JobStageMessage, parse_job_stage_message
from ..repositories.job_attempts import create_job_attempt, get_job_attempt
from ..repositories.job_events import append_job_event
from ..repositories.jobs import get_job, update_job_attempt_summary
from .job_stage_registry import JobStageRegistry


class WorkerJobNotFound(LookupError):
    pass


class WorkerJobNotExecutable(RuntimeError):
    pass


class WorkerRuntime:
    worker_kind = "celery"

    def __init__(self, *, registry: JobStageRegistry | None = None) -> None:
        self.registry = registry or JobStageRegistry()

    def handle(self, raw_message: Any) -> dict[str, Any]:
        message = parse_job_stage_message(raw_message)
        job = get_job(message.job_id)
        if job is None:
            raise WorkerJobNotFound("worker job not found")
        existing_attempt = get_job_attempt(message.job_id, message.attempt)
        if existing_attempt is not None:
            handler = self.registry.get(message.stage)
            if (
                handler is not None
                and (
                    message.stage in {"video_poll", "asset_import"}
                    or (message.stage == "video_submit" and bool(job.get("external_task_id")))
                )
                and existing_attempt.get("status") == "running"
                and str(job.get("status")) in {"queued", "running"}
                and str(job.get("stage")) == message.stage
            ):
                return handler(message, job)
            append_job_event(
                message.job_id,
                "worker_duplicate_message",
                {"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                stage=message.stage,
            )
            return self._result(message, "duplicate")
        if str(job.get("status")) in {"succeeded", "failed", "canceled"}:
            append_job_event(
                message.job_id,
                "worker_terminal_message_rejected",
                {"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                stage=str(job.get("stage") or "")[:64],
            )
            return self._result(message, "terminal")
        if message.attempt > int(job.get("max_attempts") or 1):
            append_job_event(
                message.job_id,
                "worker_attempt_limit_rejected",
                {
                    "attempt": message.attempt,
                    "dispatch_id": message.dispatch_id,
                    "trace_id": message.trace_id,
                },
                stage=message.stage,
            )
            raise WorkerJobNotExecutable("worker attempt exceeds job limit")
        if str(job.get("stage")) != message.stage:
            append_job_event(
                message.job_id,
                "worker_stage_mismatch",
                {"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                stage=str(job.get("stage") or "")[:64],
            )
            return self._result(message, "stale")

        handler = self.registry.get(message.stage)
        if handler is not None:
            return handler(message, job)

        completed_at = now_iso()
        with db_transaction(immediate=True) as conn:
            updated = update_job_attempt_summary(
                conn,
                job_id=message.job_id,
                stage=message.stage,
                attempt_count=message.attempt,
                worker_kind=self.worker_kind,
            )
            if updated is None:
                raise WorkerJobNotExecutable("worker job changed before attempt recording")
            create_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                stage=message.stage,
                worker_kind=self.worker_kind,
                status="failed",
                completed_at=completed_at,
                error_code="worker_stage_not_implemented",
                error_message="worker stage handler is not implemented",
                detail={"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                conn=conn,
            )
            append_job_event(
                message.job_id,
                "worker_stage_not_implemented",
                {
                    "attempt": message.attempt,
                    "dispatch_id": message.dispatch_id,
                    "trace_id": message.trace_id,
                    "retryable": False,
                },
                stage=message.stage,
                conn=conn,
            )
        return self._result(message, "not_implemented")

    @staticmethod
    def _result(message: JobStageMessage, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "job_id": message.job_id,
            "stage": message.stage,
            "attempt": message.attempt,
            "dispatch_id": message.dispatch_id,
        }

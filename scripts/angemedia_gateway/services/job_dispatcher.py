"""Transactional outbox dispatcher; publishes work but never executes it."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from ..job_sanitizer import sanitize_error_text
from ..queue.messages import EXECUTABLE_STAGES, InvalidQueueMessage, JobStageMessage
from ..repositories.job_dispatches import (
    OutboxClaimLost,
    claim_pending_dispatches,
    mark_dispatch_published,
    release_dispatch,
)
from ..repositories.job_events import append_job_event

log = logging.getLogger("angemedia-gateway")


@dataclass(frozen=True)
class DispatchBatchResult:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    failed: int = 0


class JobDispatcher:
    def __init__(
        self,
        *,
        queue_backend: Any,
        batch_size: int,
        lease_seconds: int,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float = 60.0,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self.queue_backend = queue_backend
        self.batch_size = max(1, int(batch_size))
        self.lease_seconds = max(1, int(lease_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_base_seconds = max(0.1, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self.now_func = now_func or (lambda: datetime.now(timezone.utc))

    def dispatch_once(self) -> DispatchBatchResult:
        now = self._now()
        claim_token = uuid4().hex
        lease_expires = now + timedelta(seconds=self.lease_seconds)
        dispatches = claim_pending_dispatches(
            claim_token=claim_token,
            claim_expires_at=lease_expires.isoformat(),
            limit=self.batch_size,
            now=now.isoformat(),
        )
        published = retried = failed = 0
        for dispatch in dispatches:
            broker_message_id: str | None = None
            try:
                message = self._message_from_dispatch(dispatch)
                append_job_event(
                    dispatch["job_id"],
                    "dispatch_claimed",
                    {
                        "dispatch_id": dispatch["id"],
                        "dispatch_attempt": dispatch["attempt_count"],
                        "trace_id": message.trace_id,
                    },
                    stage=message.stage,
                )
                broker_message_id = self.queue_backend.publish(
                    topic=str(dispatch["topic"]),
                    message=message,
                )
                mark_dispatch_published(
                    dispatch["id"],
                    claim_token=claim_token,
                    broker_message_id=broker_message_id,
                )
                published += 1
                try:
                    append_job_event(
                        dispatch["job_id"],
                        "dispatch_published",
                        {
                            "dispatch_id": dispatch["id"],
                            "trace_id": message.trace_id,
                        },
                        stage=message.stage,
                    )
                except Exception as exc:
                    log.warning(
                        "dispatch published event write failed: dispatch_id=%s error_type=%s",
                        dispatch["id"],
                        type(exc).__name__,
                    )
            except Exception as exc:
                log.warning(
                    "queue dispatch failed: dispatch_id=%s error_type=%s",
                    dispatch["id"],
                    type(exc).__name__,
                )
                # A broker id proves publish returned successfully. If the DB
                # acknowledgement failed, retain the lease and let it expire;
                # the stable Celery task_id plus worker attempt guard make the
                # eventual redelivery idempotent.
                if broker_message_id is not None:
                    failed += 1
                    continue
                safe_error = sanitize_error_text(str(exc)) or "queue publish failed"
                permanent = isinstance(exc, (InvalidQueueMessage, TypeError, ValueError))
                terminal = permanent or int(dispatch["attempt_count"]) >= self.max_attempts
                retry_at = now + timedelta(seconds=self._retry_delay(int(dispatch["attempt_count"])))
                try:
                    release_dispatch(
                        dispatch["id"],
                        claim_token=claim_token,
                        error_message=safe_error,
                        available_at=retry_at.isoformat(),
                        terminal=terminal,
                    )
                    append_job_event(
                        dispatch["job_id"],
                        "dispatch_failed" if terminal else "dispatch_retry_scheduled",
                        {
                            "dispatch_id": dispatch["id"],
                            "error": safe_error,
                            "retryable": not terminal,
                        },
                        stage=self._safe_stage(dispatch),
                    )
                    if terminal:
                        failed += 1
                    else:
                        retried += 1
                except OutboxClaimLost:
                    failed += 1
        return DispatchBatchResult(
            claimed=len(dispatches),
            published=published,
            retried=retried,
            failed=failed,
        )

    def _message_from_dispatch(self, dispatch: dict[str, Any]) -> JobStageMessage:
        try:
            payload = json.loads(str(dispatch.get("payload_json") or "{}"))
        except (TypeError, ValueError):
            raise InvalidQueueMessage() from None
        if not isinstance(payload, dict):
            raise InvalidQueueMessage()
        return JobStageMessage(
            job_id=str(dispatch.get("job_id") or ""),
            stage=str(payload.get("stage") or ""),
            attempt=payload.get("attempt", 1),
            dispatch_id=str(dispatch.get("id") or ""),
            trace_id=str(dispatch.get("id") or ""),
        )

    def _retry_delay(self, attempt: int) -> float:
        return min(self.retry_base_seconds * (2 ** max(0, attempt - 1)), self.retry_max_seconds)

    def _now(self) -> datetime:
        value = self.now_func()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _safe_stage(dispatch: dict[str, Any]) -> str | None:
        try:
            payload = json.loads(str(dispatch.get("payload_json") or "{}"))
        except (TypeError, ValueError):
            return None
        stage = payload.get("stage") if isinstance(payload, dict) else None
        return str(stage) if stage in EXECUTABLE_STAGES else None

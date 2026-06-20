"""Atomic job admission and transactional outbox orchestration."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..db.connection import db_transaction
from ..helpers import now_iso
from ..job_sanitizer import sanitized_json
from ..queue.contracts import QueueDispatchEnvelope
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.job_events import append_job_event
from ..repositories.jobs import (
    ACTIVE_JOB_STATUSES,
    VALID_JOB_STAGES,
    find_recent_job_by_request_hash,
    insert_job,
)

_REQUEST_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True)
class AdmissionResult:
    job: dict[str, Any]
    dispatch: dict[str, Any] | None
    created: bool


class JobAdmissionService:
    """Creates durable work without depending on Redis or Celery."""

    def admit(
        self,
        *,
        kind: str,
        stage: str,
        request_hash: str | None,
        request_hash_version: int | None,
        payload: Any,
        provider: str | None = None,
        model: str | None = None,
        prompt: str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        payload_schema_version: int = 1,
        scheduled_at: str | None = None,
        topic: str = "angemedia.jobs.execute",
    ) -> AdmissionResult:
        if kind not in {"image", "video"}:
            raise ValueError(f"unsupported job kind: {kind}")
        if stage not in VALID_JOB_STAGES or stage in {"admitted", "finalize"}:
            raise ValueError(f"invalid admission stage: {stage}")
        if request_hash is not None and not _REQUEST_HASH_RE.fullmatch(request_hash):
            raise ValueError("request_hash must be a SHA-256 hex digest")
        if (request_hash is None) != (request_hash_version is None):
            raise ValueError("request_hash and request_hash_version must be provided together")
        if payload_schema_version < 1 or max_attempts < 1:
            raise ValueError("payload_schema_version and max_attempts must be positive")

        safe_payload_json = sanitized_json(payload)
        with db_transaction(immediate=True) as conn:
            existing = find_recent_job_by_request_hash(
                kind=kind,
                request_hash=request_hash,
                request_hash_version=request_hash_version,
                statuses=ACTIVE_JOB_STATUSES,
                conn=conn,
            )
            if existing is not None:
                return AdmissionResult(job=existing, dispatch=None, created=False)

            job = insert_job(
                conn,
                kind=kind,
                status="queued",
                provider=provider,
                model=model,
                prompt=prompt,
                input_json=safe_payload_json,
                request_hash=request_hash,
                request_hash_version=request_hash_version,
                stage=stage,
                payload_schema_version=payload_schema_version,
                priority=priority,
                scheduled_at=scheduled_at,
                max_attempts=max_attempts,
            )
            append_job_event(
                job["id"],
                "admitted",
                {"payload_schema_version": payload_schema_version, "priority": priority},
                to_status="queued",
                stage=stage,
                conn=conn,
            )
            envelope = QueueDispatchEnvelope(
                job_id=job["id"],
                job_kind=kind,
                stage=stage,
                payload_schema_version=payload_schema_version,
            )
            dispatch = create_job_dispatch(
                job_id=job["id"],
                topic=topic,
                payload=envelope.as_dict(),
                available_at=scheduled_at or now_iso(),
                conn=conn,
            )
            return AdmissionResult(job=job, dispatch=dispatch, created=True)

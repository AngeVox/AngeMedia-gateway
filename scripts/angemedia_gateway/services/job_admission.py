"""Atomic job admission and transactional outbox orchestration."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from ..helpers import now_iso
from ..job_submission import JobSubmissionKey, canonical_submission_json, submission_intent_hash
from ..job_sanitizer import sanitized_json
from ..queue.contracts import QueueDispatchEnvelope
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.job_events import append_job_event
from ..repositories.job_submission_keys import bind_submission, replay_submission
from .job_admission_transaction import admission_transaction
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
        topic: str = WORKER_TASK_NAME,
        dedupe_hashes: Iterable[tuple[str | None, int | None]] = (),
        submission_key: JobSubmissionKey | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> AdmissionResult:
        if kind not in {"image", "video"}:
            raise ValueError(f"unsupported job kind: {kind}")
        if stage not in VALID_JOB_STAGES or stage in {"admitted", "finalize"}:
            raise ValueError(f"invalid admission stage: {stage}")
        if request_hash is not None and not _REQUEST_HASH_RE.fullmatch(request_hash):
            raise ValueError("request_hash must be a SHA-256 hex digest")
        if (request_hash is None) != (request_hash_version is None):
            raise ValueError("request_hash and request_hash_version must be provided together")
        dedupe_candidates: list[tuple[str, int]] = []
        for candidate_hash, candidate_version in dedupe_hashes:
            if not candidate_hash or candidate_version is None:
                continue
            if not _REQUEST_HASH_RE.fullmatch(candidate_hash):
                raise ValueError("dedupe request_hash must be a SHA-256 hex digest")
            dedupe_candidates.append((candidate_hash, int(candidate_version)))
        if payload_schema_version < 1 or max_attempts < 1:
            raise ValueError("payload_schema_version and max_attempts must be positive")
        if topic != WORKER_TASK_NAME:
            raise ValueError("unsupported queue task topic")
        if submission_key is not None and not isinstance(submission_key, JobSubmissionKey):
            raise ValueError('持久提交键类型无效')

        if submission_key is not None:
            # 稳定键的载荷只取一次快照，持久化正文与意图摘要不能读取两份可变输入。
            payload = json.loads(canonical_submission_json(payload))
        safe_payload_json = sanitized_json(payload)
        # 摘要绑定原始意图，不能让脱敏或字段截断把不同请求折叠成同一个键。
        intent_hash = submission_intent_hash({
            'kind': kind, 'stage': stage, 'request_hash': request_hash, 'request_hash_version': request_hash_version,
            'payload': payload, 'provider': provider, 'model': model, 'prompt': prompt, 'priority': priority,
            'max_attempts': max_attempts, 'payload_schema_version': payload_schema_version,
            'scheduled_at': scheduled_at, 'topic': topic,
        }) if submission_key is not None else None
        with admission_transaction(conn) as conn:
            if submission_key is not None:
                existing = replay_submission(conn, submission_key, intent_hash)
                if existing is not None:
                    return AdmissionResult(job=existing, dispatch=None, created=False)
            # 独立动作可有相同内容；没有提交键的旧入口保留活动任务内容去重。
            candidates = [] if submission_key is not None else [(request_hash, request_hash_version), *dedupe_candidates]
            seen: set[tuple[str, int]] = set()
            for candidate_hash, candidate_version in candidates:
                if not candidate_hash or candidate_version is None:
                    continue
                key = (candidate_hash, int(candidate_version))
                if key in seen:
                    continue
                seen.add(key)
                existing = find_recent_job_by_request_hash(
                    kind=kind,
                    request_hash=candidate_hash,
                    request_hash_version=candidate_version,
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
                dedupe_scope='submission' if submission_key is not None else 'content',
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
            if submission_key is not None:
                bind_submission(conn, submission_key, intent_hash, job['id'])
            return AdmissionResult(job=job, dispatch=dispatch, created=True)

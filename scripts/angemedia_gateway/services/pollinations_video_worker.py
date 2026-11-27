"""处理 Pollinations 视频的提交、状态查询和入库，重复投递只恢复已知结果。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3

from .. import config as C
from ..db.connection import db_transaction
from ..media_execution_scope import MediaExecutionRejected
from ..pollinations_video import POLLINATIONS_VIDEO_MAX_ATTEMPTS
from ..providers.errors import ProviderError, ProviderProtocolError
from ..providers.pollinations_video import PollinationsVideoProvider
from ..providers.reference_relay import ReferenceRelayUnavailable
from ..queue.contracts import QueueDispatchEnvelope
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.assistant_action_execution import job_intent_digest, load_grant
from ..repositories.job_attempts import create_job_attempt, finish_job_attempt, get_job_attempt
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.job_events import append_job_event
from ..repositories.jobs import StaleJobVersionError, claim_job_attempt, get_job, transition_job_in_connection
from ..repositories.pollinations_video_receipts import receive_video, reserve_video_submit, video_receipt
from .media_connection_binding import connection_binding
from .pollinations_video_execution import PROVIDER, TARGET, pollinations_video_execution, validate_video_ownership
from .pollinations_video_files import pollinations_video_file, temporary_video_file
from .video_job_admission import parse_video_job_payload
from .video_job_finalizer import ImportedVideoFinalization, finalize_imported_video
from .video_polling import VideoPipelinePolicy

_REJECTION_CODES = {
    400: ("pollinations_video_request_rejected", "validation"),
    401: ("pollinations_video_auth", "auth"),
    402: ("pollinations_video_balance", "billing"),
    403: ("pollinations_video_permission", "permission"),
    422: ("pollinations_video_request_rejected", "validation"),
    429: ("pollinations_video_rate_limit", "rate_limit"),
}


class PollinationsVideoWorker:
    def __init__(self, *, worker_kind="celery", provider=None, now=None):
        self.worker_kind = worker_kind
        self.provider = provider or PollinationsVideoProvider()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.policy = VideoPipelinePolicy(5, 1800, POLLINATIONS_VIDEO_MAX_ATTEMPTS, now_func=self.now)

    @staticmethod
    def _result(message, status):
        return {"status": status, "job_id": message.job_id, "stage": message.stage,
                "attempt": message.attempt, "dispatch_id": message.dispatch_id}

    def _dispatch(self, conn, message, stage, when, *, resume=False):
        envelope = QueueDispatchEnvelope(
            job_id=message.job_id, job_kind="video", stage=stage, payload_schema_version=1,
            attempt=message.attempt if resume else message.attempt + 1,
        )
        create_job_dispatch(job_id=message.job_id, topic=WORKER_TASK_NAME, payload=envelope.as_dict(),
                            available_at=when.isoformat(), conn=conn)

    @staticmethod
    def _transition(conn, job, status, **fields):
        return transition_job_in_connection(
            conn, job["id"], expected_version=job["version"], status=status, **fields,
        )

    def _finish(self, conn, message, status, *, code="", retry_at=None):
        finish_job_attempt(
            job_id=message.job_id, attempt_number=message.attempt, status=status,
            error_code=code, retry_at=retry_at, completed_at=self.now().isoformat(), conn=conn,
        )

    def _fail(self, conn, message, job, code, category, *, provider_status=None):
        self._transition(conn, job, "failed", stage="finalize", gateway_stage=message.stage,
                         error_code=code, error_category=category, retryable=False, provider_status=provider_status,
                         completed_at=self.now().isoformat(), next_retry_at="", claim_expires_at="")
        self._finish(conn, message, "failed", code=code)
        append_job_event(job["id"], "worker_attempt_failed", {"attempt": message.attempt, "error_code": code},
                         to_status="failed", stage=message.stage, conn=conn)
        return self._result(message, "failed")

    def _schedule(self, conn, message, job, stage, *, delay=0, provider_status, task_id=None, error_code=""):
        due = self.now() + timedelta(seconds=delay)
        self._transition(conn, job, "running", stage=stage, provider_status=provider_status,
                         external_task_id=task_id, next_retry_at=due.isoformat(), claim_expires_at="",
                         error_code=error_code, error_category="network" if error_code else "",
                         retryable=bool(error_code), gateway_stage=message.stage)
        self._finish(conn, message, "failed" if error_code else "succeeded", code=error_code,
                     retry_at=due.isoformat() if error_code else None)
        append_job_event(job["id"], "worker_video_stage_scheduled", {"attempt": message.attempt, "next_stage": stage},
                         to_status="running", stage=stage, conn=conn)
        self._dispatch(conn, message, stage, due)
        return self._result(message, "scheduled")

    def _begin(self, message, job):
        with db_transaction(immediate=True) as conn:
            current = get_job(job["id"], conn=conn)
            if (current is None or current["version"] != job["version"] or current["stage"] != message.stage
                or current["status"] not in {"queued", "running"}):
                return None, None, self._result(message, "stale")
            attempt = get_job_attempt(job["id"], message.attempt, conn=conn)
            receipt = video_receipt(conn, job["id"])
            if attempt is not None:
                if (attempt["status"] != "running" or attempt["stage"] != message.stage
                    or message.attempt != current["attempt_count"]):
                    return None, None, self._result(message, "duplicate")
                if message.stage == "video_submit":
                    return None, None, self._resume_submit(conn, message, current, receipt)
                if current["claim_expires_at"] and self.now() < datetime.fromisoformat(current["claim_expires_at"]):
                    return None, None, self._result(message, "inflight")
            elif (message.attempt != current["attempt_count"] + 1
                  or (current["next_retry_at"] and self.now() < datetime.fromisoformat(current["next_retry_at"]))):
                return None, None, self._result(message, "not_due")
            if message.stage == "video_submit" and (receipt is not None or message.attempt != 1):
                return None, None, self._result(message, "stale")

            claimed = claim_job_attempt(
                conn, job_id=job["id"], expected_version=job["version"], stage=message.stage,
                attempt_count=message.attempt, worker_kind=self.worker_kind, provider=job["provider"], model=job["model"],
                started_at=self.now().isoformat(), allow_running=True,
            )
            if attempt is None:
                create_job_attempt(job_id=job["id"], attempt_number=message.attempt, stage=message.stage,
                                   worker_kind=self.worker_kind, status="running", started_at=self.now().isoformat(),
                                   detail={"dispatch_id": message.dispatch_id}, conn=conn)
            try:
                request, _ = parse_video_job_payload(json.loads(claimed["input_json"]), provider=PROVIDER)
                if claimed["model"] != request.model:
                    raise ValueError("Video model does not match job")
                load_grant(conn, claimed)
            except MediaExecutionRejected as exc:
                return None, None, self._fail(conn, message, claimed, exc.code, "action_authorization")
            except (ValueError, TypeError):
                return None, None, self._fail(conn, message, claimed, "invalid_video_job", "validation")
            if message.stage == "video_submit":
                reserve_video_submit(conn, job["id"], job_intent_digest(claimed), connection_binding(PROVIDER))
                receipt = video_receipt(conn, job["id"])
            lease = self.now() + timedelta(seconds=max(120, float(C.HTTP_TIMEOUT) * 3 + 90))
            claimed = self._transition(conn, claimed, "running", claim_expires_at=lease.isoformat())
            self._dispatch(conn, message, message.stage, lease, resume=True)
            append_job_event(job["id"], "worker_attempt_started", {"attempt": message.attempt},
                             stage=message.stage, conn=conn)
            return claimed, (request, receipt), None

    def _resume_submit(self, conn, message, job, receipt):
        if self.now() < datetime.fromisoformat(job["claim_expires_at"]) and (
            receipt is None or receipt["state"] == "prepared"
        ):
            return self._result(message, "inflight")
        try:
            validate_video_ownership(conn, job, receipt)
        except MediaExecutionRejected as exc:
            return self._fail(conn, message, job, exc.code, "action_authorization")
        if receipt["state"] == "prepared":
            if not pollinations_video_file(job["id"], staged=True).is_file():
                return self._fail(conn, message, job, "pollinations_video_submit_unknown", "ambiguous_submit")
            receive_video(conn, job["id"], None)
            receipt = video_receipt(conn, job["id"])
        task_id = receipt["task_id"]
        return self._schedule(conn, message, job, "video_poll" if task_id else "asset_import",
                              delay=receipt["retry_after"], provider_status="pending" if task_id else "cached",
                              task_id=task_id)

    def _submit(self, message, job, request, receipt):
        with temporary_video_file(job["id"]) as temporary:
            with pollinations_video_execution(job, receipt, phase="submit"):
                accepted = asyncio.run(self.provider.submit(request, temporary))
            # 编号先提交到 SQLite。下一阶段投递失败时，原守护可从收据恢复。
            with db_transaction(immediate=True) as conn:
                validate_video_ownership(conn, job, receipt)
                if accepted.task_id is None:
                    os.replace(temporary, pollinations_video_file(job["id"], staged=True))
                receive_video(conn, job["id"], accepted.task_id, accepted.retry_after if accepted.task_id else 0)
        with db_transaction(immediate=True) as conn:
            return self._resume_submit(conn, message, job, video_receipt(conn, job["id"]))

    def _poll(self, message, job, receipt):
        with pollinations_video_execution(job, receipt, phase="followup") as scope:
            task_id = receipt["task_id"]
            observation = asyncio.run(self.provider.poll(task_id))
            scope.require_target(*TARGET, operation="followup")
        with db_transaction(immediate=True) as conn:
            current = validate_video_ownership(conn, job, receipt)
            if observation.status == "failed":
                return self._fail(conn, message, current, "video_generation_failed", "task_failed", provider_status="failed")
            if message.attempt >= job["max_attempts"]:
                return self._fail(conn, message, current, "pollinations_video_timeout", "local_timeout")
            complete = observation.status == "succeeded"
            delay = 0 if complete else max(self.policy.delay_seconds(message.attempt - 1), observation.retry_after)
            return self._schedule(conn, message, current, "asset_import" if complete else "video_poll",
                                  provider_status=observation.status, task_id=task_id, delay=delay)

    def _import(self, message, job, request, receipt):
        staged = pollinations_video_file(job["id"], staged=True)
        public = pollinations_video_file(job["id"])
        with pollinations_video_execution(job, receipt, phase="followup") as scope:
            if not staged.is_file() and not public.is_file():
                if not receipt["task_id"]:
                    raise ProviderProtocolError("Pollinations cached video is missing")
                with temporary_video_file(job["id"]) as temporary:
                    asyncio.run(self.provider.retrieve(receipt["task_id"], temporary))
                    with db_transaction(immediate=True) as conn:
                        validate_video_ownership(conn, job, receipt)
                        os.replace(temporary, staged)
            scope.require_target(*TARGET, operation="followup")
        with db_transaction(immediate=True) as conn:
            current = validate_video_ownership(conn, job, receipt)
            if staged.is_file():
                os.replace(staged, public)
            if not public.is_file():
                raise ProviderProtocolError("Pollinations video is missing")
            relative = public.relative_to(C.OUTPUT_DIR.resolve()).as_posix()
            duration = max(0, int((self.now() - datetime.fromisoformat(job["started_at"])).total_seconds() * 1000))
            finalize_imported_video(ImportedVideoFinalization(
                job=current, job_id=job["id"], attempt=message.attempt, task_id=receipt["task_id"],
                expected_version=job["version"], result={"local_path": str(public), "video_url": f"/generated/{relative}",
                "localized": True, "status": "completed", "task_id": receipt["task_id"]},
                duration_ms=duration, input_mode=request.mode,
            ), conn=conn)
        return self._result(message, "succeeded")

    def _handle_error(self, message, job, receipt, error):
        with db_transaction(immediate=True) as conn:
            current = get_job(job["id"], conn=conn)
            if current is None or current["version"] != job["version"]:
                return self._result(message, "stale")
            if message.stage == "video_submit" and video_receipt(conn, job["id"]) != receipt:
                return self._result(message, "recovery_pending")
            if isinstance(error, MediaExecutionRejected):
                return self._fail(conn, message, current, error.code, "action_authorization")
            if isinstance(error, ReferenceRelayUnavailable):
                return self._fail(conn, message, current, "pollinations_video_reference_unavailable", "validation")
            rejection = _REJECTION_CODES.get(getattr(error, "status_code", None))
            if message.stage == "video_submit":
                if rejection is None and getattr(error, "error_category", None) == "auth":
                    rejection = _REJECTION_CODES[401]
                code, category = rejection or ("pollinations_video_submit_unknown", "ambiguous_submit")
                return self._fail(conn, message, current, code, category)
            retryable = isinstance(error, OSError) or (isinstance(error, ProviderError) and error.retryable)
            if retryable:
                if message.attempt >= job["max_attempts"] or self.policy.exhausted(
                    attempt=message.attempt, started_at=job["started_at"]
                ):
                    return self._fail(conn, message, current, "pollinations_video_timeout", "local_timeout")
                code = "pollinations_video_read_retry"
                return self._schedule(conn, message, current, message.stage,
                                      delay=max(self.policy.delay_seconds(message.attempt - 1),
                                                getattr(error, 'retry_after_seconds', 0)),
                                      provider_status=job["provider_status"], task_id=receipt["task_id"], error_code=code)
            code, category = rejection or ("pollinations_video_read_failed", "provider_response")
            if getattr(error, "status_code", None) == 404:
                code, category = "pollinations_video_result_unavailable", "provider_identity"
            return self._fail(conn, message, current, code, category)

    def handle(self, message, job):
        try:
            job, work, result = self._begin(message, job)
            if result is not None:
                return result
            request, receipt = work
            try:
                if message.stage == "video_submit":
                    return self._submit(message, job, request, receipt)
                if self.policy.exhausted(attempt=message.attempt - 1, started_at=job["started_at"]):
                    with db_transaction(immediate=True) as conn:
                        current = validate_video_ownership(conn, job, receipt)
                        return self._fail(conn, message, current, "pollinations_video_timeout", "local_timeout")
                if message.stage == "video_poll":
                    return self._poll(message, job, receipt)
                return self._import(message, job, request, receipt)
            except sqlite3.Error:
                # 领取成功即持久保存守护投递，数据库暂时繁忙后由同阶段继续。
                return self._result(message, "recovery_pending")
            except (MediaExecutionRejected, ProviderError, ReferenceRelayUnavailable, OSError, ValueError) as exc:
                return self._handle_error(message, job, receipt, exc)
        except StaleJobVersionError:
            return self._result(message, "stale")

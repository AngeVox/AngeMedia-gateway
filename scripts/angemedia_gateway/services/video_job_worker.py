"""Durable video_submit, video_poll, and asset_import stage handlers."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..db.connection import db_transaction
from ..error_diagnostics import classify_provider_error
from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text
from ..queue.contracts import QueueDispatchEnvelope
from ..queue.messages import JobStageMessage
from ..queue.settings import WORKER_TASK_NAME
from ..repositories.generations import record_generation
from ..repositories.job_attempts import (
    create_job_attempt,
    finish_job_attempt,
    get_job_attempt,
    get_running_stage_attempt,
)
from ..repositories.job_dispatches import create_job_dispatch
from ..repositories.job_events import append_job_event
from ..repositories.jobs import claim_job_attempt, transition_job_in_connection
from ..repositories.video_tasks import upsert_video_task
from ..security import validate_task_id
from .generation_assets import save_generated_asset
from .job_lifecycle import StaleJobVersion
from .video_asset_import import VideoAssetImportService
from .video_execution import (
    VideoExecutionService,
    VideoPollResult,
    VideoProviderDisabled,
    build_runtime_video_executor,
)
from .video_job_admission import parse_video_job_payload
from .video_polling import VideoPipelinePolicy, poll_decision, video_output_summary


class VideoJobWorker:
    worker_kind = "celery"

    def __init__(
        self,
        *,
        executor: VideoExecutionService | Any | None = None,
        asset_importer: VideoAssetImportService | Any | None = None,
        policy: VideoPipelinePolicy | None = None,
    ) -> None:
        self.executor = executor or build_runtime_video_executor()
        self.asset_importer = asset_importer or VideoAssetImportService()
        self.policy = policy or VideoPipelinePolicy.from_config()

    def _claim(
        self,
        message: JobStageMessage,
        job: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        with db_transaction(immediate=True) as conn:
            existing = get_job_attempt(message.job_id, message.attempt, conn=conn)
            if existing is not None:
                if (
                    existing.get("status") == "running"
                    and (
                        message.stage in {"video_poll", "asset_import"}
                        or (message.stage == "video_submit" and bool(job.get("external_task_id")))
                    )
                ):
                    append_job_event(
                        message.job_id,
                        "worker_attempt_resumed",
                        {"attempt": message.attempt, "dispatch_id": message.dispatch_id},
                        stage=message.stage,
                        conn=conn,
                    )
                    return job, None
                return None, "ambiguous" if message.stage == "video_submit" else "duplicate"
            if get_running_stage_attempt(message.job_id, message.stage, conn=conn) is not None:
                append_job_event(
                    message.job_id,
                    "worker_stage_inflight",
                    {"attempt": message.attempt, "dispatch_id": message.dispatch_id},
                    stage=message.stage,
                    conn=conn,
                )
                return None, "ambiguous" if message.stage == "video_submit" else "inflight"
            claimed = claim_job_attempt(
                conn,
                job_id=message.job_id,
                expected_version=int(job["version"]),
                stage=message.stage,
                attempt_count=message.attempt,
                worker_kind=self.worker_kind,
                provider="agnes_video",
                model=str(job.get("model") or "") or None,
                started_at=now_iso(),
                allow_running=True,
            )
            if claimed is None:
                return None, "stale"
            create_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                stage=message.stage,
                worker_kind=self.worker_kind,
                status="running",
                detail={"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                conn=conn,
            )
            append_job_event(
                message.job_id,
                "worker_attempt_started",
                {"attempt": message.attempt, "dispatch_id": message.dispatch_id},
                from_status=str(job.get("status") or "running"),
                to_status="running",
                stage=message.stage,
                conn=conn,
            )
            return claimed, None

    @staticmethod
    def _result(message: JobStageMessage, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "job_id": message.job_id,
            "stage": message.stage,
            "attempt": message.attempt,
            "dispatch_id": message.dispatch_id,
        }

    def _dispatch_stage(
        self,
        *,
        conn,
        message: JobStageMessage,
        next_stage: str,
        available_at: str,
    ) -> dict[str, Any]:
        envelope = QueueDispatchEnvelope(
            job_id=message.job_id,
            job_kind="video",
            stage=next_stage,
            payload_schema_version=1,
            attempt=message.attempt + 1,
        )
        return create_job_dispatch(
            job_id=message.job_id,
            topic=WORKER_TASK_NAME,
            payload=envelope.as_dict(),
            available_at=available_at,
            conn=conn,
        )

    def _schedule(
        self,
        message: JobStageMessage,
        job: dict[str, Any],
        *,
        expected_version: int,
        next_stage: str,
        task_id: str,
        provider_status: str,
        delay: bool,
        attempt_status: str = "succeeded",
        error_code: str = "",
        error_message: str = "",
        retryable: bool = False,
        error_category: str = "",
        human_hint: str = "",
    ) -> None:
        available_at = (
            self.policy.available_at(message.attempt)
            if delay else self.policy.now_func().isoformat()
        )
        with db_transaction(immediate=True) as conn:
            transition_job_in_connection(
                conn,
                message.job_id,
                expected_version=expected_version,
                status="running",
                stage=next_stage,
                output_json=video_output_summary(
                    task_id=task_id,
                    provider_status=provider_status,
                ),
                external_task_id=task_id,
                provider_status=provider_status,
                error_code=error_code,
                error_message=error_message,
                error_category=error_category,
                human_hint=human_hint,
                retryable=1 if retryable else 0,
                gateway_stage=message.stage,
                next_retry_at=available_at,
            )
            finish_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                status=attempt_status,
                completed_at=now_iso(),
                retry_at=available_at if attempt_status == "failed" else None,
                error_code=error_code or None,
                error_message=error_message or None,
                detail={"next_stage": next_stage, "provider_status": provider_status},
                conn=conn,
            )
            dispatch = self._dispatch_stage(
                conn=conn,
                message=message,
                next_stage=next_stage,
                available_at=available_at,
            )
            append_job_event(
                message.job_id,
                "worker_stage_scheduled",
                {
                    "attempt": message.attempt,
                    "next_stage": next_stage,
                    "dispatch_id": dispatch["id"],
                    "retryable": retryable,
                },
                to_status="running",
                stage=next_stage,
                conn=conn,
            )
            upsert_video_task(
                task_id,
                str(job.get("prompt") or ""),
                str(job.get("model") or ""),
                provider_status,
                {"task_id": task_id, "status": provider_status},
                duration_ms=int(job.get("duration_ms") or 0),
                conn=conn,
            )

    def _fail(
        self,
        message: JobStageMessage,
        job: dict[str, Any],
        *,
        expected_version: int,
        error_code: str,
        error: Any,
        retryable: bool = False,
        error_category: str | None = None,
    ) -> None:
        safe_error = sanitize_error_text(str(error)) or type(error).__name__
        classification = classify_provider_error(safe_error)
        ambiguous_timeout_hint = (
            "视频提交超时，系统未收到上游任务号；为避免重复扣费或重复生成，不会自动重提。"
            "可在渠道页的全局视频请求超时中调高等待时间后手动重新提交。"
            if error_code == "video_submit_ambiguous" and "timeout" in safe_error.lower()
            else "Provider submission outcome is ambiguous; automatic resubmit is disabled."
        )
        with db_transaction(immediate=True) as conn:
            transition_job_in_connection(
                conn,
                message.job_id,
                expected_version=expected_version,
                status="failed",
                stage="finalize",
                error_code=error_code,
                error_message=safe_error,
                error_category=error_category or classification["error_category"],
                human_hint=(
                    ambiguous_timeout_hint
                    if error_code == "video_submit_ambiguous"
                    else classification["human_hint"]
                ),
                retryable=1 if retryable else 0,
                gateway_stage=message.stage,
                completed_at=now_iso(),
            )
            finish_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                status="failed",
                completed_at=now_iso(),
                error_code=error_code,
                error_message=safe_error,
                detail={"retryable": retryable},
                conn=conn,
            )
            append_job_event(
                message.job_id,
                "worker_attempt_failed",
                {"attempt": message.attempt, "error_code": error_code, "retryable": retryable},
                to_status="failed",
                stage="finalize",
                conn=conn,
            )
            task_id = str(job.get("external_task_id") or "")
            if task_id:
                upsert_video_task(
                    validate_task_id(task_id),
                    str(job.get("prompt") or ""),
                    str(job.get("model") or ""),
                    "failed",
                    {"task_id": task_id, "status": "failed"},
                    duration_ms=int(job.get("duration_ms") or 0),
                    conn=conn,
                )

    def _retry(
        self,
        message: JobStageMessage,
        job: dict[str, Any],
        *,
        expected_version: int,
        task_id: str,
        provider_status: str,
        error_code: str,
        error: Any,
    ) -> str:
        if self.policy.exhausted(attempt=message.attempt, started_at=job.get("started_at")):
            self._fail(
                message,
                job,
                expected_version=expected_version,
                error_code="video_pipeline_timeout",
                error="video pipeline retry limit exceeded",
                retryable=False,
            )
            return "failed"
        safe_error = sanitize_error_text(str(error)) or type(error).__name__
        classification = classify_provider_error(safe_error)
        self._schedule(
            message,
            job,
            expected_version=expected_version,
            next_stage=message.stage,
            task_id=task_id,
            provider_status=provider_status,
            delay=True,
            attempt_status="failed",
            error_code=error_code,
            error_message=safe_error,
            retryable=True,
            error_category=classification["error_category"],
            human_hint=classification["human_hint"],
        )
        return "scheduled"

    def _record_submitted_recovery(
        self,
        message: JobStageMessage,
        *,
        expected_version: int,
        task_id: str,
        provider_status: str,
    ) -> None:
        """Preserve a known task id when poll-dispatch persistence fails."""
        with db_transaction(immediate=True) as conn:
            transition_job_in_connection(
                conn,
                message.job_id,
                expected_version=expected_version,
                status="running",
                stage="video_submit",
                output_json=video_output_summary(
                    task_id=task_id,
                    provider_status=provider_status,
                ),
                external_task_id=task_id,
                provider_status=provider_status,
                error_code="video_submit_dispatch_pending",
                error_message="provider task accepted; poll dispatch persistence pending",
                error_category="queue_persistence",
                human_hint="Provider task was accepted; worker delivery will resume without resubmitting.",
                retryable=1,
                gateway_stage="video_submit",
            )
            append_job_event(
                message.job_id,
                "video_submit_dispatch_recovery",
                {"attempt": message.attempt, "provider_status": provider_status},
                to_status="running",
                stage="video_submit",
                conn=conn,
            )

    def handle_submit(self, message: JobStageMessage, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("kind") != "video" or message.stage != "video_submit":
            raise ValueError("video submit worker received incompatible job")
        try:
            request, _ = parse_video_job_payload(json.loads(str(job.get("input_json") or "{}")))
        except Exception as exc:
            claimed, state = self._claim(message, job)
            if claimed is None:
                return self._result(message, state or "stale")
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="invalid_video_job_payload", error=exc,
            )
            return self._result(message, "failed")

        claimed, state = self._claim(message, job)
        if claimed is None:
            return self._result(message, state or "stale")
        existing_task_id = str(job.get("external_task_id") or "")
        if existing_task_id:
            task_id = validate_task_id(existing_task_id)
            self._schedule(
                message, job, expected_version=int(claimed["version"]),
                next_stage="video_poll", task_id=task_id,
                provider_status=str(job.get("provider_status") or "queued"), delay=True,
            )
            return self._result(message, "scheduled")
        try:
            submitted = asyncio.run(self.executor.submit(request))
        except VideoProviderDisabled as exc:
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_provider_disabled", error=exc,
            )
            return self._result(message, "failed")
        except Exception as exc:
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_submit_ambiguous", error=exc,
                retryable=False, error_category="ambiguous_submit",
            )
            return self._result(message, "failed")
        try:
            self._schedule(
                message, job, expected_version=int(claimed["version"]),
                next_stage="video_poll", task_id=submitted.task_id,
                provider_status=submitted.provider_status, delay=True,
            )
            return self._result(message, "scheduled")
        except StaleJobVersion:
            return self._result(message, "stale")
        except Exception as exc:
            self._record_submitted_recovery(
                message,
                expected_version=int(claimed["version"]),
                task_id=submitted.task_id,
                provider_status=submitted.provider_status,
            )
            raise

    def handle_poll(self, message: JobStageMessage, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("kind") != "video" or message.stage != "video_poll":
            raise ValueError("video poll worker received incompatible job")
        task_id = validate_task_id(str(job.get("external_task_id") or ""))
        claimed, state = self._claim(message, job)
        if claimed is None:
            return self._result(message, state or "stale")
        if self.policy.exhausted(attempt=message.attempt, started_at=job.get("started_at")):
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_poll_timeout", error="video poll timeout",
            )
            return self._result(message, "failed")
        try:
            polled = asyncio.run(self.executor.poll(task_id))
        except VideoProviderDisabled as exc:
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_provider_disabled", error=exc,
            )
            return self._result(message, "failed")
        except Exception as exc:
            status = self._retry(
                message, job, expected_version=int(claimed["version"]),
                task_id=task_id, provider_status="poll_error",
                error_code="video_poll_failed", error=exc,
            )
            return self._result(message, status)

        decision = poll_decision(polled)
        if decision == "failed":
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_generation_failed",
                error=polled.error_message or polled.provider_status,
                error_category="provider_failure",
            )
            return self._result(message, "failed")
        next_stage = "asset_import" if decision == "completed" else "video_poll"
        self._schedule(
            message, job, expected_version=int(claimed["version"]),
            next_stage=next_stage, task_id=task_id,
            provider_status=polled.provider_status,
            delay=next_stage == "video_poll",
        )
        return self._result(message, "scheduled")

    def handle_asset_import(self, message: JobStageMessage, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("kind") != "video" or message.stage != "asset_import":
            raise ValueError("video asset worker received incompatible job")
        task_id = validate_task_id(str(job.get("external_task_id") or ""))
        claimed, state = self._claim(message, job)
        if claimed is None:
            return self._result(message, state or "stale")
        try:
            polled: VideoPollResult = asyncio.run(self.executor.poll(task_id))
            decision = poll_decision(polled)
            if decision == "failed":
                self._fail(
                    message, job, expected_version=int(claimed["version"]),
                    error_code="video_generation_failed",
                    error=polled.error_message or polled.provider_status,
                )
                return self._result(message, "failed")
            if decision != "completed":
                status = self._retry(
                    message, job, expected_version=int(claimed["version"]),
                    task_id=task_id, provider_status=polled.provider_status,
                    error_code="video_result_not_ready", error="video result is not ready",
                )
                return self._result(message, status)
            imported = asyncio.run(self.asset_importer.import_completed(task_id, polled))
        except VideoProviderDisabled as exc:
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_provider_disabled", error=exc,
            )
            return self._result(message, "failed")
        except ValueError as exc:
            self._fail(
                message, job, expected_version=int(claimed["version"]),
                error_code="video_asset_url_rejected", error=exc,
                error_category="unsafe_remote_url",
            )
            return self._result(message, "failed")
        except Exception as exc:
            status = self._retry(
                message, job, expected_version=int(claimed["version"]),
                task_id=task_id,
                provider_status=str(job.get("provider_status") or "completed"),
                error_code="video_asset_import_failed", error=exc,
            )
            return self._result(message, status)

        try:
            result = imported.result
            asset_url = "/generated/" + str(result["local_path"]).replace("\\", "/").rsplit("/", 1)[-1]
            with db_transaction(immediate=True) as conn:
                history_id = record_generation(
                    media_type="video",
                    prompt=str(job.get("prompt") or ""),
                    enhanced_prompt=None,
                    model=str(job.get("model") or ""),
                    status="completed",
                    result=result,
                    task_id=task_id,
                    provider="agnes_video",
                    request_model=str(job.get("model") or ""),
                    input_mode="queued_worker",
                    duration_ms=imported.duration_ms,
                    started_at=str(job.get("started_at") or now_iso()),
                    job_id=message.job_id,
                    conn=conn,
                )
                save_generated_asset(
                    media_type="video",
                    result=result,
                    prompt=str(job.get("prompt") or ""),
                    model=str(job.get("model") or ""),
                    provider="agnes_video",
                    duration_ms=imported.duration_ms,
                    job_id=message.job_id,
                    conn=conn,
                )
                transition_job_in_connection(
                    conn,
                    message.job_id,
                    expected_version=int(claimed["version"]),
                    status="succeeded",
                    stage="finalize",
                    output_json=video_output_summary(
                        task_id=task_id,
                        provider_status="completed",
                        asset_count=1,
                        asset_url=asset_url,
                        history_id=history_id,
                    ),
                    provider_status="completed",
                    duration_ms=imported.duration_ms,
                    retryable=0,
                    gateway_stage="asset_import",
                    completed_at=now_iso(),
                )
                finish_job_attempt(
                    job_id=message.job_id,
                    attempt_number=message.attempt,
                    status="succeeded",
                    completed_at=now_iso(),
                    detail={"history_id": history_id, "asset_count": 1},
                    conn=conn,
                )
                append_job_event(
                    message.job_id,
                    "worker_video_finalized",
                    {"attempt": message.attempt, "history_id": history_id, "asset_count": 1},
                    to_status="succeeded",
                    stage="finalize",
                    conn=conn,
                )
                upsert_video_task(
                    task_id,
                    str(job.get("prompt") or ""),
                    str(job.get("model") or ""),
                    "completed",
                    {
                        "task_id": task_id,
                        "status": "completed",
                        "video_url": asset_url,
                        "localized": True,
                        "duration_ms": imported.duration_ms,
                    },
                    duration_ms=imported.duration_ms,
                    conn=conn,
                )
            return self._result(message, "succeeded")
        except StaleJobVersion:
            return self._result(message, "stale")

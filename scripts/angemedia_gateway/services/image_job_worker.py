"""Durable image_generate stage handler."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..db.connection import db_transaction
from ..error_diagnostics import classify_provider_error
from ..helpers import now_iso
from ..job_sanitizer import sanitize_error_text
from ..queue.messages import JobStageMessage
from ..repositories.generations import record_generation
from ..repositories.job_attempts import create_job_attempt, finish_job_attempt, get_job_attempt
from ..repositories.job_events import append_job_event
from ..repositories.jobs import claim_job_attempt, transition_job_in_connection
from ..schemas import ImageRequest
from .generation_assets import safe_output_json, save_generated_asset
from .image_execution import ImageExecutionResult, ImageExecutionService, build_runtime_image_executor
from .image_job_admission import parse_image_job_payload
from .job_lifecycle import StaleJobVersion


class ImageAssetMissing(RuntimeError):
    pass


class ImageJobWorker:
    worker_kind = "celery"

    def __init__(
        self,
        *,
        executor: ImageExecutionService | Any | None = None,
    ) -> None:
        self.executor = executor or build_runtime_image_executor()

    def handle(self, message: JobStageMessage, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("kind") != "image" or message.stage != "image_generate":
            raise ValueError("image worker received incompatible job stage")
        try:
            payload = json.loads(str(job.get("input_json") or "{}"))
            request, plan = parse_image_job_payload(payload)
        except Exception as exc:
            return self._fail_without_execution(message, job, exc, "invalid_image_job_payload")

        started_at = now_iso()
        with db_transaction(immediate=True) as conn:
            if get_job_attempt(message.job_id, message.attempt, conn=conn) is not None:
                append_job_event(
                    message.job_id, "worker_duplicate_message",
                    {"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                    stage=message.stage, conn=conn,
                )
                return self._result(message, "duplicate")
            claimed = claim_job_attempt(
                conn,
                job_id=message.job_id,
                expected_version=int(job["version"]),
                stage=message.stage,
                attempt_count=message.attempt,
                worker_kind=self.worker_kind,
                provider=plan.provider,
                model=plan.model,
                started_at=started_at,
            )
            if claimed is None:
                return self._result(message, "stale")
            create_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                stage=message.stage,
                worker_kind=self.worker_kind,
                status="running",
                started_at=started_at,
                detail={"dispatch_id": message.dispatch_id, "trace_id": message.trace_id},
                conn=conn,
            )
            append_job_event(
                message.job_id, "worker_attempt_started",
                {"attempt": message.attempt, "dispatch_id": message.dispatch_id},
                from_status="queued", to_status="running", stage=message.stage, conn=conn,
            )

        try:
            execution = asyncio.run(self.executor.execute(request, plan))
            self._persist_success(
                message, request, execution, expected_version=int(claimed["version"])
            )
            return self._result(message, "succeeded")
        except StaleJobVersion:
            return self._result(message, "stale")
        except Exception as exc:
            try:
                error_code = "image_asset_missing" if isinstance(exc, ImageAssetMissing) else "image_provider_failure"
                self._persist_failure(
                    message, exc, error_code,
                    expected_version=int(claimed["version"]),
                )
            except StaleJobVersion:
                return self._result(message, "stale")
            return self._result(message, "failed")

    def _persist_success(
        self,
        message: JobStageMessage,
        request: ImageRequest,
        execution: ImageExecutionResult,
        *,
        expected_version: int,
    ) -> None:
        result = execution.result
        completed_at = now_iso()
        with db_transaction(immediate=True) as conn:
            record_id = record_generation(
                media_type="image",
                prompt=request.prompt,
                enhanced_prompt=None,
                model=execution.model,
                status="completed",
                result=result,
                provider=execution.provider,
                request_model=execution.request_model,
                input_mode=execution.input_mode,
                duration_ms=execution.duration_ms,
                started_at=execution.started_at,
                job_id=message.job_id,
                conn=conn,
            )
            asset_count = save_generated_asset(
                media_type="image",
                result=result,
                prompt=request.prompt,
                model=execution.model,
                provider=execution.provider,
                duration_ms=execution.duration_ms,
                job_id=message.job_id,
                conn=conn,
            )
            if asset_count <= 0:
                raise ImageAssetMissing("image generation completed but no local asset was imported")
            result["history_id"] = record_id
            transition_job_in_connection(
                conn,
                message.job_id,
                expected_version=expected_version,
                status="succeeded",
                output_json=safe_output_json(result),
                completed_at=completed_at,
                duration_ms=execution.duration_ms,
            )
            finish_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                status="succeeded",
                completed_at=completed_at,
                detail={"history_id": record_id, "asset_count": asset_count},
                conn=conn,
            )
            append_job_event(
                message.job_id,
                "worker_attempt_succeeded",
                {"attempt": message.attempt, "history_id": record_id},
                to_status="succeeded",
                stage="finalize",
                conn=conn,
            )

    def _persist_failure(
        self,
        message: JobStageMessage,
        exc: Exception,
        error_code: str,
        *,
        expected_version: int,
    ) -> None:
        detail = "; ".join(exc.errors) if hasattr(exc, "errors") else str(exc)
        safe_error = sanitize_error_text(detail) or type(exc).__name__
        classification = classify_provider_error(safe_error)
        completed_at = now_iso()
        with db_transaction(immediate=True) as conn:
            transition_job_in_connection(
                conn,
                message.job_id,
                expected_version=expected_version,
                status="failed",
                error_code=error_code,
                error_message=safe_error,
                error_category=classification["error_category"],
                human_hint=classification["human_hint"],
                retryable=1 if classification["retryable"] else 0,
                gateway_stage=classification["gateway_stage"],
                completed_at=completed_at,
            )
            finish_job_attempt(
                job_id=message.job_id,
                attempt_number=message.attempt,
                status="failed",
                completed_at=completed_at,
                error_code=error_code,
                error_message=safe_error,
                detail={"retryable": bool(classification["retryable"])},
                conn=conn,
            )
            append_job_event(
                message.job_id,
                "worker_attempt_failed",
                {"attempt": message.attempt, "error_code": error_code, "retryable": bool(classification["retryable"])},
                to_status="failed",
                stage="finalize",
                conn=conn,
            )

    def _fail_without_execution(
        self,
        message: JobStageMessage,
        job: dict[str, Any],
        exc: Exception,
        error_code: str,
    ) -> dict[str, Any]:
        started_at = now_iso()
        with db_transaction(immediate=True) as conn:
            if get_job_attempt(message.job_id, message.attempt, conn=conn) is not None:
                return self._result(message, "duplicate")
            claimed = claim_job_attempt(
                conn,
                job_id=message.job_id,
                expected_version=int(job["version"]),
                stage=message.stage,
                attempt_count=message.attempt,
                worker_kind=self.worker_kind,
                provider=str(job.get("provider") or "image"),
                model=str(job.get("model") or "") or None,
                started_at=started_at,
            )
            if claimed is None:
                return self._result(message, "stale")
            create_job_attempt(
                job_id=message.job_id, attempt_number=message.attempt,
                stage=message.stage, worker_kind=self.worker_kind, status="running",
                started_at=started_at, conn=conn,
            )
            append_job_event(
                message.job_id, "worker_image_payload_rejected",
                {"attempt": message.attempt, "error_code": error_code},
                from_status="queued", to_status="running", stage=message.stage, conn=conn,
            )
        self._persist_failure(
            message, exc, error_code, expected_version=int(claimed["version"])
        )
        return self._result(message, "failed")

    @staticmethod
    def _result(message: JobStageMessage, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "job_id": message.job_id,
            "stage": message.stage,
            "attempt": message.attempt,
            "dispatch_id": message.dispatch_id,
        }

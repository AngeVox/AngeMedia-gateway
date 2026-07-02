"""Video job finalization persistence helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..db.connection import db_transaction
from ..helpers import now_iso
from ..repositories.generations import record_generation
from ..repositories.job_attempts import finish_job_attempt
from ..repositories.job_events import append_job_event
from ..repositories.jobs import transition_job_in_connection
from ..repositories.video_tasks import upsert_video_task
from .generation_assets import save_generated_asset
from .video_polling import video_output_summary


@dataclass(frozen=True)
class ImportedVideoFinalization:
    job: dict[str, Any]
    job_id: str
    attempt: int
    task_id: str
    expected_version: int
    result: dict[str, Any]
    duration_ms: int


def finalize_imported_video(finalization: ImportedVideoFinalization) -> None:
    result = finalization.result
    job = finalization.job
    asset_url = "/generated/" + str(result["local_path"]).replace("\\", "/").rsplit("/", 1)[-1]
    with db_transaction(immediate=True) as conn:
        history_id = record_generation(
            media_type="video",
            prompt=str(job.get("prompt") or ""),
            enhanced_prompt=None,
            model=str(job.get("model") or ""),
            status="completed",
            result=result,
            task_id=finalization.task_id,
            provider="agnes_video",
            request_model=str(job.get("model") or ""),
            input_mode="queued_worker",
            duration_ms=finalization.duration_ms,
            started_at=str(job.get("started_at") or now_iso()),
            job_id=finalization.job_id,
            conn=conn,
        )
        save_generated_asset(
            media_type="video",
            result=result,
            prompt=str(job.get("prompt") or ""),
            model=str(job.get("model") or ""),
            provider="agnes_video",
            duration_ms=finalization.duration_ms,
            job_id=finalization.job_id,
            conn=conn,
        )
        transition_job_in_connection(
            conn,
            finalization.job_id,
            expected_version=finalization.expected_version,
            status="succeeded",
            stage="finalize",
            output_json=video_output_summary(
                task_id=finalization.task_id,
                provider_status="completed",
                asset_count=1,
                asset_url=asset_url,
                history_id=history_id,
            ),
            provider_status="completed",
            duration_ms=finalization.duration_ms,
            retryable=0,
            gateway_stage="asset_import",
            completed_at=now_iso(),
        )
        finish_job_attempt(
            job_id=finalization.job_id,
            attempt_number=finalization.attempt,
            status="succeeded",
            completed_at=now_iso(),
            detail={"history_id": history_id, "asset_count": 1},
            conn=conn,
        )
        append_job_event(
            finalization.job_id,
            "worker_video_finalized",
            {"attempt": finalization.attempt, "history_id": history_id, "asset_count": 1},
            to_status="succeeded",
            stage="finalize",
            conn=conn,
        )
        upsert_video_task(
            finalization.task_id,
            str(job.get("prompt") or ""),
            str(job.get("model") or ""),
            "completed",
            {
                "task_id": finalization.task_id,
                "status": "completed",
                "video_url": asset_url,
                "localized": True,
                "duration_ms": finalization.duration_ms,
            },
            duration_ms=finalization.duration_ms,
            conn=conn,
        )

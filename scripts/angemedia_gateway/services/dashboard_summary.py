"""Server-side Dashboard summaries for queued jobs and generated assets."""
from __future__ import annotations

from typing import Any

from ..job_sanitizer import sanitize_error_text
from ..presenters.assets import safe_asset_summary
from ..repositories.assets import count_assets, list_assets
from ..repositories.jobs import ACTIVE_JOB_STATUSES, VALID_JOB_STATUSES, count_jobs, list_jobs


class DashboardSummaryService:
    """Build a small sanitized dashboard payload without browser-side aggregation."""

    RECENT_JOB_LIMIT = 6
    RECENT_FAILURE_LIMIT = 4
    RECENT_ASSET_LIMIT = 6

    def summary(self) -> dict[str, Any]:
        recent_jobs = list_jobs(limit=self.RECENT_JOB_LIMIT, offset=0, sort="created_at_desc")
        failed_jobs = list_jobs(
            status="failed",
            limit=self.RECENT_FAILURE_LIMIT,
            offset=0,
            sort="updated_at_desc",
        )
        assets = list_assets(limit=self.RECENT_ASSET_LIMIT, offset=0)
        return {
            "queue": self._queue_summary(),
            "assets": self._asset_counts(),
            "recent_jobs": [self._safe_job_item(job) for job in recent_jobs],
            "recent_failed_jobs": [self._safe_failed_job(job) for job in failed_jobs],
            "recent_assets": [safe_asset_summary(asset) for asset in assets],
        }

    def _queue_summary(self) -> dict[str, Any]:
        status_counts = {
            status: count_jobs(status=status)
            for status in sorted(VALID_JOB_STATUSES)
        }
        kind_counts = {
            "image": count_jobs(kind="image"),
            "video": count_jobs(kind="video"),
        }
        return {
            "total": count_jobs(),
            "active_total": sum(status_counts.get(status, 0) for status in ACTIVE_JOB_STATUSES),
            "status_counts": status_counts,
            "kind_counts": kind_counts,
        }

    def _asset_counts(self) -> dict[str, int]:
        return {
            "total": count_assets(),
            "image": count_assets(media_type="image"),
            "video": count_assets(media_type="video"),
            "generated": count_assets(source="generated"),
            "upload": count_assets(source="upload"),
        }

    def _safe_job_item(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job.get("id"),
            "kind": job.get("kind"),
            "status": job.get("status"),
            "stage": sanitize_error_text(job.get("stage"), limit=128),
            "provider": sanitize_error_text(job.get("provider"), limit=256),
            "model": sanitize_error_text(job.get("model"), limit=256),
            "prompt": sanitize_error_text(job.get("prompt"), limit=300),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "duration_ms": job.get("duration_ms"),
            "error_category": sanitize_error_text(job.get("error_category"), limit=128),
            "human_hint": sanitize_error_text(job.get("human_hint"), limit=300),
            "retryable": _normalize_retryable(job.get("retryable")),
            "gateway_stage": sanitize_error_text(job.get("gateway_stage"), limit=128),
        }

    def _safe_failed_job(self, job: dict[str, Any]) -> dict[str, Any]:
        item = self._safe_job_item(job)
        item["error_code"] = sanitize_error_text(job.get("error_code"), limit=128)
        item["error_message"] = sanitize_error_text(job.get("error_message"), limit=300)
        return item


def _normalize_retryable(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


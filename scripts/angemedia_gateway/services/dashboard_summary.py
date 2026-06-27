"""Server-side Dashboard summaries for queued jobs, storage, and generated assets."""
from __future__ import annotations

import shutil
import string
import sys
from pathlib import Path
from typing import Any

from .. import config as C
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
            "storage": self._storage_summary(),
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

    def _storage_summary(self) -> dict[str, Any]:
        """Return storage capacity summaries without exposing configured paths."""
        usage_root = _existing_disk_root(C.OUTPUT_DIR)
        usage = shutil.disk_usage(usage_root)
        media = {
            "generated_bytes": _directory_size(C.OUTPUT_DIR),
            "uploads_bytes": _directory_size(C.UPLOAD_DIR),
        }
        media["total_bytes"] = media["generated_bytes"] + media["uploads_bytes"]
        percent = 0.0 if usage.total <= 0 else round((usage.used / usage.total) * 100, 1)
        return {
            "media_volume": {
                "label": _volume_label(usage_root),
                "total_bytes": int(usage.total),
                "used_bytes": int(usage.used),
                "free_bytes": int(usage.free),
                "used_percent": percent,
            },
            "media": media,
            "volumes": _storage_volumes(usage_root),
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


def _directory_size(path: Any) -> int:
    try:
        root = Path(path)
    except Exception:
        return 0
    if not root.exists() or not root.is_dir():
        return 0
    total = 0
    for item in root.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return int(total)


def _existing_disk_root(path: Any) -> Path:
    try:
        root = Path(path)
    except Exception:
        return Path.cwd()
    if root.exists():
        return root
    for parent in root.parents:
        if parent.exists():
            return parent
    return Path.cwd()


def _volume_label(path: Path) -> str:
    anchor = path.resolve().anchor or str(path)
    if sys.platform.startswith("win") and len(anchor) >= 2 and anchor[1] == ":":
        return anchor[:2].upper()
    return "media"


def _volume_item(path: Path) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent = 0.0 if usage.total <= 0 else round((usage.used / usage.total) * 100, 1)
    return {
        "label": _volume_label(path),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "used_percent": percent,
    }


def _storage_volumes(fallback: Path) -> list[dict[str, Any]]:
    if sys.platform.startswith("win"):
        items = []
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:/")
            if root.exists():
                item = _volume_item(root)
                if item is not None:
                    items.append(item)
        return items
    item = _volume_item(fallback)
    return [item] if item is not None else []

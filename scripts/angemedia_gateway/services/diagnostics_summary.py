"""Safe Admin diagnostics summary for Studio."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from .. import config as C
from ..db.connection import db_connect
from ..job_sanitizer import sanitize_error_text
from ..queue.celery_backend import CeleryQueueBackend
from ..queue.diagnostics import queue_diagnostics
from ..queue.settings import QueueSettings
from ..repositories.settings import BUILTIN_PROVIDER_CONFIG_KEYS, builtin_provider_enabled, list_custom_providers
from .dashboard_summary import DashboardSummaryService
from .maintenance_retention import retention_preview


class DiagnosticsSummaryService:
    """Build a small diagnostics payload without raw env/config/path exposure."""

    RECENT_DISPATCH_LIMIT = 5

    def __init__(self, dashboard: DashboardSummaryService | None = None) -> None:
        self.dashboard = dashboard or DashboardSummaryService()

    def summary(self) -> dict[str, Any]:
        dashboard = self.dashboard.summary()
        return {
            "health": {"status": "ok"},
            "runtime": self._runtime_summary(),
            "queue": self._queue_summary(dashboard),
            "database": self._database_summary(),
            "media": self._media_summary(),
            "providers": self._provider_summary(),
            "recent_failed_jobs": dashboard.get("recent_failed_jobs", []),
            "dispatches": self._dispatch_summary(),
            "maintenance": self._maintenance_summary(),
        }

    def _runtime_summary(self) -> dict[str, Any]:
        return {
            "app": "AngeMedia Gateway",
            "version": "v0.2.1",
        }

    def _queue_summary(self, dashboard: dict[str, Any]) -> dict[str, Any]:
        try:
            from ..queue.celery_app import celery_app

            settings = QueueSettings.from_env()
            broker = queue_diagnostics(CeleryQueueBackend(app=celery_app, settings=settings), settings)
        except Exception:
            broker = {
                "enabled": False,
                "backend": "unavailable",
                "healthy": False,
                "error_code": "queue_diagnostics_unavailable",
            }
        queue_counts = dashboard.get("queue", {}) if isinstance(dashboard, dict) else {}
        return {
            **broker,
            "active_total": int(queue_counts.get("active_total") or 0),
            "status_counts": dict(queue_counts.get("status_counts") or {}),
            "kind_counts": dict(queue_counts.get("kind_counts") or {}),
        }

    def _database_summary(self) -> dict[str, Any]:
        try:
            with closing(db_connect()) as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception:
            return {"state": "error", "configured": True, "reachable": False}
        return {"state": "ok", "configured": True, "reachable": True}

    def _media_summary(self) -> dict[str, Any]:
        return {
            "generated": self._path_summary(C.OUTPUT_DIR),
            "uploads": self._path_summary(C.UPLOAD_DIR),
        }

    def _path_summary(self, value: Any) -> dict[str, bool]:
        path = Path(value)
        exists = path.exists()
        return {
            "configured": True,
            "exists": exists,
            "writable": exists and path.is_dir(),
        }

    def _provider_summary(self) -> dict[str, Any]:
        custom = [self._custom_provider_item(item) for item in list_custom_providers(include_secret=False)]
        builtins = [
            {"id": provider_id, "enabled": builtin_provider_enabled(provider_id)}
            for provider_id in sorted(BUILTIN_PROVIDER_CONFIG_KEYS)
        ]
        return {
            "builtin": builtins,
            "custom": {
                "total": len(custom),
                "enabled": sum(1 for item in custom if item.get("enabled")),
            },
            "custom_providers": custom[:10],
        }

    def _custom_provider_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": sanitize_error_text(item.get("id"), limit=80),
            "name": sanitize_error_text(item.get("name"), limit=120),
            "provider_type": sanitize_error_text(item.get("provider_type"), limit=80),
            "default_model": sanitize_error_text(item.get("default_model"), limit=120),
            "enabled": bool(item.get("enabled")),
            "last_test_status": sanitize_error_text(item.get("last_test_status"), limit=80),
            "last_test_at": item.get("last_test_at"),
        }

    def _dispatch_summary(self) -> dict[str, Any]:
        with closing(db_connect()) as conn:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS total FROM job_dispatches GROUP BY status"
            ).fetchall()
            recent = conn.execute(
                "SELECT id,job_id,topic,status,attempt_count,last_error,updated_at "
                "FROM job_dispatches WHERE last_error IS NOT NULL OR status='failed' "
                "ORDER BY updated_at DESC,id DESC LIMIT ?",
                (self.RECENT_DISPATCH_LIMIT,),
            ).fetchall()
        return {
            "status_counts": {str(row["status"]): int(row["total"]) for row in rows},
            "recent_errors": [self._dispatch_error_item(dict(row)) for row in recent],
        }

    def _dispatch_error_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "job_id": item.get("job_id"),
            "topic": sanitize_error_text(item.get("topic"), limit=120),
            "status": sanitize_error_text(item.get("status"), limit=40),
            "attempt_count": int(item.get("attempt_count") or 0),
            "last_error": sanitize_error_text(item.get("last_error"), limit=240),
            "updated_at": item.get("updated_at"),
        }

    def _maintenance_summary(self) -> dict[str, Any]:
        try:
            preview = retention_preview({"older_than_days": 30, "limit": 500})
        except Exception:
            return {"state": "unavailable"}
        return {
            "state": "ok",
            "older_than_days": preview.get("older_than_days"),
            "jobs": preview.get("jobs", {}),
            "assistant": preview.get("assistant", {}),
            "assets_deleted": 0,
            "media_files_deleted": 0,
        }

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.db.schema import init_db  # noqa: E402
from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user  # noqa: E402
from angemedia_gateway.repositories.gateway_keys import create_gateway_api_key, revoke_gateway_api_key  # noqa: E402
from angemedia_gateway.repositories.jobs import create_job, get_job  # noqa: E402
from angemedia_gateway.routes.jobs import _job_list_item  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.services.video_job_refresh import (  # noqa: E402
    VideoJobRefreshError,
    VideoJobRefreshService,
)


class VideoJobRefreshServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="video-refresh-test-")
        self.db_path = Path(self.tmp_dir) / "test.db"
        self.output_dir = Path(self.tmp_dir) / "output"
        self.upload_dir = Path(self.tmp_dir) / "upload"
        self.output_dir.mkdir()
        self.upload_dir.mkdir()
        self.original_db = C.DB_FILE
        self.original_output = C.OUTPUT_DIR
        self.original_upload = C.UPLOAD_DIR
        self.original_base = C.PUBLIC_BASE_URL
        C.DB_FILE = self.db_path
        C.OUTPUT_DIR = self.output_dir
        C.UPLOAD_DIR = self.upload_dir
        C.PUBLIC_BASE_URL = "http://testserver"
        init_db()

    def tearDown(self) -> None:
        C.DB_FILE = self.original_db
        C.OUTPUT_DIR = self.original_output
        C.UPLOAD_DIR = self.original_upload
        C.PUBLIC_BASE_URL = self.original_base
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def create_video_job(self, *, provider: str = "agnes_video", status: str = "running") -> dict:
        job = create_job(
            kind="video",
            status=status,
            provider=provider,
            model="agnes-video-v2.0",
            prompt="refresh test",
            external_task_id="refresh-task-001",
            started_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        )
        old = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old, job["id"]))
        return get_job(job["id"]) or job

    def service(
        self,
        poll_result: dict,
        *,
        interval: float = 0,
        localized_result: dict | None = None,
    ) -> tuple[VideoJobRefreshService, AsyncMock]:
        poll = AsyncMock(return_value=poll_result)
        localize = AsyncMock(return_value=localized_result) if localized_result is not None else None
        service = VideoJobRefreshService(
            poll_task_func=poll,
            **({"localize_video_result_func": localize} if localize is not None else {}),
            min_poll_interval_seconds=interval,
        )
        return service, poll

    async def test_unknown_non_video_and_unsupported_jobs_do_not_poll(self) -> None:
        service, poll = self.service({"status": "running"})
        with self.assertRaises(VideoJobRefreshError) as missing:
            await service.refresh("missing")
        self.assertEqual(missing.exception.status_code, 404)

        image = create_job(kind="image", status="running", provider="agnes_video", prompt="image")
        with self.assertRaises(VideoJobRefreshError) as wrong_kind:
            await service.refresh(image["id"])
        self.assertEqual(wrong_kind.exception.status_code, 400)

        other = self.create_video_job(provider="other_video")
        result = await service.refresh(other["id"])
        self.assertEqual(result["refresh_status"], "unsupported")
        poll.assert_not_awaited()

    async def test_running_polls_once_and_interval_throttles_repeat(self) -> None:
        job = self.create_video_job()
        service, poll = self.service({"task_id": "refresh-task-001", "status": "running"}, interval=30)
        first = await service.refresh(job["id"])
        second = await service.refresh(job["id"])
        self.assertTrue(first["polled"])
        self.assertEqual(first["provider_status"], "running")
        self.assertEqual(_job_list_item(get_job(job["id"]))["provider_status"], "running")
        self.assertEqual(second["refresh_status"], "throttled")
        self.assertFalse(second["polled"])
        poll.assert_awaited_once_with("refresh-task-001")

    async def test_missing_task_id_and_poll_failure_are_safe(self) -> None:
        missing_task = create_job(
            kind="video",
            status="running",
            provider="agnes_video",
            model="agnes-video-v2.0",
            prompt="missing task",
        )
        service, poll = self.service({"status": "running"})
        with self.assertRaises(VideoJobRefreshError) as missing:
            await service.refresh(missing_task["id"])
        self.assertEqual(missing.exception.status_code, 409)
        poll.assert_not_awaited()

        job = self.create_video_job()
        secret = "sk-poll-error-do-not-store"
        failing_poll = AsyncMock(side_effect=RuntimeError(f"Bearer {secret}"))
        failing_service = VideoJobRefreshService(
            poll_task_func=failing_poll,
            min_poll_interval_seconds=0,
        )
        with self.assertRaises(VideoJobRefreshError) as failed:
            await failing_service.refresh(job["id"])
        self.assertEqual(failed.exception.status_code, 502)
        refreshed = get_job(job["id"])
        self.assertEqual(refreshed["status"], "running")
        self.assertEqual(refreshed["retryable"], 1)
        self.assertNotIn(secret, refreshed["error_message"])

    async def test_completed_download_creates_asset_and_succeeds(self) -> None:
        job = self.create_video_job()
        local_file = self.output_dir / "video-refresh.mp4"
        local_file.write_bytes(b"video-data")
        service, _ = self.service({
            "task_id": "refresh-task-001",
            "status": "completed",
            "video_url": "https://cdn.example/video.mp4?token=private",
        }, localized_result={
            "task_id": "refresh-task-001",
            "status": "completed",
            "video_url": "http://testserver/generated/video-refresh.mp4",
            "remote_video_url": "https://cdn.example/video.mp4?token=private",
            "local_path": str(local_file),
            "localized": True,
        })
        result = await service.refresh(job["id"])
        refreshed = get_job(job["id"])
        self.assertEqual(refreshed["status"], "succeeded")
        self.assertEqual(result["provider_status"], "completed")
        self.assertTrue(result["asset_url"].startswith("/generated/"))
        with sqlite3.connect(self.db_path) as conn:
            asset = conn.execute("SELECT job_id,url_path FROM assets WHERE job_id = ?", (job["id"],)).fetchone()
        self.assertIsNotNone(asset)
        self.assertEqual(asset[0], job["id"])

    async def test_failed_status_is_redacted(self) -> None:
        job = self.create_video_job()
        service, _ = self.service({
            "task_id": "refresh-task-001",
            "status": "failed",
            "error": "Authorization: Bearer refresh-secret-do-not-store",
        })
        result = await service.refresh(job["id"])
        refreshed = get_job(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("refresh-secret-do-not-store", refreshed["error_message"])

    async def test_private_result_url_is_not_downloaded_or_completed(self) -> None:
        job = self.create_video_job()
        service, _ = self.service({
            "task_id": "refresh-task-001",
            "status": "completed",
            "video_url": "http://127.0.0.1/private.mp4",
        })
        result = await service.refresh(job["id"])
        refreshed = get_job(job["id"])
        self.assertEqual(result["refresh_status"], "download_pending")
        self.assertEqual(refreshed["status"], "running")
        self.assertEqual(refreshed["retryable"], 1)
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM assets WHERE job_id = ?", (job["id"],)).fetchone()[0]
        self.assertEqual(count, 0)

    async def test_raw_provider_fields_and_api_key_are_not_persisted(self) -> None:
        job = self.create_video_job()
        local_file = self.output_dir / "video-safe.mp4"
        local_file.write_bytes(b"safe-video")
        secret = "sk-refresh-secret-do-not-store"
        service, _ = self.service({
            "task_id": "refresh-task-001",
            "status": "completed",
            "video_url": "https://cdn.example/video.mp4?token=provider-secret",
            "api_key": secret,
            "Authorization": f"Bearer {secret}",
            "raw_provider_body": {"secret": secret},
            "local_path": "D:/must-not-be-trusted.mp4",
            "localized": True,
        }, localized_result={
            "task_id": "refresh-task-001",
            "status": "completed",
            "video_url": "http://testserver/generated/video-safe.mp4",
            "remote_video_url": "https://cdn.example/video.mp4?token=provider-secret",
            "local_path": str(local_file),
            "localized": True,
        })
        await service.refresh(job["id"])
        refreshed = get_job(job["id"])
        with sqlite3.connect(self.db_path) as conn:
            raw_json = conn.execute(
                "SELECT raw_json FROM video_tasks WHERE task_id = 'refresh-task-001'"
            ).fetchone()[0]
        persisted = f"{refreshed['output_json']} {raw_json}"
        self.assertNotIn(secret, persisted)
        self.assertNotIn("Authorization", persisted)
        self.assertNotIn("provider-secret", persisted)
        self.assertNotIn("must-not-be-trusted", persisted)


class VideoJobRefreshApiAuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="video-refresh-api-test-")
        self.db_path = Path(self.tmp_dir) / "test.db"
        self.original_db = C.DB_FILE
        C.DB_FILE = self.db_path
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        login = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def tearDown(self) -> None:
        C.DB_FILE = self.original_db
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_refresh_requires_session_and_rejects_gateway_key(self) -> None:
        path = "/v1/admin/jobs/missing/refresh"
        anonymous = TestClient(app).post(path)
        self.assertEqual(anonymous.status_code, 401, anonymous.text)

        gateway_key = create_gateway_api_key(name="video-refresh-admin-boundary")
        try:
            gateway = TestClient(app).post(
                path,
                headers={"Authorization": f"Bearer {gateway_key['key']}"},
            )
            self.assertEqual(gateway.status_code, 403, gateway.text)
            self.assertNotIn(gateway_key["key"], gateway.text)
        finally:
            revoke_gateway_api_key(gateway_key["id"])

        missing = self.client.post(path)
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_session_refresh_response_is_safe(self) -> None:
        safe_result = {
            "job_id": "job-1",
            "status": "running",
            "provider_status": "running",
            "refresh_status": "polled",
            "polled": True,
            "asset_url": None,
        }
        with patch(
            "angemedia_gateway.routes.admin.video_job_refresh_service.refresh",
            AsyncMock(return_value=safe_result),
        ):
            response = self.client.post("/v1/admin/jobs/job-1/refresh")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], safe_result)
        self.assertNotIn("Authorization", response.text)


if __name__ == "__main__":
    unittest.main()

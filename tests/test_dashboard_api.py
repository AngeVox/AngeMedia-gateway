"""Admin dashboard queue summary API tests."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import (  # noqa: E402
    create_gateway_api_key,
    create_job,
    ensure_default_admin_user,
    init_db,
    record_generation,
    save_asset,
    update_job_status,
)


class DashboardSummaryApiTest(unittest.TestCase):
    """Admin dashboard summary should be server-side and sanitized."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="dashboard-api-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_gateway_key = C.GATEWAY_API_KEY

        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.GATEWAY_API_KEY = ""
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.GATEWAY_API_KEY = self._orig_gateway_key
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_summary_requires_admin_session(self) -> None:
        response = self.client.get("/v1/admin/dashboard/summary")
        self.assertEqual(response.status_code, 401)

    def test_gateway_api_key_cannot_access_summary(self) -> None:
        item = create_gateway_api_key(name="dashboard-test")
        response = self.client.get(
            "/v1/admin/dashboard/summary",
            headers={"Authorization": f"Bearer {item['key']}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_summary_returns_queue_counts_safely(self) -> None:
        create_job(kind="image", status="queued", prompt="q")
        create_job(kind="image", status="running", prompt="r")
        create_job(kind="video", status="succeeded", prompt="s")
        create_job(kind="video", status="failed", prompt="f", error_message="failed")

        self.login_admin()
        response = self.client.get("/v1/admin/dashboard/summary")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]

        self.assertEqual(data["queue"]["active_total"], 2)
        self.assertEqual(data["queue"]["status_counts"]["queued"], 1)
        self.assertEqual(data["queue"]["status_counts"]["running"], 1)
        self.assertEqual(data["queue"]["status_counts"]["succeeded"], 1)
        self.assertEqual(data["queue"]["status_counts"]["failed"], 1)
        self.assertEqual(data["queue"]["kind_counts"]["image"], 2)
        self.assertEqual(data["queue"]["kind_counts"]["video"], 2)
        self.assertGreaterEqual(len(data["recent_jobs"]), 1)

    def test_summary_sanitizes_failed_job_diagnostics_and_raw_fields(self) -> None:
        failed = create_job(
            kind="image",
            status="failed",
            prompt="unsafe",
            input_json='{"api_key":"sk-dashboard-input-secret-123456"}',
            output_json='{"provider_body":{"Authorization":"Bearer dashboard-token-123456"}}',
            request_hash="a" * 64,
            request_hash_version=1,
            error_message="provider leaked sk-dashboard-error-secret-123456",
        )
        update_job_status(
            failed["id"],
            status="failed",
            error_category="provider_error",
            human_hint="Authorization Bearer dashboard-hint-token-123456",
            retryable=1,
            gateway_stage="provider_response",
        )

        self.login_admin()
        response = self.client.get("/v1/admin/dashboard/summary")
        self.assertEqual(response.status_code, 200, response.text)
        body = json.dumps(response.json()["data"], ensure_ascii=False)

        self.assertNotIn("input_json", body)
        self.assertNotIn("output_json", body)
        self.assertNotIn("request_hash", body)
        self.assertNotIn("sk-dashboard-input-secret-123456", body)
        self.assertNotIn("dashboard-token-123456", body)
        self.assertNotIn("sk-dashboard-error-secret-123456", body)
        self.assertNotIn("dashboard-hint-token-123456", body)
        self.assertIn("REDACTED", body)

    def test_summary_includes_recent_asset_summary(self) -> None:
        job = create_job(kind="image", status="succeeded", provider="p1", model="m1")
        save_asset(
            id="asset-dash-1",
            filename="dash.png",
            storage_area="output",
            relative_path="dash.png",
            url_path="/generated/dash.png",
            media_type="image",
            source="generated",
            provider="p1",
            model="m1",
            job_id=job["id"],
        )
        record_generation(
            media_type="image",
            prompt="dash",
            enhanced_prompt=None,
            model="m1",
            status="completed",
            result={"data": [{"url": "https://provider.test/dash.png?token=secret"}]},
            provider="p1",
            duration_ms=10,
            job_id=job["id"],
        )

        self.login_admin()
        response = self.client.get("/v1/admin/dashboard/summary")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        asset = data["recent_assets"][0]

        self.assertEqual(asset["id"], "asset-dash-1")
        self.assertEqual(asset["url_path"], "/generated/dash.png")
        self.assertEqual(asset["job"]["job_id"], job["id"])
        self.assertEqual(asset["generation"]["media_type"], "image")
        body = json.dumps(asset, ensure_ascii=False)
        self.assertNotIn("storage_area", body)
        self.assertNotIn("relative_path", body)
        self.assertNotIn("token=secret", body)


if __name__ == "__main__":
    unittest.main()

"""Admin diagnostics summary API tests."""
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
    update_job_status,
)
from angemedia_gateway.repositories.settings import upsert_custom_provider  # noqa: E402


class DiagnosticsSummaryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="diagnostics-api-test-")
        self._db_path = Path(self._tmp_dir) / "secret-test.db"
        self._output_dir = Path(self._tmp_dir) / "absolute-output"
        self._upload_dir = Path(self._tmp_dir) / "absolute-upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_gateway_key = C.GATEWAY_API_KEY

        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.GATEWAY_API_KEY = "am-diagnostics-legacy-secret"
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
        response = self.client.get("/v1/admin/diagnostics/summary")
        self.assertEqual(response.status_code, 401)

    def test_gateway_api_key_cannot_access_summary(self) -> None:
        key = create_gateway_api_key(name="diagnostics")
        response = self.client.get(
            "/v1/admin/diagnostics/summary",
            headers={"Authorization": f"Bearer {key['key']}"},
        )
        self.assertEqual(response.status_code, 403)
        legacy = self.client.get(
            "/v1/admin/diagnostics/summary",
            headers={"Authorization": "Bearer am-diagnostics-legacy-secret"},
        )
        self.assertEqual(legacy.status_code, 403)

    def test_summary_returns_safe_sections(self) -> None:
        failed = create_job(
            kind="image",
            status="failed",
            prompt="diag",
            input_json='{"Authorization":"Bearer diag-secret-token-123456"}',
            output_json='{"provider_body":{"api_key":"sk-diagnostics-output-secret"}}',
            request_hash="c" * 64,
            request_hash_version=1,
            error_message="failed with sk-diagnostics-error-secret",
        )
        update_job_status(
            failed["id"],
            status="failed",
            error_category="provider_error",
            human_hint="Check provider route",
            retryable=1,
            gateway_stage="provider_response",
        )
        upsert_custom_provider({
            "id": "diag-provider",
            "name": "Diagnostic Provider",
            "provider_type": "openai_image",
            "base_url": "https://provider.example.test/v1",
            "api_key": "sk-diagnostics-provider-secret",
            "default_model": "diag-model",
            "enabled": True,
        })

        self.login_admin()
        response = self.client.get("/v1/admin/diagnostics/summary")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]

        for key in ("health", "queue", "runtime", "database", "media", "providers", "recent_failed_jobs", "dispatches"):
            self.assertIn(key, data)
        self.assertEqual(data["health"]["status"], "ok")
        self.assertIn("backend", data["queue"])
        self.assertIn("state", data["database"])
        self.assertIn("generated", data["media"])
        self.assertGreaterEqual(data["providers"]["custom"]["total"], 1)

        body = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("input_json", body)
        self.assertNotIn("output_json", body)
        self.assertNotIn("request_hash", body)
        self.assertNotIn("sk-diagnostics", body)
        self.assertNotIn("Bearer diag-secret", body)
        self.assertNotIn(str(self._tmp_dir), body)
        self.assertNotIn(str(self._db_path), body)
        self.assertNotIn("base_url", body)
        self.assertNotIn("api_key", body)


if __name__ == "__main__":
    unittest.main()

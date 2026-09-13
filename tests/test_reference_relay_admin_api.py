from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user
from angemedia_gateway.server import app


class ReferenceRelayAdminApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="relay-api-")
        self._original_db = C.DB_FILE
        C.DB_FILE = Path(self._tmp.name) / "angemedia.db"
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        login = self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        self.assertEqual(login.status_code, 200, login.text)

    def tearDown(self) -> None:
        self.client.close()
        C.DB_FILE = self._original_db
        self._tmp.cleanup()

    def test_save_read_are_safe(self) -> None:
        url = "http://192.168.1.10:8787/upload"
        token = "relay-test-token"
        saved = self.client.post(
            "/v1/admin/reference-relay",
            json={"mode": "external_http", "upload_url": url, "token": token},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        data = saved.json()["data"]
        self.assertTrue(data["configured"])
        self.assertTrue(data["upload_url_configured"])
        self.assertTrue(data["token_configured"])
        self.assertNotIn(url, saved.text)
        self.assertNotIn(token, saved.text)

        read = self.client.get("/v1/admin/reference-relay")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertNotIn(url, read.text)
        self.assertNotIn(token, read.text)

    def test_invalid_or_extra_config_is_rejected(self) -> None:
        bad = self.client.post("/v1/admin/reference-relay", json={"mode": "external_http"})
        self.assertEqual(bad.status_code, 400, bad.text)
        metadata = self.client.post(
            "/v1/admin/reference-relay",
            json={"mode": "external_http", "upload_url": "http://169.254.169.254/latest/meta-data"},
        )
        self.assertEqual(metadata.status_code, 400, metadata.text)
        extra = self.client.post("/v1/admin/reference-relay", json={"mode": "disabled", "unexpected": True})
        self.assertEqual(extra.status_code, 422, extra.text)


if __name__ == "__main__":
    unittest.main()

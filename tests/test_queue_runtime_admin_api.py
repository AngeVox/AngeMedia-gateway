"""Admin API contracts for managed queue runtime controls."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402
import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import ensure_default_admin_user, init_db  # noqa: E402


class QueueRuntimeAdminApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="queue-runtime-api-"))
        self.orig_db = C.DB_FILE
        C.DB_FILE = self.tmp / "test.db"
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self.orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self) -> None:
        response = self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_queue_runtime_requires_admin_session(self) -> None:
        self.assertEqual(self.client.get("/v1/admin/system/queue").status_code, 401)
        self.assertEqual(self.client.post("/v1/admin/system/queue/redis/detect", json={}).status_code, 401)
        self.assertEqual(self.client.post("/v1/admin/system/queue/switch", json={"backend": "local"}).status_code, 401)

    def test_queue_runtime_summary_never_contains_broker_url(self) -> None:
        self.login()
        safe = {
            "backend": "local", "enabled": True, "can_switch": True, "platform": "fnos",
            "redis_configured": True, "active_jobs": 0, "active_dispatches": 0,
            "processes": {"dispatcher": True, "worker": False},
        }
        with patch("angemedia_gateway.routes.admin.queue_runtime_service.summary", return_value=safe):
            response = self.client.get("/v1/admin/system/queue")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("redis://", response.text)
        self.assertNotIn("broker_url", response.text)

    def test_redis_detect_returns_safe_candidate_only(self) -> None:
        self.login()
        result = {
            "detected": True,
            "recommended_source": "manual",
            "candidates": [{
                "source": "manual", "reachable": True, "host": "127.0.0.1", "port": 6379,
                "tls": False, "credentials_configured": True,
            }],
        }
        with patch("angemedia_gateway.routes.admin.queue_runtime_service.detect_redis", return_value=result):
            response = self.client.post(
                "/v1/admin/system/queue/redis/detect",
                json={"redis_url": "redis://:dummy-secret@127.0.0.1:6379/0"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("dummy-secret", response.text)
        self.assertNotIn("redis_url", response.text)


if __name__ == "__main__":
    unittest.main()

"""Scoped assistant chat API contracts."""
from __future__ import annotations

import os
import shutil
import sqlite3
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
from angemedia_gateway.state import create_gateway_api_key, ensure_default_admin_user, init_db  # noqa: E402


class AssistantChatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="assistant-chat-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        self._orig_gateway_key = C.GATEWAY_API_KEY
        C.DB_FILE = self._db_path
        C.GATEWAY_API_KEY = ""
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.GATEWAY_API_KEY = self._orig_gateway_key
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        response = self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        self.assertEqual(response.status_code, 200, response.text)

    def gateway_headers(self) -> dict[str, str]:
        item = create_gateway_api_key(name="assistant-chat")
        return {"Authorization": f"Bearer {item['key']}"}

    def assert_safe(self, text: str) -> None:
        for marker in (
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            "Authorization",
            "Bearer leak-token",
            "request_hash",
            "provider_raw_body",
            "C:\\Users\\admin\\secret.png",
            "data:image/png;base64",
        ):
            self.assertNotIn(marker, text)

    def test_assistant_chat_requires_admin_session(self) -> None:
        response = self.client.post("/v1/assistant/chat", json={"message": "AngeMedia Jobs 怎么看"})
        self.assertEqual(response.status_code, 401)

    def test_gateway_key_cannot_access_assistant_chat_or_sessions(self) -> None:
        headers = self.gateway_headers()
        chat = self.client.post("/v1/assistant/chat", json={"message": "AngeMedia Jobs"}, headers=headers)
        sessions = self.client.get("/v1/admin/assistant/sessions", headers=headers)
        self.assertEqual(chat.status_code, 403, chat.text)
        self.assertEqual(sessions.status_code, 403, sessions.text)

    def test_in_scope_question_returns_kb_answer_timeline_and_persists(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/chat",
            json={"message": "AngeMedia 视频任务超时应该怎么看？", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertIn("session_id", body)
        self.assertIn("timeline", body)
        self.assertIn("local_kb_search", response.text)
        self.assertIn("AngeMedia", body["answer"])
        self.assertGreaterEqual(len(body["messages"]), 2)
        self.assert_safe(response.text)

        sessions = self.client.get("/v1/admin/assistant/sessions")
        self.assertEqual(sessions.status_code, 200, sessions.text)
        self.assertEqual(sessions.json()["total"], 1)

        detail = self.client.get(f"/v1/admin/assistant/sessions/{body['session_id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(len(detail.json()["messages"]), 2)

        with sqlite3.connect(str(self._db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assistant_runs").fetchone()[0], 1)

    def test_out_of_scope_question_is_refused_and_stored_as_refused_run(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/assistant/chat", json={"message": "帮我写一首情诗", "language": "zh"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "refused")
        self.assertIn("超出范围", body["answer"])
        self.assertIn("scope_guard", response.text)
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute("SELECT status FROM assistant_runs").fetchone()
        self.assertEqual(row[0], "refused")

    def test_sensitive_input_is_sanitized(self) -> None:
        self.login_admin()
        polluted = (
            "AngeMedia Jobs 怎么看 Authorization: Bearer leak-token "
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR request_hash=abc "
            "provider_raw_body C:\\Users\\admin\\secret.png data:image/png;base64,AAAA"
        )
        response = self.client.post("/v1/assistant/chat", json={"message": polluted})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_safe(response.text)


if __name__ == "__main__":
    unittest.main()

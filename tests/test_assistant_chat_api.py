"""Scoped assistant chat API contracts."""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.repositories.settings import set_config_many  # noqa: E402
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
        stream = self.client.post("/v1/assistant/chat/stream", json={"message": "AngeMedia Jobs"}, headers=headers)
        sessions = self.client.get("/v1/admin/assistant/sessions", headers=headers)
        self.assertEqual(chat.status_code, 403, chat.text)
        self.assertEqual(stream.status_code, 403, stream.text)
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

    def test_stream_chat_returns_sse_events(self) -> None:
        self.login_admin()
        response = self.client.post(
            "/v1/assistant/chat/stream",
            json={"message": "你好", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))
        self.assertIn("event: chunk", response.text)
        self.assertIn("event: done", response.text)
        self.assertIn("AngeMedia", response.text)
        self.assert_safe(response.text)

    def test_stream_route_never_exposes_upstream_exception_details(self) -> None:
        self.login_admin()

        async def exploding_stream(_payload):
            raise RuntimeError(
                "Traceback /srv/private/app.py SELECT secret FROM users "
                "sk-LEAKED-SECRET-MUST-NOT-APPEAR"
            )
            yield ""

        with patch(
            "angemedia_gateway.routes.media.build_assistant_chat_stream",
            new=exploding_stream,
        ):
            response = self.client.post(
                "/v1/assistant/chat/stream",
                json={"message": "任务超时怎么办？", "language": "zh"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("event: error", response.text)
        self.assertIn("assistant_service_unavailable", response.text)
        for marker in ("Traceback", "/srv/private/app.py", "SELECT secret", "sk-LEAKED"):
            self.assertNotIn(marker, response.text)

    def test_non_stream_llm_failure_does_not_expose_exception_details(self) -> None:
        self.login_admin()
        set_config_many(
            {
                "ANGE_ASSISTANT_ENABLED": "true",
                "ANGE_LLM_BASE_URL": "http://llm.local/v1",
                "ANGE_LLM_MODEL": "test-chat-model",
                "ANGE_LLM_API_KEY": "sk-test-secret",
            }
        )
        leaked = (
            "Traceback /srv/private/app.py SELECT secret FROM users "
            "sk-LEAKED-SECRET-MUST-NOT-APPEAR"
        )
        with patch(
            "angemedia_gateway.services.assistant_chat_service._call_llm_chat",
            new=AsyncMock(side_effect=RuntimeError(leaked)),
        ):
            response = self.client.post(
                "/v1/assistant/chat",
                json={"message": "任务超时怎么办？", "language": "zh"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("LLM 调用失败", response.text)
        for marker in ("Traceback", "/srv/private/app.py", "SELECT secret", "sk-LEAKED"):
            self.assertNotIn(marker, response.text)

    def test_stream_chat_cleans_markdown_tokens(self) -> None:
        self.login_admin()
        set_config_many(
            {
                "ANGE_ASSISTANT_ENABLED": "true",
                "ANGE_LLM_BASE_URL": "http://llm.local/v1",
                "ANGE_LLM_MODEL": "test-chat-model",
                "ANGE_LLM_API_KEY": "sk-test-secret",
            }
        )

        async def fake_stream(*_args, **_kwargs):
            for chunk in ("## 标题\n", "1. **查看 Jobs**\n", "| raw | table |"):
                yield chunk

        with patch("angemedia_gateway.services.assistant_chat_service._stream_llm_chat", new=fake_stream):
            response = self.client.post(
                "/v1/assistant/chat/stream",
                json={"message": "任务超时怎么办？", "language": "zh"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("event: chunk", response.text)
        self.assertNotIn("##", response.text)
        self.assertNotIn("**", response.text)
        self.assertNotIn("|", response.text)
        self.assert_safe(response.text)

    def test_stream_chat_partial_llm_does_not_append_local_fallback(self) -> None:
        self.login_admin()
        set_config_many(
            {
                "ANGE_ASSISTANT_ENABLED": "true",
                "ANGE_LLM_BASE_URL": "http://llm.local/v1",
                "ANGE_LLM_MODEL": "test-chat-model",
                "ANGE_LLM_API_KEY": "sk-test-secret",
            }
        )

        async def partial_stream(*_args, **_kwargs):
            yield "任务超时时先查看 Jobs 详情。"
            raise RuntimeError("stream interrupted")

        with patch("angemedia_gateway.services.assistant_chat_service._stream_llm_chat", new=partial_stream):
            response = self.client.post(
                "/v1/assistant/chat/stream",
                json={"message": "任务超时怎么办？", "language": "zh"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("任务超时时先查看 Jobs 详情", response.text)
        self.assertIn("partial", response.text)
        self.assertNotIn("我只找到了有限的本地知识", response.text)
        self.assert_safe(response.text)

    def test_delete_assistant_session_removes_messages_and_runs(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/assistant/chat", json={"message": "你好", "language": "zh"})
        self.assertEqual(response.status_code, 200, response.text)
        session_id = response.json()["session_id"]
        deleted = self.client.delete(f"/v1/admin/assistant/sessions/{session_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        missing = self.client.get(f"/v1/admin/assistant/sessions/{session_id}")
        self.assertEqual(missing.status_code, 404)
        with sqlite3.connect(str(self._db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assistant_messages").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assistant_runs").fetchone()[0], 0)

    def test_short_identity_question_is_allowed(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/assistant/chat", json={"message": "你是什么模型？", "language": "zh"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.json()["status"], "refused")

    def test_in_scope_question_uses_configured_llm_before_local_fallback(self) -> None:
        self.login_admin()
        set_config_many(
            {
                "ANGE_ASSISTANT_ENABLED": "true",
                "ANGE_LLM_BASE_URL": "http://llm.local/v1",
                "ANGE_LLM_MODEL": "test-chat-model",
                "ANGE_LLM_API_KEY": "sk-test-secret",
            }
        )
        with patch(
            "angemedia_gateway.services.assistant_chat_service._call_llm_chat",
            new=AsyncMock(return_value=("这是 LLM 对图片失败诊断的回答。", 12)),
        ) as mocked:
            response = self.client.post(
                "/v1/assistant/chat",
                json={"message": "图片失败怎么查看原因？", "language": "zh"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertIn("LLM", body["answer"])
        self.assertIn("llm_chat", response.text)
        mocked.assert_awaited_once()
        self.assert_safe(response.text)

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

    def test_greeting_is_not_rejected(self) -> None:
        self.login_admin()
        response = self.client.post("/v1/assistant/chat", json={"message": "你好", "language": "zh"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertIn("AngeMedia", body["answer"])
        self.assertIn("问候已放行", response.text)

    def test_job_id_chat_executes_safe_job_tools_and_persists_only_sanitized_context(self) -> None:
        from angemedia_gateway.repositories.jobs import transition_job
        from angemedia_gateway.services.job_admission import JobAdmissionService

        self.login_admin()
        admitted = JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="f" * 64,
            request_hash_version=1,
            payload={
                "prompt": "private prompt must not appear",
                "api_key": "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
                "local_path": "/root/private/input.png",
            },
            provider="siliconflow",
            model="Kwai-Kolors/Kolors",
            prompt="private prompt must not appear",
        )
        transition_job(
            admitted.job["id"],
            expected_version=int(admitted.job["version"]),
            status="failed",
            stage="finalize",
            error_code="provider_auth_failed",
            error_message="upstream rejected credential sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            error_category="auth_failed",
            human_hint="请检查 Provider API Key 或认证配置",
            retryable=0,
            gateway_stage="provider_response",
        )

        response = self.client.post(
            "/v1/assistant/chat",
            json={"message": f"任务 {admitted.job['id']} 为什么失败？", "language": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["run"]["skill_id"], "job_diagnostician")
        tools = [item.get("tool") for item in body["timeline"] if item.get("type") == "tool"]
        self.assertIn("job_safe_summary", tools)
        self.assertIn("failure_diagnostic", tools)
        self.assertIn("local_kb_search", tools)
        self.assertIn("auth_failed", response.text)
        for forbidden in (
            "private prompt must not appear",
            "LEAKED-SECRET",
            "/root/private",
            '"request_hash"',
            '"input_json"',
            '"output_json"',
        ):
            self.assertNotIn(forbidden, response.text)
        self.assert_safe(response.text)

    def test_llm_receives_only_prepared_safe_tool_context(self) -> None:
        from angemedia_gateway.services.job_admission import JobAdmissionService

        self.login_admin()
        admitted = JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="e" * 64,
            request_hash_version=1,
            payload={"prompt": "hidden prompt", "api_key": "sk-hidden-secret"},
            provider="siliconflow",
            model="Kwai-Kolors/Kolors",
            prompt="hidden prompt",
        )
        set_config_many(
            {
                "ANGE_ASSISTANT_ENABLED": "true",
                "ANGE_LLM_BASE_URL": "http://llm.local/v1",
                "ANGE_LLM_MODEL": "test-chat-model",
                "ANGE_LLM_API_KEY": "sk-test-secret",
            }
        )
        with patch(
            "angemedia_gateway.services.assistant_chat_service._call_llm_chat",
            new=AsyncMock(return_value=("安全工具上下文已读取。", 9)),
        ) as mocked:
            response = self.client.post(
                "/v1/assistant/chat",
                json={"message": f"帮我看看任务 {admitted.job['id']}", "language": "zh"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        mocked.assert_awaited_once()
        kwargs = mocked.await_args.kwargs
        rendered = repr(kwargs.get("tool_results"))
        self.assertIn("job_safe_summary", rendered)
        self.assertNotIn("hidden prompt", rendered)
        self.assertNotIn("sk-hidden-secret", rendered)
        self.assertNotIn("request_hash", rendered)
        self.assertEqual(kwargs.get("skill_context", {}).get("id"), "job_diagnostician")
        self.assert_safe(response.text)

    def test_channel_connection_question_runs_async_connection_and_network_tools(self) -> None:
        self.login_admin()
        with (
            patch(
                "angemedia_gateway.services.assistant_tools.probe_builtin_provider_connection",
                new=AsyncMock(return_value={
                    "provider_id": "siliconflow",
                    "status": "success",
                    "message": "Provider connection test passed.",
                    "http_status": 200,
                    "duration_ms": 18,
                    "details": {"endpoint_kind": "models", "base_url_source": "default", "api_key_source": "env"},
                }),
            ) as connection_mock,
            patch(
                "angemedia_gateway.services.assistant_tools.probe_builtin_provider_network",
                new=AsyncMock(return_value={
                    "provider_id": "siliconflow",
                    "status": "success",
                    "message": "Provider DNS and TCP/TLS probe passed.",
                    "base_url_source": "default",
                    "dns_class": "public",
                    "address_count": 2,
                    "tcp_tls_ms": 12,
                }),
            ) as network_mock,
        ):
            response = self.client.post(
                "/v1/assistant/chat",
                json={"message": "SiliconFlow 渠道连接失败，帮我测试连接和网络连通", "language": "zh"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["run"]["skill_id"], "channel_config_advisor")
        tools = [item.get("tool") for item in body["timeline"] if item.get("type") == "tool"]
        self.assertIn("provider_connection_test", tools)
        self.assertIn("network_probe", tools)
        self.assertIn("success", response.text)
        connection_mock.assert_awaited_once_with("siliconflow")
        network_mock.assert_awaited_once_with("siliconflow")
        self.assert_safe(response.text)

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

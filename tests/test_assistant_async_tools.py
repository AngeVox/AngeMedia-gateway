"""Async Assistant tools and bounded network probe contracts."""
from __future__ import annotations

import asyncio
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.services.assistant_network_probe import probe_builtin_provider_network  # noqa: E402
from angemedia_gateway.services.assistant_skills import load_assistant_skill  # noqa: E402
from angemedia_gateway.services.assistant_tool_planner import plan_assistant_tools, select_chat_skill  # noqa: E402
from angemedia_gateway.services.assistant_tools import (  # noqa: E402
    AssistantToolError,
    AssistantToolExecutor,
)
from angemedia_gateway.state import init_db  # noqa: E402


class _Writer:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class AssistantAsyncToolContractsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="assistant-async-tools-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        C.DB_FILE = self._db_path
        init_db()

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    async def test_async_executor_still_runs_sync_tools(self) -> None:
        skill = load_assistant_skill("angemedia_faq")
        result = await AssistantToolExecutor().execute_async(
            skill,
            "local_knowledge_base",
            {"query": "AngeMedia queue timeout", "limit": 1},
        )
        self.assertEqual(result.status, "done")
        self.assertEqual(result.tool, "local_knowledge_base")
        self.assertLessEqual(result.data["count"], 1)

    async def test_sync_executor_rejects_async_tool_without_event_loop_hack(self) -> None:
        skill = load_assistant_skill("channel_config_advisor")
        with self.assertRaisesRegex(AssistantToolError, "execute_async"):
            AssistantToolExecutor().execute(
                skill,
                "provider_connection_test",
                {"provider_id": "siliconflow"},
            )

    async def test_async_provider_connection_result_is_sanitized(self) -> None:
        skill = load_assistant_skill("channel_config_advisor")
        fake = {
            "provider_id": "siliconflow",
            "status": "success",
            "message": "Authorization: Bearer leak-token",
            "api_key": "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            "base_url_override": "https://secret.example/v1",
            "duration_ms": 12,
        }
        with patch(
            "angemedia_gateway.services.assistant_tools.probe_builtin_provider_connection",
            new=AsyncMock(return_value=fake),
        ):
            result = await AssistantToolExecutor().execute_async(
                skill,
                "provider_connection_test",
                {"provider_id": "siliconflow"},
            )
        rendered = repr(result.as_dict())
        self.assertEqual(result.data["status"], "success")
        self.assertNotIn("api_key", result.data)
        self.assertNotIn("base_url_override", result.data)
        self.assertNotIn("LEAKED", rendered)
        self.assertNotIn("leak-token", rendered)
        self.assertNotIn("Authorization", rendered)

    async def test_connection_intent_plans_network_tools_only_for_named_provider(self) -> None:
        message = "siliconflow 渠道连接失败，测试连接和网络连通"
        skill = select_chat_skill(message)
        self.assertEqual(skill.id, "channel_config_advisor")
        calls = plan_assistant_tools(message, skill)
        names = [name for name, _ in calls]
        self.assertIn("provider_connection_test", names)
        self.assertIn("network_probe", names)
        self.assertLessEqual(len(calls), 4)

        unnamed = "渠道网络怎么检查"
        unnamed_skill = select_chat_skill(unnamed)
        unnamed_names = [name for name, _ in plan_assistant_tools(unnamed, unnamed_skill)]
        self.assertNotIn("provider_connection_test", unnamed_names)
        self.assertNotIn("network_probe", unnamed_names)

    async def test_network_probe_allows_admin_authorized_private_override(self) -> None:
        runtime = SimpleNamespace(
            base_url="http://192.168.1.20:3000/v1",
            base_url_override="http://192.168.1.20:3000/v1",
        )
        loop = asyncio.get_running_loop()
        dns = AsyncMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 3000)),
        ])
        writer = _Writer()
        connect = AsyncMock(return_value=(object(), writer))
        decision = SimpleNamespace(mode="direct", source="provider", proxy_url=None)
        with (
            patch(
                "angemedia_gateway.services.assistant_network_probe.resolve_provider_runtime_config",
                return_value=runtime,
            ),
            patch(
                "angemedia_gateway.services.assistant_network_probe.resolve_saved_provider_transport",
                return_value=decision,
            ),
            patch.object(loop, "getaddrinfo", new=dns),
            patch("angemedia_gateway.services.assistant_network_probe.asyncio.open_connection", new=connect),
        ):
            result = await probe_builtin_provider_network("siliconflow")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dns_class"], "local")
        self.assertEqual(result["transport_mode"], "direct")
        connect.assert_awaited_once()

    async def test_network_probe_explicit_proxy_skips_direct_target_probe(self) -> None:
        runtime = SimpleNamespace(
            base_url="https://api.example.test/v1",
            base_url_override="https://api.example.test/v1",
        )
        decision = SimpleNamespace(mode="explicit_proxy", source="provider", proxy_url="http://127.0.0.1:7890")
        loop = asyncio.get_running_loop()
        dns = AsyncMock()
        connect = AsyncMock()
        with (
            patch(
                "angemedia_gateway.services.assistant_network_probe.resolve_provider_runtime_config",
                return_value=runtime,
            ),
            patch(
                "angemedia_gateway.services.assistant_network_probe.resolve_saved_provider_transport",
                return_value=decision,
            ),
            patch.object(loop, "getaddrinfo", new=dns),
            patch("angemedia_gateway.services.assistant_network_probe.asyncio.open_connection", new=connect),
        ):
            result = await probe_builtin_provider_network("siliconflow")
        self.assertEqual(result["status"], "proxy_delegated")
        self.assertEqual(result["transport_mode"], "explicit_proxy")
        self.assertNotIn("proxy_url", result)
        dns.assert_not_awaited()
        connect.assert_not_awaited()

    async def test_network_probe_recognizes_fake_ip_for_direct_provider(self) -> None:
        runtime = SimpleNamespace(
            base_url="https://apihub.agnes-ai.com/v1",
            base_url_override=None,
        )
        loop = asyncio.get_running_loop()
        dns = AsyncMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.2.16", 443)),
        ])
        writer = _Writer()
        connect = AsyncMock(return_value=(object(), writer))
        with (
            patch(
                "angemedia_gateway.services.assistant_network_probe.resolve_provider_runtime_config",
                return_value=runtime,
            ),
            patch(
                "angemedia_gateway.services.assistant_network_probe.resolve_saved_provider_transport",
                return_value=SimpleNamespace(mode="direct", source="default", proxy_url=None),
            ),
            patch.object(loop, "getaddrinfo", new=dns),
            patch("angemedia_gateway.services.assistant_network_probe.asyncio.open_connection", new=connect),
        ):
            result = await probe_builtin_provider_network("agnes_image")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dns_class"], "fake_ip")
        self.assertTrue(writer.closed)
        kwargs = connect.await_args.kwargs
        self.assertEqual(kwargs["host"], "198.18.2.16")
        self.assertEqual(kwargs["server_hostname"], "apihub.agnes-ai.com")


if __name__ == "__main__":
    unittest.main()

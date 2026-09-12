"""Read-only Assistant ToolRegistry/Executor contracts."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.services.assistant_skills import load_assistant_skill  # noqa: E402
from angemedia_gateway.services.assistant_tool_planner import (  # noqa: E402
    plan_assistant_tools,
    select_chat_skill,
)
from angemedia_gateway.services.assistant_tools import (  # noqa: E402
    AssistantToolDefinition,
    AssistantToolError,
    AssistantToolExecutor,
    AssistantToolRegistry,
    sanitize_assistant_tool_value,
)
from angemedia_gateway.services.job_admission import JobAdmissionService  # noqa: E402
from angemedia_gateway.state import init_db  # noqa: E402


class AssistantToolContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="assistant-tools-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        C.DB_FILE = self._db_path
        init_db()

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_registry_is_explicit_read_only_and_rejects_mutation_tools(self) -> None:
        registry = AssistantToolRegistry()
        self.assertEqual(
            set(registry.names()),
            {
                "catalog_model_capabilities",
                "channel_safe_summary",
                "diagnostics_summary",
                "failure_diagnostic",
                "job_safe_summary",
                "local_knowledge_base",
                "network_probe",
                "provider_connection_test",
                "queue_status",
            },
        )
        with self.assertRaises(ValueError):
            registry.register(AssistantToolDefinition(name="restart_worker", handler=lambda _args: ({}, "bad"), read_only=False))

    def test_skill_allowed_tools_is_enforced_by_executor(self) -> None:
        faq = load_assistant_skill("angemedia_faq")
        executor = AssistantToolExecutor()
        with self.assertRaises(AssistantToolError):
            executor.execute(faq, "job_safe_summary", {"job_id": "a" * 32})

    def test_defense_in_depth_sanitizer_removes_secrets_paths_and_signed_urls(self) -> None:
        value = {
            "api_key": "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
            "request_hash": "f" * 64,
            "nested": {
                "ok": "Authorization: Bearer leak-token C:\\Users\\admin\\secret.png",
                "signed": "https://example.com/a.png?X-Amz-Signature=secret&X-Amz-Expires=10",
                "path": "/root/private/file.txt",
            },
        }
        safe = sanitize_assistant_tool_value(value)
        rendered = repr(safe)
        self.assertNotIn("api_key", safe)
        self.assertNotIn("request_hash", safe)
        self.assertNotIn("LEAKED", rendered)
        self.assertNotIn("leak-token", rendered)
        self.assertNotIn("Users\\admin", rendered)
        self.assertNotIn("X-Amz-Signature", rendered)
        self.assertNotIn("/root/private", rendered)

    def test_job_safe_summary_never_returns_raw_payload_or_prompt(self) -> None:
        admitted = JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="a" * 64,
            request_hash_version=1,
            payload={
                "prompt": "secret prompt that should not be exposed by the tool",
                "api_key": "sk-LEAKED-SECRET-MUST-NOT-APPEAR",
                "local_path": "/root/private/input.png",
            },
            provider="siliconflow",
            model="Kwai-Kolors/Kolors",
            prompt="secret prompt that should not be exposed by the tool",
        )
        skill = load_assistant_skill("job_diagnostician")
        result = AssistantToolExecutor().execute(skill, "job_safe_summary", {"job_id": admitted.job["id"]})
        rendered = repr(result.as_dict())
        self.assertEqual(result.data["job_id"], admitted.job["id"])
        self.assertEqual(result.data["status"], "queued")
        for forbidden in (
            "input_json", "output_json", "request_hash", "secret prompt", "LEAKED", "/root/private",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_channel_safe_summary_does_not_expose_preview_or_base_url(self) -> None:
        skill = load_assistant_skill("channel_config_advisor")
        result = AssistantToolExecutor().execute(
            skill,
            "channel_safe_summary",
            {"provider_id": "siliconflow"},
        )
        rendered = repr(result.as_dict())
        self.assertEqual(result.data["total"], 1)
        channel = result.data["channels"][0]
        self.assertEqual(channel["provider_id"], "siliconflow")
        self.assertIn("key_configured", channel)
        self.assertIn("base_url_overridden", channel)
        self.assertNotIn("api_key_preview", rendered)
        self.assertNotIn("base_url_override", rendered)
        self.assertNotIn("http://", rendered)
        self.assertNotIn("https://", rendered)

    def test_catalog_capability_tool_is_data_only(self) -> None:
        skill = load_assistant_skill("channel_config_advisor")
        result = AssistantToolExecutor().execute(
            skill,
            "catalog_model_capabilities",
            {"model": "gpt-image-2.5-sunburst"},
        )
        self.assertEqual(result.data["provider"], "openai_image")
        self.assertEqual(result.data["media_type"], "image")
        self.assertTrue(result.data["capabilities"]["text_to_image"])
        self.assertIn("operations", result.data)

    def test_rule_planner_routes_job_system_channel_and_faq_without_freeform_tools(self) -> None:
        job_id = "b" * 32
        cases = (
            (f"任务 {job_id} 为什么失败", "job_diagnostician"),
            ("队列 worker 为什么离线", "system_diagnostician"),
            ("siliconflow 渠道配置怎么看", "channel_config_advisor"),
            ("AngeMedia 怎么查看生成结果", "angemedia_faq"),
        )
        for message, wanted in cases:
            with self.subTest(message=message):
                skill = select_chat_skill(message)
                self.assertEqual(skill.id, wanted)
                calls = plan_assistant_tools(message, skill)
                self.assertLessEqual(len(calls), 4)
                self.assertTrue(all(name in skill.allowed_tools for name, _ in calls))
                self.assertNotIn("run_shell", [name for name, _ in calls])


if __name__ == "__main__":
    unittest.main()

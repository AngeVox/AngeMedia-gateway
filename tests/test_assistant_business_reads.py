"""真实业务读取、主体/数据库绑定、只读强制与线程取消收尾合同。"""
from __future__ import annotations

import asyncio
from contextlib import closing
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_run_support import RunFixture
import angemedia_gateway.config as C
from angemedia_gateway.assistant_execution import RunExecution
from angemedia_gateway.assistant_runtime_contract import RunLeaseLost
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.readonly_scope import readonly_database
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assets import save_asset
from angemedia_gateway.repositories.jobs import create_job
from angemedia_gateway.repositories.settings import get_config, set_config, set_config_many
from angemedia_gateway.services.assistant_business_executor import execute_business_tool
from angemedia_gateway.services.assistant_business_policy import AssistantPolicyDenied
from angemedia_gateway.services.assistant_business_specs import BUSINESS_TOOLS
from angemedia_gateway.services.assistant_tools import AssistantToolDefinition


class ReadScopeTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    def second_database(self):
        other = self.temp / "other.db"
        original = C.DB_FILE
        C.DB_FILE = other
        try:
            init_db()
            set_config("ANGE_LLM_MODEL", "other-db")
        finally:
            C.DB_FILE = original
        return other

    async def test_scope_is_read_only_and_explicit_paths_cannot_escape(self):
        other = self.second_database()
        set_config("ANGE_LLM_MODEL", "original-db")
        with readonly_database(self.repo.db_path):
            self.assertEqual(get_config("ANGE_LLM_MODEL"), "original-db")
            with self.assertRaises(sqlite3.OperationalError):
                set_config("ANGE_LLM_MODEL", "must-not-write")
            with self.assertRaises(ValueError):
                db_connect(path=other)
            with self.assertRaises(ValueError):
                with readonly_database(other):
                    pass
        set_config("ANGE_LLM_MODEL", "outside-can-write")
        self.assertEqual(get_config("ANGE_LLM_MODEL"), "outside-can-write")

    async def test_concurrent_tasks_and_threads_have_independent_database_bindings(self):
        other = self.second_database()
        set_config("ANGE_LLM_MODEL", "original-db")
        async def read(path):
            with readonly_database(path):
                await asyncio.sleep(0.01)
                return await asyncio.to_thread(get_config, "ANGE_LLM_MODEL")
        self.assertEqual(await asyncio.gather(read(self.repo.db_path), read(other)), ["original-db", "other-db"])
        self.assertEqual(get_config("ANGE_LLM_MODEL"), "original-db")

    async def test_scope_resets_after_cancellation(self):
        other = self.second_database()
        entered = asyncio.Event()
        async def wait():
            with readonly_database(other):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(wait())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        set_config("ANGE_LLM_MODEL", "still-original")
        self.assertEqual(get_config("ANGE_LLM_MODEL"), "still-original")


class BusinessReadsTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        set_config_many({"ANGE_ASSISTANT_ENABLED": "true", "ANGE_LLM_MODEL": "fixture", "ANGE_LLM_BASE_URL": "http://127.0.0.1:1/v1"})
        run = self.repo.admit(self.session, uuid4().hex, {"message": "读取实际状态"}).run
        self.context = RunExecution(self.repo.claim(run["id"]), {"message": "读取实际状态"}, run["deadline_ms"], self.repo)
        self.job = create_job(kind="image", stage="image_generate", provider="siliconflow", model="Kwai-Kolors/Kolors",
                              prompt="private-prompt", input_json='{"api_key":"private-input-key"}')
        for asset_id, media in (("asset-image", "image"), ("asset-video", "video")):
            save_asset(id=asset_id, filename=asset_id, storage_area="output", relative_path=asset_id,
                url_path="/generated/" + asset_id, media_type=media, source="generated", job_id=self.job["id"], prompt="private-prompt")

    async def call(self, name, args=None):
        return await execute_business_tool(self.context, name, {} if args is None else args)

    async def test_real_job_followed_by_asset_query_uses_persisted_ids(self):
        job = await self.call("job_safe_summary", {"job_id": self.job["id"]})
        self.assertEqual(job["data"]["job_id"], self.job["id"])
        self.assertEqual(job["data"]["asset_count"], 2)
        assets = await self.call("asset_search", {"job_id": job["data"]["job_id"], "media_type": "video"})
        self.assertEqual([item["id"] for item in assets["data"]["items"]], ["asset-video"])
        for secret in ("private-prompt", "private-input-key", "input_json", "relative_path"):
            self.assertNotIn(secret, str(job) + str(assets))

    async def test_missing_record_is_structured_error_without_fake_success(self):
        for name, args in (("job_safe_summary", {"job_id": "missing"}), ("asset_search", {"asset_id": "missing"}),
                           ("catalog_model_capabilities", {"model": "missing"})):
            self.assertEqual((await self.call(name, args))["code"], "not_found")

    async def test_strict_public_parameters_reject_control_fields_and_wrong_types(self):
        for args in ({"job_id": self.job["id"], "runtime": None}, {"job_id": self.job["id"], "username": "bob"},
                     {"job_id": self.job["id"], "db_path": "other.db"}, {"job_id": 123}):
            self.assertEqual((await self.call("job_safe_summary", args))["code"], "invalid_tool_arguments")
        self.assertEqual((await self.call("asset_search", {"limit": True}))["code"], "invalid_tool_arguments")

    async def test_channel_and_catalog_reads_do_not_claim_generation_verified(self):
        channel = await self.call("channel_safe_summary", {"provider_id": "siliconflow"})
        self.assertFalse(channel["data"]["channels"][0]["connection_evidence"]["generation_verified"])
        model = await self.call("catalog_model_capabilities", {"model": "gpt-image-2.5-sunburst"})
        self.assertTrue(model["data"]["capabilities"]["text_to_image"])

    async def test_binding_ignores_later_global_database_change(self):
        original = C.DB_FILE
        C.DB_FILE = self.temp / "other.db"
        try:
            init_db()
            self.assertEqual((await self.call("job_safe_summary", {"job_id": self.job["id"]}))["data"]["job_id"], self.job["id"])
        finally:
            C.DB_FILE = original

    async def test_disabled_assistant_and_lost_lease_cannot_read(self):
        set_config("ANGE_ASSISTANT_ENABLED", "false")
        with self.assertRaises(AssistantPolicyDenied):
            await self.call("job_safe_summary", {"job_id": self.job["id"]})
        set_config("ANGE_ASSISTANT_ENABLED", "true")
        self.repo.cancel(self.context.lease.run_id)
        with self.assertRaises(RunLeaseLost):
            await self.call("job_safe_summary", {"job_id": self.job["id"]})

    async def test_worker_cannot_write_config_even_if_handler_attempts_it(self):
        def write(_args):
            set_config("ANGE_LLM_MODEL", "must-not-write")
            return {}, "bad"
        with patch("angemedia_gateway.services.assistant_business_executor.AssistantToolRegistry.get",
                   return_value=AssistantToolDefinition("local_knowledge_base", write)):
            result = await self.call("local_knowledge_base", {"query": "流程"})
        self.assertEqual(result["code"], "business_read_failed")
        self.assertEqual(get_config("ANGE_LLM_MODEL"), "fixture")

    async def test_cancel_waits_for_read_worker_before_returning(self):
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        def wait(_args):
            entered.set()
            release.wait(3)
            exited.set()
            return {}, "done"
        with patch("angemedia_gateway.services.assistant_business_executor.AssistantToolRegistry.get",
                   return_value=AssistantToolDefinition("local_knowledge_base", wait)):
            task = asyncio.create_task(self.call("local_knowledge_base", {"query": "流程"}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(exited.is_set())

    async def test_disable_during_read_drops_result_before_next_model_call(self):
        entered, release = threading.Event(), threading.Event()
        def wait(_args):
            entered.set()
            release.wait(3)
            return {"private": "read-result"}, "done"
        with patch("angemedia_gateway.services.assistant_business_executor.AssistantToolRegistry.get",
                   return_value=AssistantToolDefinition("local_knowledge_base", wait)):
            task = asyncio.create_task(self.call("local_knowledge_base", {"query": "流程"}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                set_config("ANGE_ASSISTANT_ENABLED", "false")
            finally:
                release.set()
            with self.assertRaises(AssistantPolicyDenied):
                await task

    async def test_diagnostics_do_not_walk_media_directories(self):
        with patch("angemedia_gateway.services.dashboard_summary._directory_size", side_effect=AssertionError("不应扫描")):
            result = await self.call("diagnostics_summary")
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["data"]["database"]["reachable"])

    async def test_logs_redact_exact_configured_keys_and_bound_output(self):
        secret = "bare-channel-credential-123456"
        set_config("SILICONFLOW_API_KEY", secret)
        logs = self.temp / "logs"
        logs.mkdir()
        (logs / "api.log").write_text(("普通创作记录 " + secret + " " + "x"*600 + "\n")*100, encoding="utf-8")
        with patch.dict(os.environ, {"ANGEMEDIA_LOG_DIR": str(logs)}):
            result = await self.call("recent_logs", {"source": "all", "line_limit": 3})
        self.assertNotIn(secret, str(result))
        self.assertIn("普通创作记录", str(result))
        self.assertLess(len(str(result)), 8000)

    async def test_capabilities_declare_external_effects_separately(self):
        specs = {spec.name: spec for spec in BUSINESS_TOOLS}
        self.assertEqual(len(specs), 12)
        self.assertIn("provider_model_list_request", specs["provider_connection_test"].effects)
        self.assertEqual(specs["job_safe_summary"].effects, ("database_read",))

    async def test_search_jobs_without_id_is_filtered_bounded_and_safe(self):
        from angemedia_gateway.repositories.jobs import update_job_status
        update_job_status(self.job['id'], status='failed', error_code='provider_error')
        result = await self.call('job_search', {'status': 'failed', 'kind': 'image', 'limit': 1})
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['data']['items'][0]['id'], self.job['id'])
        for private in ('private-prompt', 'private-input-key', 'input_json', 'request_hash'):
            self.assertNotIn(private, str(result))
        empty = await self.call('job_search', {'status': 'succeeded'})
        self.assertEqual(empty['data']['items'], [])
        self.assertEqual((await self.call('job_search', {'limit': 11}))['code'], 'invalid_tool_arguments')


if __name__ == "__main__":
    unittest.main()

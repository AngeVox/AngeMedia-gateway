"""生产工厂与真实SDK调用实际业务读取；模型仅为本机受控HTTP。"""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from assistant_run_support import RunFixture
from engine_lab import Fixture
from fastapi import FastAPI
import httpx
from langchain_core.messages import ToolMessage
from angemedia_gateway.assistant_engine_payloads import EnginePayloadPolicy
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assets import save_asset
from angemedia_gateway.repositories.jobs import create_job
from angemedia_gateway.repositories.settings import set_config_many, set_config
from angemedia_gateway.routes import assistant_runs
from angemedia_gateway.services.assistant_business_tools import business_tools
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan
from angemedia_gateway.services.assistant_tools import AssistantToolDefinition

BASE = "/v1/admin/assistant"


class BusinessAssistantTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = Fixture()
        self.mode = "normal"
        self.asset = "asset-business-result"
        self.job = create_job(kind="image", stage="image_generate", provider="siliconflow", model="Kwai-Kolors/Kolors")
        save_asset(id=self.asset, filename="result.png", storage_area="output", relative_path="result.png", url_path="/generated/result.png",
                   media_type="image", source="generated", job_id=self.job["id"], prompt="private-asset-prompt")
        set_config_many({"ANGE_ASSISTANT_ENABLED": "true", "ANGE_LLM_MODEL": "protocol-fixture", "ANGE_LLM_API_KEY": "business-model-credential",
                         "ANGE_LLM_BASE_URL": f"http://127.0.0.1:{self.fixture.server.server_port}/v1/business"})
        self.fixture.reply = self.reply
        self.app = FastAPI(lifespan=assistant_lifespan)
        self.app.include_router(assistant_runs.router)
        self.lifecycle = assistant_lifespan(self.app)
        await self.lifecycle.__aenter__()
        self.service = self.app.state.assistant_run_service
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver",
                                        **self.studio_credentials("alice"))

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)
        await asyncio.to_thread(self.fixture.close)

    def reply(self, scenario, messages):
        tools = [message for message in messages if message["role"] == "tool"]
        def call(name, args, call_id):
            return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}, "tool_calls"
        if not tools:
            if self.mode == "logs":
                return call("recent_logs", {"source": "api", "line_limit": 2}, "read-logs")
            args = {"job_id": "missing" if self.mode == "missing" else self.job["id"]}
            if self.mode == "forged":
                args["runtime"] = None
            return call("job_safe_summary", args, "read-job")
        try:
            last = json.loads(tools[-1]["content"])
        except ValueError:
            last = {"status": "error", "code": tools[-1]["content"]}
        if last.get("status") == "error":
            return {"role": "assistant", "content": "记录或参数需要核对：" + last["code"]}, "stop"
        if len(tools) == 1 and self.mode != "logs":
            return call("asset_search", {"job_id": last["data"]["job_id"]}, "read-assets")
        content = "已找到作品：" + last["data"]["items"][0]["id"] if self.mode != "logs" else "普通创作建议和日志已读取"
        return {"role": "assistant", "content": content}, "stop"

    async def submit(self, key=None):
        return await self.client.post(f"{BASE}/conversations/{self.session}/runs",
                                      json={"request_key": key or uuid4().hex, "message": "先查这项任务，再找它的作品"})

    async def finished(self, run_id):
        async def wait():
            while True:
                run = (await self.client.get(f"{BASE}/runs/{run_id}")).json()["run"]
                if run["status"] not in {"queued", "running", "paused"}:
                    return run
                await asyncio.sleep(0.01)
        return await asyncio.wait_for(wait(), 10)

    async def test_production_factory_uses_native_dependent_business_tools_and_replays(self):
        key = uuid4().hex
        self.assertTrue((await self.client.get(BASE + "/runtime")).json()["can_submit"])
        accepted = await self.submit(key)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        run = await self.finished(accepted.json()["run"]["id"])
        self.assertEqual(run["status"], "succeeded", run)
        self.assertEqual((run["model_calls"], run["tool_calls"]), (3, 2))
        self.assertIn(self.asset, run["answer"])
        count = len(self.fixture.requests)
        replay = await self.submit(key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["run"], run)
        self.assertEqual(len(self.fixture.requests), count)
        page = (await self.client.get(f"{BASE}/runs/{run['id']}/events")).json()
        names = [event["payload"]["name"] for event in page["events"] if event["event_type"] == "tool_started"]
        self.assertEqual(names, ["job_safe_summary", "asset_search"])
        schema_text = json.dumps(self.fixture.requests[0]["body"]["tools"])
        self.assertNotIn('"runtime"', schema_text)
        self.assertNotIn('"username"', schema_text)
        with closing(sqlite3.connect(self.service.checkpoint_db)) as conn:
            rows = repr([conn.execute(f"SELECT * FROM {table}").fetchall() for table in ("checkpoints", "writes")])
            for secret in ("business-model-credential", self.cookies["alice"], self.access["alice"].session_hash):
                self.assertNotIn(secret, rows)
            self.assertNotIn("RunExecution", rows)

    async def test_forged_hidden_runtime_is_rejected_before_business_handler(self):
        self.mode = "forged"
        accepted = await self.submit()
        run = await self.finished(accepted.json()["run"]["id"])
        self.assertEqual(run["status"], "succeeded", run)
        self.assertIn("invalid_tool_arguments", run["answer"])
        events = self.repo.events(run["id"])
        self.assertFalse(any(event["event_type"] == "tool_started" for event in events))

    async def test_missing_job_is_a_tool_error_and_clarification_not_a_scope_refusal(self):
        self.mode = "missing"
        accepted = await self.submit()
        run = await self.finished(accepted.json()["run"]["id"])
        self.assertEqual(run["status"], "succeeded", run)
        self.assertIn("not_found", run["answer"])

    async def test_disable_before_admission_prevents_model_requests(self):
        set_config("ANGE_ASSISTANT_ENABLED", "false")
        self.assertEqual((await self.submit()).status_code, 503)
        self.assertEqual(len(self.fixture.requests), 0)
        self.assertEqual(self.repo.ready(), [])

    async def test_disable_while_reading_stops_further_model_calls(self):
        entered, release = threading.Event(), threading.Event()
        def wait(_args):
            entered.set()
            release.wait(5)
            return {}, "done"
        with patch("angemedia_gateway.services.assistant_business_executor.AssistantToolRegistry.get",
                   return_value=AssistantToolDefinition("job_safe_summary", wait)):
            accepted = await self.submit()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 4))
                set_config("ANGE_ASSISTANT_ENABLED", "false")
            finally:
                release.set()
            run = await self.finished(accepted.json()["run"]["id"])
        self.assertEqual(run["status"], "refused", run)
        self.assertEqual(run["output"]["error_code"], "assistant_disabled")
        self.assertEqual(len(self.fixture.requests), 1)

    async def test_known_channel_credentials_in_logs_never_enter_model_or_checkpoint(self):
        import os
        secret = "unprefixed-channel-credential-12345"
        set_config("SILICONFLOW_API_KEY", secret)
        logs = self.temp / "logs"
        logs.mkdir()
        (logs / "api.log").write_text("普通创作记录 " + secret, encoding="utf-8")
        self.mode = "logs"
        with patch.dict(os.environ, {"ANGEMEDIA_LOG_DIR": str(logs)}):
            accepted = await self.submit()
            run = await self.finished(accepted.json()["run"]["id"])
        self.assertEqual(run["status"], "succeeded", run)
        self.assertNotIn(secret, repr(self.fixture.requests))
        self.assertNotIn(secret, repr(self.repo.events(run["id"])))
        with closing(sqlite3.connect(self.service.checkpoint_db)) as conn:
            for table in ("checkpoints", "writes"):
                self.assertNotIn(secret, repr(conn.execute(f"SELECT * FROM {table}").fetchall()))

    def test_public_schema_and_error_projection_exclude_runtime_and_raw_sdk_errors(self):
        for tool in business_tools():
            self.assertNotIn("runtime", tool.tool_call_schema.model_fields)
            with self.assertRaises(ValueError):
                tool.tool_call_schema.model_validate({"runtime": None}, strict=True, extra="forbid")
        policy = EnginePayloadPolicy()
        raw = policy.tool_message(ToolMessage(content="private traceback body", status="error", tool_call_id="t"))
        self.assertEqual(raw.content, "invalid_tool_arguments")
        fixed = policy.tool_message(ToolMessage(content='{"tool":"job_safe_summary","status":"error","code":"not_found"}', status="error", tool_call_id="t"))
        self.assertIn("not_found", fixed.content)


if __name__ == "__main__":
    unittest.main()

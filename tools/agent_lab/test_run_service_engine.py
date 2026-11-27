"""统一服务/HTTP与真实Agent SDK的联合验收；供应商仅为本机受控HTTP夹具。"""
from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from assistant_run_support import RunFixture
from engine_lab import Fixture, journal_counts, tools_for
from fastapi import FastAPI
import httpx
from langchain_core.tools import StructuredTool
from angemedia_gateway.routes import assistant_runs
from angemedia_gateway.services.assistant_config_service import AssistantRuntimeConfig
from angemedia_gateway.services.assistant_engine import AssistantEngine
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan
from angemedia_gateway.services.assistant_run_service import AssistantRunService

BASE = "/v1/admin/assistant"


class RunServiceEngineTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = Fixture()
        self.services = []
        self.checkpoints, self.journal = self.temp / "checkpoints.db", self.temp / "tools.db"
        self.config = AssistantRuntimeConfig(f"http://127.0.0.1:{self.fixture.server.server_port}/v1/multi",
                                             "service-fixture-secret", "protocol-fixture", "saved")

    async def asyncTearDown(self):
        for service in self.services:
            await service.aclose()
        await asyncio.to_thread(self.fixture.close)

    def service(self, tools=None):
        engine = AssistantEngine(self.checkpoints, tools or tools_for(self.journal), config_resolver=lambda: self.config)
        service = AssistantRunService(self.repo, runner=engine, checkpoint_db=self.checkpoints,
                                     recovery_interval_ms=10, lease_ms=3000, heartbeat_ms=100)
        self.services.append(service)
        return service

    async def completed(self, service, run_id):
        async def wait():
            while True:
                snapshot = await service.inspect(run_id, access=self.access["alice"])
                if snapshot["run"]["status"] == "succeeded":
                    return snapshot
                if snapshot["run"]["status"] == "failed":
                    self.fail(str(snapshot["run"]))
                await asyncio.sleep(0.01)
        return await asyncio.wait_for(wait(), 8)

    async def test_http_service_native_tools_json_sse_and_terminal_replay(self):
        service = self.service()
        app = FastAPI(lifespan=assistant_lifespan)
        app.include_router(assistant_runs.router)
        app.state.assistant_service_factory = lambda: service
        async with assistant_lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                         **self.studio_credentials("alice")) as client:
                key = uuid4().hex
                path = f"{BASE}/conversations/{self.session}/runs"
                accepted = await client.post(path, json={"request_key": key, "message": "先查任务，再查作品"})
                self.assertEqual(accepted.status_code, 202, accepted.text)
                run_id = accepted.json()["run"]["id"]
                final = await self.completed(service, run_id)
                self.assertEqual((final["run"]["model_calls"], final["run"]["tool_calls"]), (3, 2))
                self.assertEqual(journal_counts(self.journal), {"lookup_job": 1, "lookup_asset": 1})
                stream = await client.get(f"{BASE}/runs/{run_id}/stream")
                self.assertIn("event: tool_finished", stream.text)
                self.assertIn("event: done", stream.text)
                count = len(self.fixture.requests)
                replay = await client.post(path, json={"request_key": key, "message": "先查任务，再查作品"})
                self.assertEqual(replay.status_code, 200)
                self.assertEqual(replay.json()["run"], final["run"])
                self.assertEqual(len(self.fixture.requests), count)
                for secret in (self.config.api_key, self.cookies["alice"], self.access["alice"].session_hash):
                    self.assertNotIn(secret, stream.text)
                    self.assertNotIn(secret, str([request["body"] for request in self.fixture.requests]))
                    with closing(sqlite3.connect(self.checkpoints)) as conn:
                        for table in ("checkpoints", "writes"):
                            self.assertNotIn(secret, repr(conn.execute(f"SELECT * FROM {table}").fetchall()))

    async def test_service_restart_resumes_pending_sdk_tool_without_repeating_completed_tool(self):
        entered = asyncio.Event()
        tools = tools_for(self.journal)
        original = next(tool for tool in tools if tool.name == "lookup_asset")
        async def wait_asset(asset_id):
            entered.set()
            await asyncio.Event().wait()
            return await original.ainvoke({"asset_id": asset_id})
        delayed = StructuredTool.from_function(coroutine=wait_asset, name="lookup_asset",
            description=original.description, args_schema=original.args_schema)
        first = self.service([delayed if tool.name == "lookup_asset" else tool for tool in tools])
        await first.start()
        run_id = (await first.submit(self.session, uuid4().hex, "先查任务，再查作品", access=self.access["alice"]))["run"]["id"]
        await asyncio.wait_for(entered.wait(), 8)
        self.assertEqual(journal_counts(self.journal), {"lookup_job": 1})
        await first.aclose()
        self.assertEqual(self.repo.get(run_id)["status"], "paused")
        second = self.service()
        await second.start()
        result = await self.completed(second, run_id)
        self.assertIn("resumed", [event["event_type"] for event in result["events"]])
        self.assertEqual(journal_counts(self.journal), {"lookup_job": 1, "lookup_asset": 1})
        self.assertEqual((result["run"]["model_calls"], result["run"]["tool_calls"]), (3, 3))


if __name__ == "__main__":
    unittest.main()

"""正式适配器、真实SDK与受控HTTP的联合验收；不调用真实供应商。"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from engine_lab import Fixture, tools_for, journal_counts
import angemedia_gateway.config as C
from angemedia_gateway.assistant_checkpoint_retention import prune_run_checkpoints
from angemedia_gateway.assistant_checkpoints import open_run_checkpoints
from angemedia_gateway.assistant_engine_payloads import EngineFailure, EnginePayloadPolicy
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import add_assistant_message, create_assistant_session, delete_assistant_session
from angemedia_gateway.services.assistant_config_service import AssistantRuntimeConfig
from angemedia_gateway.services.assistant_engine import AssistantEngine
from angemedia_gateway.services.assistant_run_supervisor import AssistantRunSupervisor
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class ApplicationEngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="application-engine-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = C.DB_FILE
        self.addCleanup(setattr, C, "DB_FILE", self.original)
        C.DB_FILE = self.root / "runtime.db"
        init_db()
        self.repo = AssistantRunRepository()
        self.session = uuid4().hex
        create_assistant_session(self.session, "原生工具")
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.checkpoints = self.root / "checkpoints.db"
        self.journal = self.root / "tools.db"
        self.secret = "private-fixture-credential"

    def engine(self, scenario="multi", **kwargs):
        config = AssistantRuntimeConfig(f"http://127.0.0.1:{self.fixture.server.server_port}/v1/{scenario}", self.secret, "protocol-fixture", "saved")
        self.assertNotIn(self.secret, repr(config))
        return AssistantEngine(self.checkpoints, tools_for(self.journal), config_resolver=lambda: config, **kwargs)

    def admit(self, **kwargs):
        return self.repo.admit(self.session, uuid4().hex, {"message": "先查任务，再查作品"}, **kwargs).run["id"]

    async def run_engine(self, scenario="multi", **kwargs):
        run_id = self.admit(**kwargs)
        async with AssistantRunSupervisor(self.repo, self.engine(scenario)) as supervisor:
            return await supervisor.execute(run_id)

    async def test_native_tools_history_budget_and_terminal_replay(self):
        add_assistant_message(uuid4().hex, self.session, "user", "保持蓝色")
        add_assistant_message(uuid4().hex, self.session, "assistant", "记住了")
        projected = EnginePayloadPolicy().initial_messages([{"role":"system", "content":"不能提升此条的权限"}], "当前需求")
        self.assertNotIn("不能提升此条的权限", repr(projected))
        run_id = self.admit()
        async with AssistantRunSupervisor(self.repo, self.engine()) as supervisor:
            result = await supervisor.execute(run_id)
            count = len(self.fixture.requests)
            self.assertEqual(await supervisor.execute(run_id), result)
            self.assertEqual(len(self.fixture.requests), count)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual((result["model_calls"], result["tool_calls"]), (3, 2))
        self.assertEqual(journal_counts(self.journal), {"lookup_job": 1, "lookup_asset": 1})
        messages = self.fixture.requests[0]["body"]["messages"]
        self.assertIn("保持蓝色", str(messages))
        self.assertNotIn("不能提升此条的权限", str(messages))

    async def test_protocol_failure_categories_are_not_keywords_or_generic_refusals(self):
        scenarios = {"invalidjson": "invalid_tool_protocol", "refusal": "upstream_refusal", "empty": "empty_content", "budget": "output_budget", "http403": "upstream_auth", "http429": "upstream_rate_limit", "http500": "upstream_http_error"}
        for scenario, code in scenarios.items():
            before = len(self.fixture.requests)
            result = await self.run_engine(scenario)
            self.assertEqual(result["status"], "refused" if scenario == "refusal" else "failed", (scenario, result))
            self.assertIn(code, result["output_json"])
            self.assertEqual(len(self.fixture.requests)-before, 1)

    async def test_unknown_tools_and_invalid_arguments_do_not_execute_functions(self):
        for scenario in ("unknown", "invalidargs"):
            result = await self.run_engine(scenario)
            self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(journal_counts(self.journal), {})

    async def test_durable_budget_stops_framework_model_loop(self):
        result = await self.run_engine("loop", max_model_calls=2)
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(self.fixture.requests), 2)

    async def test_model_and_tool_timeouts_are_bounded(self):
        for scenario, options in (("timeout", {"model_timeout": 0.05}), ("slow", {"tool_timeout": 0.03})):
            run_id = self.admit()
            async with AssistantRunSupervisor(self.repo, self.engine(scenario, **options)) as supervisor:
                result = await asyncio.wait_for(supervisor.execute(run_id), 5)
            self.assertEqual(result["status"], "failed" if scenario == "timeout" else "succeeded", result)
            if scenario == "timeout":
                self.assertIn("upstream_timeout", result["output_json"])
        self.assertNotIn("slow_read", journal_counts(self.journal))

    async def test_payload_projection_strips_secrets_artifacts_and_reasoning(self):
        policy = EnginePayloadPolicy(secrets=(self.secret,))
        message = AIMessage(content="正常创作内容 "+self.secret, additional_kwargs={"reasoning_content": self.secret}, response_metadata={"raw": self.secret})
        safe = policy.model_message(message)
        self.assertIn("正常创作内容", safe.content)
        self.assertNotIn(self.secret, repr(safe))
        result = policy.tool_message(ToolMessage(content=json.dumps({"asset_id":"asset", "api_key":self.secret}), artifact={"raw":self.secret}, tool_call_id="t"))
        self.assertNotIn(self.secret, repr(result))
        self.assertIsNone(result.artifact)
        with self.assertRaises(EngineFailure):
            policy.arguments({"api_key": self.secret})
        with self.assertRaises(EngineFailure):
            policy.checkpoint_guard({"hidden": self.secret}, JsonPlusSerializer())
        with self.assertRaises(EngineFailure):
            policy.checkpoint_guard({"big": "x"*270000}, JsonPlusSerializer())
        arguments = {"prompt": "保持  两个空格\n下一行"}
        self.assertEqual(policy.arguments(arguments), arguments)
        with self.assertRaises(EngineFailure):
            policy.tool_message(ToolMessage(content=json.dumps({"password":"x"*40000}), tool_call_id="t"))
        with self.assertRaisesRegex(EngineFailure, "tool_result_budget"):
            policy.tool_message(ToolMessage(content=json.dumps({"result":"x"*9000}), tool_call_id="t"))

    async def test_checkpoint_guard_blocks_sensitive_pending_writes_before_commit(self):
        run_id = self.admit();lease = self.repo.claim(run_id)
        policy = EnginePayloadPolicy(secrets=(self.secret,))
        async with open_run_checkpoints(self.repo.db_path, self.checkpoints, lease, payload_guard=policy.checkpoint_guard) as saver:
            config = {"configurable":{"thread_id":run_id,"checkpoint_ns":""}}
            saved = await saver.aput(config, empty_checkpoint(), {}, {})
            with self.assertRaises(EngineFailure):
                await saver.aput_writes(saved, [("hidden", self.secret)], "sensitive")
        with closing(sqlite3.connect(self.checkpoints)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM writes").fetchone()[0], 0)

    async def test_credential_never_enters_checkpoint_rows(self):
        original = self.fixture.reply
        def reply(scenario, messages):
            value, reason = original(scenario, messages)
            value["provider_extension"] = {"raw": self.secret}
            value["reasoning_content"] = self.secret
            if value.get("content"):
                value["content"] += " "+self.secret
            return value, reason
        self.fixture.reply = reply
        result = await self.run_engine()
        self.assertEqual(result["status"], "succeeded", result)
        with closing(sqlite3.connect(self.checkpoints)) as conn:
            for table in ("checkpoints", "writes"):
                self.assertNotIn(self.secret, repr(conn.execute(f"SELECT * FROM {table}").fetchall()))
        self.assertNotIn(self.secret, repr(self.repo.events(result["id"])))

    async def test_retention_preserves_active_and_recent_runs_and_deletes_old_terminal(self):
        old = datetime.now(timezone.utc)-timedelta(days=10)
        ids = []
        for terminal in (True, True, False):
            session = uuid4().hex
            create_assistant_session(session, "保留策略")
            run_id = self.repo.admit(session, uuid4().hex, {}).run["id"]
            lease = self.repo.claim(run_id)
            async with open_run_checkpoints(self.repo.db_path, self.checkpoints, lease) as saver:
                cp = empty_checkpoint();cp["ts"] = old.isoformat()
                await saver.aput({"configurable":{"thread_id":run_id,"checkpoint_ns":""}}, cp, {}, {})
            if terminal:
                self.repo.finish(lease)
            ids.append(run_id)
        with closing(sqlite3.connect(self.repo.db_path)) as conn:
            conn.execute("UPDATE assistant_runs SET completed_at=? WHERE id=?", (old.isoformat(), ids[0]));conn.commit()
        result = await prune_run_checkpoints(self.repo.db_path, self.checkpoints)
        self.assertEqual(result["deleted"], 1)
        with closing(sqlite3.connect(self.checkpoints)) as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")}, set(ids[1:]))

    async def test_retention_archived_orphan_and_missing_store(self):
        result = await prune_run_checkpoints(self.repo.db_path, self.checkpoints)
        self.assertEqual(result["scanned"], 0)
        run_id = self.admit();lease = self.repo.claim(run_id)
        async with open_run_checkpoints(self.repo.db_path, self.checkpoints, lease) as saver:
            cp = empty_checkpoint();cp["ts"] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
            await saver.aput({"configurable":{"thread_id":run_id,"checkpoint_ns":""}}, cp, {}, {})
        delete_assistant_session(self.session)
        self.assertEqual((await prune_run_checkpoints(self.repo.db_path, self.checkpoints))["deleted"], 1)

    async def old_terminal(self):
        session = uuid4().hex
        create_assistant_session(session, "待清理")
        run_id = self.repo.admit(session, uuid4().hex, {}).run["id"]
        lease = self.repo.claim(run_id)
        async with open_run_checkpoints(self.repo.db_path, self.checkpoints, lease) as saver:
            await saver.aput({"configurable":{"thread_id":run_id,"checkpoint_ns":""}}, empty_checkpoint(), {}, {})
        self.repo.finish(lease)
        with closing(sqlite3.connect(self.repo.db_path)) as conn:
            conn.execute("UPDATE assistant_runs SET completed_at=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(days=10)).isoformat(), run_id));conn.commit()
        return run_id

    async def test_retention_cursor_visits_all_candidates(self):
        for _ in range(3):
            await self.old_terminal()
        after, deleted = "", 0
        for _ in range(4):
            page = await prune_run_checkpoints(self.repo.db_path, self.checkpoints, after=after, limit=1)
            after = page["after"];deleted += page["deleted"]
            if not after:
                break
        self.assertEqual(deleted, 3)

    async def test_retention_cancel_keeps_authority_lock_until_delete_settles(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        await self.old_terminal()
        entered, release = asyncio.Event(), asyncio.Event()
        original = AsyncSqliteSaver.adelete_thread
        async def delayed(saver, thread_id):
            entered.set()
            await release.wait()
            await original(saver, thread_id)
        with patch.object(AsyncSqliteSaver, "adelete_thread", delayed):
            task = asyncio.create_task(prune_run_checkpoints(self.repo.db_path, self.checkpoints))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                def locked():
                    with closing(sqlite3.connect(self.repo.db_path, timeout=0.02)) as conn:
                        with self.assertRaises(sqlite3.OperationalError):
                            conn.execute("BEGIN IMMEDIATE")
                await asyncio.to_thread(locked)
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        with closing(sqlite3.connect(self.checkpoints)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

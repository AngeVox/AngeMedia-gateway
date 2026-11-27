"""真实图与运行控制联合验收，包含进程强退及独立进程争抢。"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from typing import TypedDict
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.assistant_checkpoints import open_run_checkpoints
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session
from angemedia_gateway.services.assistant_run_supervisor import AssistantRunSupervisor
from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    count: int


def graph_runner(checkpoints, *, first_observer=None, second_wait=None):
    async def invoke(context):
        async def first(state):
            await context.reserve_call("tool")
            if first_observer:
                first_observer()
            return {"count": state["count"] + 1}

        async def second(state):
            if second_wait:
                await second_wait()
            await context.reserve_call("tool")
            return {"count": state["count"] + 10}

        builder = StateGraph(State)
        builder.add_node("first", first)
        builder.add_node("second", second)
        builder.add_edge(START, "first")
        builder.add_edge("first", "second")
        builder.add_edge("second", END)
        async with open_run_checkpoints(context.repository.db_path, checkpoints, context.lease,
                                        clock=context.repository.clock) as saver:
            graph = builder.compile(checkpointer=saver)
            config = {"configurable": {"thread_id": context.lease.run_id, "checkpoint_ns": ""}}
            existing = await saver.aget_tuple(config)
            result = await graph.ainvoke(None if existing else {"count": 0}, config, durability="sync")
        return RunResult(output=result)
    return invoke


def _crash_after_checkpoint(runtime, checkpoints, run_id, ready):
    repo = AssistantRunRepository(db_path=Path(runtime), clock=lambda: 1000)
    async def crash():
        ready.set()
        os._exit(23)
    async def execute():
        async with AssistantRunSupervisor(repo, graph_runner(Path(checkpoints), second_wait=crash),
                                          lease_ms=300, heartbeat_ms=100) as supervisor:
            await supervisor.execute(run_id)
    asyncio.run(execute())


def _contest(runtime, run_id, marker, ready, output):
    repo = AssistantRunRepository(db_path=Path(runtime), clock=lambda: 1000)
    async def runner(context):
        await context.reserve_call("tool")
        with open(marker, "a", encoding="utf-8") as stream:
            stream.write("执行一次\n")
        await asyncio.sleep(0.05)
        return RunResult()
    async def execute():
        await asyncio.to_thread(ready.wait, 20)
        async with AssistantRunSupervisor(repo, runner, lease_ms=300, heartbeat_ms=100) as supervisor:
            output.put((await supervisor.execute(run_id))["status"])
    asyncio.run(execute())


class SupervisorGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-graph-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = C.DB_FILE
        C.DB_FILE = self.root / "runtime.db"
        self.addCleanup(setattr, C, "DB_FILE", self.original)
        init_db()
        self.now = 1000
        self.repo = AssistantRunRepository(clock=lambda: self.now)
        session = uuid4().hex
        create_assistant_session(session, "真实图")
        self.run_id = self.repo.admit(session, uuid4().hex, {}).run["id"]
        self.checkpoints = self.root / "checkpoints.db"

    async def test_shutdown_and_resume_keep_committed_node_and_durable_call_budget(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def wait():
            entered.set()
            await release.wait()
        old = AssistantRunSupervisor(self.repo, graph_runner(self.checkpoints, first_observer=lambda: calls.append("first"), second_wait=wait))
        task = asyncio.create_task(old.execute(self.run_id))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await old.aclose()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.repo.get(self.run_id)["status"], "paused")
            release.set()
            async with AssistantRunSupervisor(self.repo, graph_runner(self.checkpoints, first_observer=lambda: calls.append("first"))) as new:
                result = await new.execute(self.run_id)
                repeat = await new.execute(self.run_id)
            self.assertEqual(result, repeat)
            self.assertEqual(result["output_json"], '{"count":11}')
            self.assertEqual(result["tool_calls"], 2)
            self.assertEqual(calls, ["first"])
        finally:
            release.set()
            await old.aclose()

    async def test_process_crash_after_checkpoint_recovers_without_replaying_finished_node(self):
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        child = ctx.Process(target=_crash_after_checkpoint, args=(str(self.repo.db_path), str(self.checkpoints), self.run_id, ready))
        child.start()
        try:
            self.assertTrue(await asyncio.to_thread(ready.wait, 20))
            await asyncio.to_thread(child.join, 10)
            self.assertEqual(child.exitcode, 23)
            self.now = 1300
            calls = []
            async with AssistantRunSupervisor(self.repo, graph_runner(self.checkpoints, first_observer=lambda: calls.append("unexpected"))) as supervisor:
                result = await supervisor.execute(self.run_id)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["output_json"], '{"count":11}')
            self.assertEqual(result["tool_calls"], 2)
            self.assertEqual(calls, [])
        finally:
            if child.is_alive():
                child.terminate()
            child.join(5)

    async def test_deadline_waits_for_checkpoint_commit_then_persists_timeout(self):
        entered, release = asyncio.Event(), asyncio.Event()
        session = uuid4().hex
        create_assistant_session(session, "提交超时")
        run_id = self.repo.admit(session, uuid4().hex, {}, timeout_ms=80).run["id"]

        async def runner(context):
            async with open_run_checkpoints(self.repo.db_path, self.checkpoints, context.lease, clock=self.repo.clock) as saver:
                config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
                await saver.aget_tuple(config)
                original = saver._delegate.conn.commit
                async def delayed():
                    entered.set()
                    await release.wait()
                    await original()
                from langgraph.checkpoint.base import empty_checkpoint
                with patch.object(saver._delegate.conn, "commit", delayed):
                    await saver.aput(config, empty_checkpoint(), {}, {})
            return RunResult(answer="过时结果不能发布")

        supervisor = AssistantRunSupervisor(self.repo, runner, lease_ms=300, heartbeat_ms=100)
        task = asyncio.create_task(supervisor.execute(run_id))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(0.12)
            self.assertFalse(task.done())
            release.set()
            result = await asyncio.wait_for(task, 5)
            self.assertEqual(result["status"], "timed_out")
            self.assertEqual(result["output_json"], "{}")
        finally:
            release.set()
            await supervisor.aclose()

    async def test_independent_process_supervisors_do_not_execute_same_run_twice(self):
        ctx = multiprocessing.get_context("spawn")
        ready, output = ctx.Event(), ctx.Queue()
        marker = self.root / "calls.log"
        children = [ctx.Process(target=_contest, args=(str(self.repo.db_path), self.run_id, str(marker), ready, output)) for _ in range(3)]
        try:
            for child in children:
                child.start()
            ready.set()
            values = await asyncio.to_thread(lambda: [output.get(timeout=25) for _ in children])
            for child in children:
                await asyncio.to_thread(child.join, 5)
                self.assertEqual(child.exitcode, 0)
            self.assertTrue(set(values) <= {"running", "succeeded"})
            self.assertEqual(marker.read_text(encoding="utf-8"), "执行一次\n")
            self.assertEqual(self.repo.get(self.run_id)["tool_calls"], 1)
            self.assertEqual(self.repo.get(self.run_id)["status"], "succeeded")
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join(5)
            output.close()
            output.join_thread()


if __name__ == "__main__":
    unittest.main()

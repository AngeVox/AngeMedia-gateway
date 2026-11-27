"""使用真实框架、SQLite和独立进程验证检查点接管边界；不请求模型。"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, closing
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.assistant_checkpoints import open_run_checkpoints
from angemedia_gateway.assistant_runtime_contract import RunLeaseLost
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session, delete_assistant_session
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _child_write(runtime, checkpoints, run_id, ready, output):
    C.DB_FILE = Path(runtime)
    repo = AssistantRunRepository(clock=lambda: 1000)
    ready.wait(20)
    lease = repo.claim(run_id)

    async def execute():
        async with open_run_checkpoints(Path(runtime), Path(checkpoints), lease, clock=lambda: 1000) as saver:
            config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
            saved = await saver.aput(config, empty_checkpoint(), {}, {})
            await saver.aput_writes(saved, [("messages", "进程结果")], "worker")
            output.put((run_id, (await saver.aget_tuple(saved)).pending_writes))
    asyncio.run(execute())


def _child_stale_write(runtime, checkpoints, lease, ready, replaced, output):
    async def execute():
        async with open_run_checkpoints(Path(runtime), Path(checkpoints), lease, clock=lambda: 1000) as saver:
            config = {"configurable": {"thread_id": lease.run_id, "checkpoint_ns": ""}}
            saved = await saver.aput(config, empty_checkpoint(), {}, {})
            ready.set()
            await asyncio.to_thread(replaced.wait, 20)
            for operation in (lambda: saver.aput(config, empty_checkpoint(), {}, {}),
                              lambda: saver.aput_writes(saved, [("messages", "旧进程")], "late")):
                try:
                    await operation()
                    output.put("unexpected-write")
                except RunLeaseLost:
                    output.put("fenced")
    asyncio.run(execute())


class CheckpointFenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="assistant-checkpoint-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original = C.DB_FILE
        C.DB_FILE = self.root / "runtime.db"
        self.addCleanup(setattr, C, "DB_FILE", self.original)
        init_db()
        self.now = 1000
        self.repo = AssistantRunRepository(clock=lambda: self.now)
        self.session = uuid4().hex
        create_assistant_session(self.session, "检查点会话")
        self.run_id = self.repo.admit(self.session, uuid4().hex, {"message": "中文需求"}).run["id"]
        self.lease = self.repo.claim(self.run_id, lease_ms=100)
        self.checkpoints = self.root / "checkpoints.db"
        self.config = {"configurable": {"thread_id": self.run_id, "checkpoint_ns": ""}}

    def open(self, lease=None):
        return open_run_checkpoints(C.DB_FILE, self.checkpoints, lease or self.lease, clock=lambda: self.now)

    def count(self, table):
        with closing(sqlite3.connect(self.checkpoints)) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def assert_authority_locked(self):
        with closing(sqlite3.connect(C.DB_FILE, timeout=0.02)) as conn:
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                conn.execute("BEGIN IMMEDIATE")

    async def test_typed_messages_and_pending_writes_survive_new_connection(self):
        checkpoint = empty_checkpoint()
        messages = [HumanMessage(content="保持蓝色\n第二行", id="u1"),
                    AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"id": "素材"}, "id": "t1"}], id="a1"),
                    ToolMessage(content="中文结果", tool_call_id="t1", id="r1")]
        checkpoint["channel_values"] = {"messages": messages}
        async with self.open() as saver:
            saved = await saver.aput(self.config, checkpoint, {"step": 0}, {})
            await saver.aput_writes(saved, [("messages", messages[-1])], "task", "工具路径")
        async with self.open() as saver:
            restored = await saver.aget_tuple(saved)
            self.assertEqual(restored.checkpoint["channel_values"]["messages"], messages)
            self.assertEqual(restored.pending_writes[0][2], messages[-1])
            self.assertEqual(len([row async for row in saver.alist(self.config)]), 1)

    async def test_real_graph_recovers_after_takeover_and_terminal_replay_does_no_work(self):
        calls = []

        async def first(value):
            calls.append("first")
            return {"count": value["count"] + 1}

        async def second(value):
            calls.append("second")
            return {"count": value["count"] + 10}

        from typing import TypedDict
        class State(TypedDict):
            count: int

        builder = StateGraph(State)
        builder.add_node("first", first)
        builder.add_node("second", second)
        builder.add_edge(START, "first")
        builder.add_edge("first", "second")
        builder.add_edge("second", END)
        async with self.open() as saver:
            graph = builder.compile(checkpointer=saver, interrupt_after=["first"])
            self.assertEqual((await graph.ainvoke({"count": 0}, self.config, durability="sync"))["count"], 1)
        self.repo.pause(self.lease)
        new = self.repo.claim(self.run_id)
        async with self.open(new) as saver:
            graph = builder.compile(checkpointer=saver)
            self.assertEqual((await graph.ainvoke(None, self.config, durability="sync"))["count"], 11)
            self.assertEqual((await graph.ainvoke(None, self.config, durability="sync"))["count"], 11)
        self.assertEqual(calls, ["first", "second"])

    async def test_expired_owner_cannot_read_write_or_initialize_checkpoint_schema(self):
        self.now = 1100
        new = self.repo.claim(self.run_id)
        async with self.open() as old:
            for operation in (lambda: old.aput(self.config, empty_checkpoint(), {}, {}),
                              lambda: old.aput_writes(self.config, [("x", 1)], "task"),
                              lambda: old.aget_tuple(self.config)):
                with self.assertRaises(RunLeaseLost):
                    await operation()
        with closing(sqlite3.connect(self.checkpoints)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)
        async with self.open(new) as saver:
            await saver.aput(self.config, empty_checkpoint(), {}, {})
        self.assertEqual(self.count("checkpoints"), 1)

    async def test_cancel_finish_pause_archive_and_deadline_revoke_checkpoint_access(self):
        for transition in ("cancel", "finish", "pause", "archive", "deadline"):
            with self.subTest(transition=transition):
                session = uuid4().hex
                create_assistant_session(session, "终态边界")
                run_id = self.repo.admit(session, uuid4().hex, {}, timeout_ms=100).run["id"]
                lease = self.repo.claim(run_id)
                config = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
                async with self.open(lease) as saver:
                    await saver.aput(config, empty_checkpoint(), {}, {})
                    if transition == "archive":
                        delete_assistant_session(session)
                    elif transition == "deadline":
                        self.now += 100
                    elif transition == "cancel":
                        self.repo.cancel(run_id)
                    else:
                        getattr(self.repo, transition)(lease)
                    from angemedia_gateway.assistant_runtime_contract import RunNotFound
                    with self.assertRaises((RunLeaseLost, RunNotFound)):
                        await saver.aput(config, empty_checkpoint(), {}, {})

    async def test_wrong_scope_alias_file_and_unbounded_lists_are_rejected(self):
        async with self.open() as saver:
            wrong = {"configurable": {"thread_id": "另一个运行", "checkpoint_ns": ""}}
            for config in (None, wrong):
                with self.assertRaises(ValueError):
                    await saver.aget_tuple(config)
                with self.assertRaises(ValueError):
                    await saver.aput(config, empty_checkpoint(), {}, {})
            for limit in (0, 501, True):
                with self.assertRaises(ValueError):
                    _ = [x async for x in saver.alist(self.config, limit=limit)]
            with self.assertRaises(ValueError):
                _ = [x async for x in saver.alist(self.config, before=wrong)]
            with self.assertRaises(NotImplementedError):
                await saver.adelete_thread(self.run_id)
        for path in (C.DB_FILE, self.root / "runtime-link.db"):
            if path != C.DB_FILE:
                path.hardlink_to(C.DB_FILE)
            with self.assertRaises(ValueError):
                async with open_run_checkpoints(C.DB_FILE, path, self.lease):
                    self.fail("同一文件不得打开为两个库")
        with self.assertRaises(FileNotFoundError):
            async with open_run_checkpoints(self.root / "missing.db", self.checkpoints, self.lease):
                self.fail("不得静默创建运行库")

    async def test_commit_failure_rolls_back_checkpoint_and_intermediate_writes(self):
        async with self.open() as saver:
            await saver.aget_tuple(self.config)
            for kind in ("checkpoint", "writes"):
                operation = (lambda: saver.aput(self.config, empty_checkpoint(), {}, {})) if kind == "checkpoint" else (
                    lambda: saver.aput_writes({"configurable": {**self.config["configurable"], "checkpoint_id": "cp"}}, [("x", 1)], "task"))
                with patch.object(saver._delegate.conn, "commit", side_effect=sqlite3.OperationalError("故障注入")):
                    with self.assertRaises(sqlite3.OperationalError):
                        await operation()
                self.assertEqual(self.count("checkpoints"), 0)
                self.assertEqual(self.count("writes"), 0)
            await saver.aput(self.config, empty_checkpoint(), {}, {})
            self.assertEqual(self.count("checkpoints"), 1)

    async def test_repeated_cancel_during_commit_keeps_authority_gate_until_settled(self):
        async with self.open() as saver:
            await saver.aget_tuple(self.config)
            entered, release = asyncio.Event(), asyncio.Event()
            original_commit = saver._delegate.conn.commit

            async def delayed_commit():
                entered.set()
                await release.wait()
                await original_commit()

            with patch.object(saver._delegate.conn, "commit", delayed_commit):
                task = asyncio.create_task(saver.aput(self.config, empty_checkpoint(), {}, {}))
                await asyncio.wait_for(entered.wait(), 5)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    await asyncio.to_thread(self.assert_authority_locked)
                # 在锁内开始的写入先收尾，接管只能在线性化点之后发生。
                self.now = 1100
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            new = self.repo.claim(self.run_id)
            with self.assertRaises(RunLeaseLost):
                await saver.aput(self.config, empty_checkpoint(), {}, {})
        async with self.open(new) as saver:
            self.assertIsNotNone(await saver.aget_tuple(self.config))
        self.assertEqual(self.count("checkpoints"), 1)

    async def test_partial_write_error_rolls_back_before_authority_is_released(self):
        async with self.open() as saver:
            await saver.aget_tuple(self.config)

            async def partial_failure(*args):
                await saver._delegate.conn.execute(
                    "INSERT INTO checkpoints(thread_id,checkpoint_id) VALUES(?,?)", (self.run_id, "partial"),
                )
                raise RuntimeError("写入后故障")

            with patch.object(saver._delegate, "aput", side_effect=partial_failure):
                with self.assertRaises(RuntimeError):
                    await saver.aput(self.config, empty_checkpoint(), {}, {})
            self.assertEqual(self.count("checkpoints"), 0)
            self.repo.renew(self.lease)

    async def test_rollback_failure_closes_checkpoint_connection_before_unlocking(self):
        async with self.open() as saver:
            await saver.aget_tuple(self.config)

            async def partial_failure(*args):
                await saver._delegate.conn.execute(
                    "INSERT INTO checkpoints(thread_id,checkpoint_id) VALUES(?,?)", (self.run_id, "partial"),
                )
                raise RuntimeError("写入后故障")

            with patch.object(saver._delegate, "aput", side_effect=partial_failure), patch.object(
                saver._delegate.conn, "rollback", side_effect=sqlite3.OperationalError("回滚故障"),
            ):
                with self.assertRaises(RuntimeError):
                    await saver.aput(self.config, empty_checkpoint(), {}, {})
            self.assertEqual(self.count("checkpoints"), 0)
            with self.assertRaises(RuntimeError):
                await saver.aget_tuple(self.config)
            self.repo.renew(self.lease)

    async def test_escaping_factory_closes_saver_and_allowlist_clones(self):
        async with self.open() as saver:
            clone = saver.with_allowlist({("example", "State")})
            self.assertIs(clone._lock, saver._lock)
            self.assertIs(clone._delegate.serde, clone.serde)
            await clone.aget_tuple(self.config)
        for instance in (saver, clone):
            with self.assertRaises(RuntimeError):
                await instance.aget_tuple(self.config)

    async def test_canceled_connection_acquisition_closes_already_registered_resource(self):
        entered, release = asyncio.Event(), asyncio.Event()
        acquired = []
        original = AsyncExitStack.enter_async_context

        async def delayed_enter(stack, context):
            connection = await original(stack, context)
            acquired.append(connection)
            entered.set()
            await release.wait()
            return connection

        async def opening():
            async with self.open():
                self.fail("取消后不应向调用者交付连接")

        with patch.object(AsyncExitStack, "enter_async_context", delayed_enter):
            task = asyncio.create_task(opening())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(acquired), 1)
        with self.assertRaises(ValueError):
            await acquired[0].execute("SELECT 1")
        self.repo.renew(self.lease)

    async def test_cancellation_during_schema_setup_does_not_release_authority_early(self):
        async with self.open() as saver:
            entered, release = asyncio.Event(), asyncio.Event()
            original = saver._delegate.setup

            async def delayed_setup():
                entered.set()
                await release.wait()
                await original()

            with patch.object(saver._delegate, "setup", delayed_setup):
                task = asyncio.create_task(saver.aget_tuple(self.config))
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                await asyncio.to_thread(self.assert_authority_locked)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.repo.renew(self.lease)
            self.assertIsNone(await saver.aget_tuple(self.config))

    async def test_two_processes_initialize_shared_checkpoint_file_without_schema_race(self):
        ctx = multiprocessing.get_context("spawn")
        ready, output = ctx.Event(), ctx.Queue()
        children = []
        try:
            for _ in range(3):
                session = uuid4().hex
                create_assistant_session(session, "独立运行")
                run_id = self.repo.admit(session, uuid4().hex, {}).run["id"]
                child = ctx.Process(target=_child_write, args=(str(C.DB_FILE), str(self.checkpoints), run_id, ready, output))
                child.start()
                children.append(child)
            ready.set()
            values = await asyncio.to_thread(lambda: [output.get(timeout=25) for _ in children])
            for child in children:
                await asyncio.to_thread(child.join, 5)
                self.assertEqual(child.exitcode, 0)
            self.assertEqual(len({row[0] for row in values}), 3)
            self.assertTrue(all(row[1][0][2] == "进程结果" for row in values))
            self.assertEqual(self.count("checkpoints"), 3)
            self.assertEqual(self.count("writes"), 3)
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join(5)
            output.close()
            output.join_thread()

    async def test_old_process_with_stale_clock_cannot_publish_after_new_fence(self):
        ctx = multiprocessing.get_context("spawn")
        ready, replaced, output = ctx.Event(), ctx.Event(), ctx.Queue()
        child = ctx.Process(target=_child_stale_write, args=(str(C.DB_FILE), str(self.checkpoints), self.lease, ready, replaced, output))
        child.start()
        try:
            self.assertTrue(await asyncio.to_thread(ready.wait, 20))
            self.now = 1100
            new = self.repo.claim(self.run_id)
            replaced.set()
            values = await asyncio.to_thread(lambda: [output.get(timeout=20) for _ in range(2)])
            await asyncio.to_thread(child.join, 5)
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(values, ["fenced", "fenced"])
            async with self.open(new) as saver:
                self.assertIsNotNone(await saver.aget_tuple(self.config))
            self.assertEqual(self.count("checkpoints"), 1)
            self.assertEqual(self.count("writes"), 0)
        finally:
            replaced.set()
            if child.is_alive():
                child.terminate()
            child.join(5)
            output.close()
            output.join_thread()


if __name__ == "__main__":
    unittest.main()

"""真实SQLite下的异步控制、容量、取消与故障收尾；不依赖模型SDK。"""
from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.assistant_runtime_contract import RunBusy, RunLeaseLost
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session, delete_assistant_session, list_assistant_messages
from angemedia_gateway.services.assistant_run_supervisor import AssistantRunSupervisor


class AssistantSupervisorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="assistant-supervisor-")
        self.addCleanup(self.temp.cleanup)
        self.original = C.DB_FILE
        self.addCleanup(setattr, C, "DB_FILE", self.original)
        C.DB_FILE = Path(self.temp.name) / "state.db"
        init_db()
        self.now = 1000
        self.repo = AssistantRunRepository(clock=lambda: self.now)
        self.controllers = []
        self.session = uuid4().hex
        create_assistant_session(self.session, "控制测试")

    async def asyncTearDown(self):
        for supervisor in self.controllers:
            await supervisor.aclose()

    def admit(self, *, session=None, **kwargs):
        return self.repo.admit(session or self.session, uuid4().hex, {"message": "保留蓝色"}, **kwargs).run["id"]

    def controller(self, runner, **kwargs):
        supervisor = AssistantRunSupervisor(self.repo, runner, lease_ms=90, heartbeat_ms=20, **kwargs)
        self.controllers.append(supervisor)
        return supervisor

    async def until(self, predicate):
        async def poll():
            while not await asyncio.to_thread(predicate):
                await asyncio.sleep(0.005)
        await asyncio.wait_for(poll(), 5)

    async def test_success_publishes_once_and_terminal_replay_never_invokes_runner(self):
        calls = []
        async def runner(context):
            calls.append(context.input)
            await context.publish_message("user", context.input["message"])
            await context.reserve_call("model")
            await context.reserve_call("tool")
            await context.append_event("tool_finished", {"asset_id": "asset"})
            return RunResult(answer="完成\n中文结果", output={"asset_id": "asset"})
        run_id = self.admit()
        supervisor = self.controller(runner)
        result = await supervisor.execute(run_id)
        repeat = await supervisor.execute(run_id)
        self.assertEqual(result, repeat)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual((result["model_calls"], result["tool_calls"]), (1, 1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(list_assistant_messages(self.session)), 2)

    async def test_waiter_disconnect_does_not_cancel_execution(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def runner(context):
            entered.set()
            await release.wait()
            return RunResult(answer="继续完成")
        supervisor = self.controller(runner)
        run_id = self.admit()
        waiter = asyncio.create_task(supervisor.execute(run_id))
        await entered.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(self.repo.get(run_id)["status"], "running")
        self.assertEqual(supervisor.active_count, 1)
        release.set()
        self.assertEqual((await supervisor.execute(run_id))["status"], "succeeded")

    async def test_explicit_cancel_is_durable_and_prevents_followup_calls(self):
        entered, stopped = asyncio.Event(), asyncio.Event()
        attempts = []
        async def runner(context):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                with self.assertRaises(RunLeaseLost):
                    await context.reserve_call("tool")
                attempts.append("blocked")
                stopped.set()
        supervisor = self.controller(runner)
        run_id = self.admit()
        waiter = asyncio.create_task(supervisor.execute(run_id))
        await entered.wait()
        self.assertEqual((await supervisor.cancel(run_id))["status"], "canceled")
        self.assertEqual((await waiter)["status"], "canceled")
        await stopped.wait()
        self.assertEqual(attempts, ["blocked"])
        self.assertEqual(self.repo.get(run_id)["tool_calls"], 0)

    async def test_foreign_cancel_and_archive_are_seen_by_heartbeat(self):
        for transition in ("cancel", "archive"):
            with self.subTest(transition=transition):
                entered, stopped = asyncio.Event(), asyncio.Event()
                async def runner(context):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        stopped.set()
                session = uuid4().hex
                create_assistant_session(session, "外部操作")
                run_id = self.admit(session=session)
                supervisor = self.controller(runner)
                task = asyncio.create_task(supervisor.execute(run_id))
                await entered.wait()
                if transition == "cancel":
                    await asyncio.to_thread(self.repo.cancel, run_id)
                else:
                    await asyncio.to_thread(delete_assistant_session, session)
                result = await asyncio.wait_for(task, 3)
                self.assertTrue(stopped.is_set())
                self.assertEqual(result["status"] if result else None, "canceled" if transition == "cancel" else None)

    async def test_heartbeat_renews_and_competing_owner_cannot_take_over(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def runner(context):
            entered.set()
            await release.wait()
            return RunResult()
        run_id = self.admit()
        supervisor = self.controller(runner)
        task = asyncio.create_task(supervisor.execute(run_id))
        await entered.wait()
        self.now = 1050
        def renewed():
            with closing(sqlite3.connect(self.repo.db_path)) as conn:
                return conn.execute("SELECT expires_ms FROM assistant_session_leases WHERE session_id=?", (self.session,)).fetchone()[0] == 1140
        await self.until(renewed)
        contender = AssistantRunRepository(clock=lambda: 1100, db_path=self.repo.db_path)
        with self.assertRaises(RunBusy):
            await asyncio.to_thread(contender.claim, run_id)
        release.set()
        self.assertEqual((await task)["status"], "succeeded")

    async def test_takeover_stops_old_runner_without_finishing_or_pausing_new_owner(self):
        entered = asyncio.Event()
        async def runner(context):
            entered.set()
            await asyncio.Event().wait()
        run_id = self.admit()
        supervisor = self.controller(runner)
        task = asyncio.create_task(supervisor.execute(run_id))
        await entered.wait()
        self.now = 1090
        new = self.repo.claim(run_id)
        result = await asyncio.wait_for(task, 3)
        self.assertEqual(result["status"], "running")
        self.repo.finish(new, answer="新持有者")
        self.assertEqual([m["content"] for m in list_assistant_messages(self.session)], ["新持有者"])

    async def test_total_monotonic_deadline_survives_wall_clock_rollback(self):
        entered, stopped = asyncio.Event(), asyncio.Event()
        async def runner(context):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        run_id = self.admit(timeout_ms=120)
        supervisor = self.controller(runner)
        task = asyncio.create_task(supervisor.execute(run_id))
        await entered.wait()
        self.now = 500
        result = await asyncio.wait_for(task, 3)
        self.assertEqual(result["status"], "timed_out")
        self.assertTrue(stopped.is_set())

    async def test_budget_exhaustion_remains_terminal_even_if_engine_returns_success(self):
        async def runner(context):
            await context.reserve_call("model")
            try:
                await context.reserve_call("model")
            except Exception:
                return RunResult(answer="不能发布")
        run_id = self.admit(max_model_calls=1)
        supervisor = self.controller(runner)
        result = await supervisor.execute(run_id)
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(list_assistant_messages(self.session), [])

    async def test_engine_failure_and_invalid_payload_are_safe_terminal_results(self):
        async def broken(context):
            raise RuntimeError("Authorization: Bearer private-test-token /private/config.env")
        async def invalid(context):
            return RunResult(output={"bad": object()})
        async def canceled(context):
            raise asyncio.CancelledError()
        for runner, code in ((broken, "engine_error"), (invalid, "invalid_engine_result"), (canceled, "engine_canceled")):
            run_id = self.admit()
            result = await self.controller(runner).execute(run_id)
            self.assertEqual(result["status"], "failed")
            self.assertIn(code, result["output_json"])
            self.assertNotIn("private-test-token", repr(self.repo.events(run_id)))

    async def test_capacity_fifo_and_recovery_scan_do_not_create_unbounded_tasks(self):
        entered, release = asyncio.Event(), asyncio.Event()
        order = []
        async def runner(context):
            order.append(context.lease.run_id)
            entered.set()
            await release.wait()
            return RunResult()
        first, second = self.admit(), self.admit()
        supervisor = self.controller(runner, concurrency=1)
        self.assertEqual(await supervisor.recover_once(), 1)
        await entered.wait()
        self.assertEqual(await supervisor.recover_once(), 0)
        with self.assertRaises(RunBusy):
            await supervisor.execute(second)
        self.assertEqual(self.repo.get(second)["status"], "queued")
        release.set()
        await self.until(lambda: self.repo.get(first)["status"] == "succeeded" and supervisor.active_count == 0)
        self.assertEqual(await supervisor.recover_once(), 1)
        await self.until(lambda: self.repo.get(second)["status"] == "succeeded")
        self.assertEqual(order, [first, second])

    async def test_graceful_shutdown_pauses_owned_run_and_new_supervisor_recovers(self):
        entered = asyncio.Event()
        async def blocked(context):
            entered.set()
            await asyncio.Event().wait()
        run_id = self.admit()
        old = self.controller(blocked)
        waiter = asyncio.create_task(old.execute(run_id))
        await entered.wait()
        await old.aclose()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(self.repo.get(run_id)["status"], "paused")
        async def recovered(context):
            return RunResult(answer="恢复完成")
        new = self.controller(recovered)
        self.assertEqual(await new.recover_once(), 1)
        await self.until(lambda: self.repo.get(run_id)["status"] == "succeeded")
        self.assertEqual(old.active_count, 0)

    async def test_shutdown_during_thread_claim_waits_and_releases_acquired_lease(self):
        entered, release = threading.Event(), threading.Event()
        original = self.repo.claim
        async def runner(context):
            self.fail("关闭后不应启动引擎")
        def delayed_claim(*args, **kwargs):
            lease = original(*args, **kwargs)
            entered.set()
            release.wait(5)
            return lease
        supervisor = self.controller(runner)
        run_id = self.admit()
        with patch.object(self.repo, "claim", delayed_claim):
            waiter = asyncio.create_task(supervisor.execute(run_id))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            closing_task = asyncio.create_task(supervisor.aclose())
            await asyncio.sleep(0)
            closing_task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closing_task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing_task
            with self.assertRaises(asyncio.CancelledError):
                await waiter
        self.assertEqual(self.repo.get(run_id)["status"], "paused")

    async def test_slow_sqlite_operation_does_not_block_other_coroutines(self):
        entered, release = threading.Event(), threading.Event()
        original = self.repo.reserve_call
        def delayed(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        async def runner(context):
            await context.reserve_call("model")
            return RunResult()
        supervisor = self.controller(runner)
        with patch.object(self.repo, "reserve_call", delayed):
            task = asyncio.create_task(supervisor.execute(self.admit()))
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            ticks = []
            for _ in range(5):
                await asyncio.sleep(0)
                ticks.append("healthy")
            self.assertEqual(len(ticks), 5)
            self.assertFalse(task.done())
            release.set()
            await task

    async def test_expired_queued_head_is_reaped_before_next_turn_and_service_stops(self):
        first = self.admit(timeout_ms=1)
        second = self.admit()
        self.now = 1001
        async def runner(context):
            return RunResult()
        supervisor = self.controller(runner)
        stop = asyncio.Event()
        service = asyncio.create_task(supervisor.serve(stop, interval_ms=10))
        await self.until(lambda: self.repo.get(second)["status"] == "succeeded")
        self.assertEqual(self.repo.get(first)["status"], "timed_out")
        stop.set()
        await service
        with self.assertRaises(RuntimeError):
            await supervisor.execute(second)

    async def test_repository_database_identity_is_pinned_across_config_changes(self):
        run_id = self.admit()
        original_path = self.repo.db_path
        C.DB_FILE = Path(self.temp.name) / "other.db"
        init_db()
        async def runner(context):
            self.assertEqual(context.repository.db_path, original_path)
            return RunResult()
        result = await self.controller(runner).execute(run_id)
        self.assertEqual(result["status"], "succeeded")
        with closing(sqlite3.connect(C.DB_FILE)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assistant_runs").fetchone()[0], 0)

    async def test_cancellation_cleanup_keeps_capacity_until_engine_actually_exits(self):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def runner(context):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                with self.assertRaises(RunLeaseLost):
                    await context.reserve_call("tool")
                return RunResult(answer="过时结果")
        first, second = self.admit(), self.admit()
        supervisor = self.controller(runner, concurrency=1)
        task = asyncio.create_task(supervisor.execute(first))
        try:
            await entered.wait()
            await supervisor.cancel(first)
            await cleaning.wait()
            self.assertEqual(supervisor.active_count, 1)
            with self.assertRaises(RunBusy):
                await supervisor.execute(second)
            release.set()
            self.assertEqual((await task)["status"], "canceled")
            self.assertEqual(list_assistant_messages(self.session), [])
        finally:
            release.set()

    async def test_old_owner_cannot_timeout_replacement_or_completed_run(self):
        run_id = self.admit()
        old = self.repo.claim(run_id, lease_ms=10)
        self.now = 1010
        new = self.repo.claim(run_id)
        with self.assertRaises(RunLeaseLost):
            await asyncio.to_thread(self.repo.timeout_owned, old)
        self.repo.finish(new)
        with self.assertRaises(RunLeaseLost):
            await asyncio.to_thread(self.repo.timeout_owned, new)
        self.assertEqual(self.repo.get(run_id)["status"], "succeeded")

    async def test_terminal_replay_does_not_consume_busy_execution_capacity(self):
        terminal = self.admit()
        lease = self.repo.claim(terminal)
        self.repo.finish(lease)
        active = self.admit()
        entered, release = asyncio.Event(), asyncio.Event()
        async def runner(context):
            entered.set()
            await release.wait()
            return RunResult()
        supervisor = self.controller(runner, concurrency=1)
        task = asyncio.create_task(supervisor.execute(active))
        try:
            await entered.wait()
            self.assertEqual((await supervisor.execute(terminal))["status"], "succeeded")
            self.assertEqual(supervisor.active_count, 1)
        finally:
            release.set()
            await task

    async def test_retiring_expired_owner_does_not_hide_other_sessions_from_recovery(self):
        first = self.admit()
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def runner(context):
            if context.lease.run_id != first:
                return RunResult()
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                return RunResult()
        supervisor = self.controller(runner, concurrency=2)
        task = asyncio.create_task(supervisor.execute(first))
        try:
            await entered.wait()
            self.now = 1090
            await cleaning.wait()
            session = uuid4().hex
            create_assistant_session(session, "另一个会话")
            second = self.admit(session=session)
            self.assertEqual(await supervisor.recover_once(limit=1), 1)
            await self.until(lambda: self.repo.get(second)["status"] == "succeeded")
        finally:
            release.set()
            await task


if __name__ == "__main__":
    unittest.main()

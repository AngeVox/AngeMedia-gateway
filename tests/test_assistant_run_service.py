"""应用拥有的恢复任务、请求重放、关闭恢复、并发和保留调度合同。"""
from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_run_support import RunFixture
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.assistant_run_access import RunUnavailable
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.services.assistant_run_service import AssistantRunService


class RunServiceTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.services = []

    async def asyncTearDown(self):
        for service in self.services:
            await service.aclose()

    async def service(self, runner=None, **kwargs):
        service = AssistantRunService(AssistantRunRepository(account_only=True), runner=runner,
            recovery_interval_ms=10, lease_ms=3000, heartbeat_ms=100, **kwargs)
        self.services.append(service)
        await service.start()
        return service

    async def wait_status(self, service, run_id, status):
        async def wait():
            while True:
                snapshot = await service.inspect(run_id, access=self.access["alice"])
                if snapshot["run"]["status"] == status:
                    return snapshot
                await asyncio.sleep(0.005)
        return await asyncio.wait_for(wait(), 5)

    async def submit(self, service, key=None, message="保持蓝色"):
        return await service.submit(self.session, key or uuid4().hex, message, access=self.access["alice"])

    async def test_concurrent_retry_reuses_one_run_and_one_execution(self):
        calls = []
        async def runner(context):
            calls.append(context.lease.run_id)
            await context.reserve_call("model")
            await context.publish_message("user", context.input["message"])
            return RunResult(answer="完成")
        service = await self.service(runner)
        key = uuid4().hex
        first, second = await asyncio.gather(self.submit(service, key), self.submit(service, key))
        self.assertEqual(first["run"]["id"], second["run"]["id"])
        self.assertEqual(sorted((first["created"], second["created"])), [False, True])
        run_id = first["run"]["id"]
        completed = await self.wait_status(service, run_id, "succeeded")
        replay = await self.submit(service, key)
        self.assertEqual(replay["run"], completed["run"])
        self.assertEqual(calls, [run_id])
        self.assertEqual(completed["run"]["model_calls"], 1)

    async def test_disconnected_submit_finishes_commit_and_retry_does_not_duplicate(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        async def runner(context):
            calls.append(context.lease.run_id)
            return RunResult(answer="后台完成")
        service = await self.service(runner)
        original = service.repository.admit
        def blocked_admit(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        key = uuid4().hex
        with patch.object(service.repository, "admit", blocked_admit):
            task = asyncio.create_task(self.submit(service, key))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        replay = await self.submit(service, key)
        self.assertFalse(replay["created"])
        await self.wait_status(service, replay["run"]["id"], "succeeded")
        self.assertEqual(calls, [replay["run"]["id"]])

    async def test_explicit_cancel_stops_local_runner_and_replay_keeps_terminal_state(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def runner(context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        service = await self.service(runner)
        key = uuid4().hex
        run_id = (await self.submit(service, key))["run"]["id"]
        await asyncio.wait_for(started.wait(), 3)
        canceled = await service.cancel(run_id, access=self.access["alice"])
        self.assertEqual(canceled["run"]["status"], "canceled")
        await asyncio.wait_for(stopped.wait(), 3)
        self.assertEqual((await self.submit(service, key))["run"]["status"], "canceled")
        self.assertEqual((await service.cancel(run_id, access=self.access["alice"]))["run"], canceled["run"])

    async def test_shutdown_drains_and_replacement_service_resumes_paused_run(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def first_runner(context):
            started.set()
            try:
                await context.publish_message("user", context.input["message"])
                await asyncio.Event().wait()
            finally:
                stopped.set()
        service = await self.service(first_runner)
        run_id = (await self.submit(service))["run"]["id"]
        await asyncio.wait_for(started.wait(), 3)
        await service.aclose()
        self.assertTrue(stopped.is_set())
        self.assertEqual(service.supervisor.active_count, 0)
        self.assertEqual(self.repo.get(run_id)["status"], "paused")
        async def second_runner(context):
            await context.publish_message("user", context.input["message"])
            return RunResult(answer="恢复完成")
        replacement = await self.service(second_runner)
        result = await self.wait_status(replacement, run_id, "succeeded")
        self.assertEqual(result["run"]["answer"], "恢复完成")
        self.assertIn("resumed", [item["event_type"] for item in result["events"]])

    async def test_two_recovery_services_compete_for_one_durable_lease(self):
        calls = []
        async def runner(context):
            calls.append(context.lease.run_id)
            await asyncio.sleep(0.05)
            return RunResult(answer="仅执行一次")
        first = await self.service(runner)
        second = await self.service(runner)
        run_id = (await self.submit(first))["run"]["id"]
        await self.wait_status(second, run_id, "succeeded")
        self.assertEqual(calls, [run_id])

    async def test_same_conversation_remains_fifo(self):
        release, started = asyncio.Event(), asyncio.Event()
        calls = []
        async def runner(context):
            calls.append(context.input["message"])
            if len(calls) == 1:
                started.set()
                await release.wait()
            return RunResult(answer=context.input["message"])
        service = await self.service(runner)
        await self.submit(service, message="第一轮")
        await asyncio.wait_for(started.wait(), 3)
        second = await self.submit(service, message="第二轮")
        self.assertEqual(second["run"]["status"], "queued")
        release.set()
        await self.wait_status(service, second["run"]["id"], "succeeded")
        self.assertEqual(calls, ["第一轮", "第二轮"])

    async def test_runtime_fault_stops_admission_but_preserves_read_and_cancel(self):
        async def runner(context):
            self.fail("故障恢复器不应执行引擎")
        run_id = self.repo.admit(self.session, uuid4().hex, {"message": "已有运行"}).run["id"]
        with patch.object(AssistantRunRepository, "ready", side_effect=RuntimeError("private-error-body")):
            with self.assertLogs("angemedia_gateway.services.assistant_run_service", level="ERROR") as logs:
                service = await self.service(runner)
                await asyncio.wait_for(service._recovery_task, 3)
            self.assertNotIn("private-error-body", str(logs.output))
            self.assertEqual(service.status(), {"state": "unavailable", "can_submit": False})
            with self.assertRaises(RunUnavailable):
                await self.submit(service)
            self.assertEqual((await service.inspect(run_id, access=self.access["alice"]))["run"]["status"], "queued")
            self.assertEqual((await service.cancel(run_id, access=self.access["alice"]))["run"]["status"], "canceled")

    async def test_disabled_service_is_explicit_and_never_enqueues_new_work(self):
        service = await self.service()
        self.assertEqual(service.status(), {"state": "tools_not_connected", "can_submit": False})
        with self.assertRaises(RunUnavailable):
            await self.submit(service)
        self.assertEqual(self.repo.ready(), [])

    async def test_disabled_execution_still_expires_existing_owned_runs_without_calling_engine(self):
        expired = AssistantRunRepository(account_only=True, clock=lambda: 0)
        run_id = expired.admit(self.session, uuid4().hex, {"message": "已有过期运行"}, timeout_ms=1).run["id"]
        service = await self.service()
        snapshot = await self.wait_status(service, run_id, "timed_out")
        self.assertFalse(service.status()["can_submit"])
        self.assertEqual((snapshot["run"]["model_calls"], snapshot["run"]["tool_calls"]), (0, 0))

    async def test_retention_pages_advance_and_stop_with_application(self):
        calls, scanned = [], asyncio.Event()
        async def pruner(runtime, checkpoints, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                scanned.set()
            return {"after": "cursor-one" if len(calls) == 1 else ""}
        service = await self.service(checkpoint_db=self.temp / "checkpoints.db", pruner=pruner, retention_interval=0.01)
        await asyncio.wait_for(scanned.wait(), 3)
        await service.aclose()
        self.assertEqual([call["after"] for call in calls[:2]], ["", "cursor-one"])
        self.assertTrue(all(call["limit"] == 50 for call in calls))
        self.assertTrue(service._retention_task.done())

    async def test_retention_failure_retries_without_disabling_execution(self):
        calls, retried = [], asyncio.Event()
        async def pruner(*args, **kwargs):
            calls.append(kwargs["after"])
            if len(calls) == 1:
                raise RuntimeError("private-pruner-body")
            retried.set()
            return {"after": ""}
        async def runner(context):
            return RunResult(answer="仍可执行")
        with self.assertLogs("angemedia_gateway.services.assistant_run_service", level="ERROR") as logs:
            service = await self.service(runner, checkpoint_db=self.temp / "checkpoints.db", pruner=pruner, retention_interval=0.01)
            await asyncio.wait_for(retried.wait(), 3)
        self.assertNotIn("private-pruner-body", str(logs.output))
        run_id = (await self.submit(service))["run"]["id"]
        await self.wait_status(service, run_id, "succeeded")

    async def test_retention_cleanup_finishes_before_service_close_returns(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def pruner(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                closed.set()
        service = await self.service(checkpoint_db=self.temp / "checkpoints.db", pruner=pruner)
        await asyncio.wait_for(entered.wait(), 3)
        await service.aclose()
        self.assertTrue(closed.is_set())


if __name__ == "__main__":
    unittest.main()

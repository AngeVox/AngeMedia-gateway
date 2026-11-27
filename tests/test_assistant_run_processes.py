"""独立进程运行服务的争抢与强制退出后接管，使用持久库而非进程内状态。"""
from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
import unittest
from uuid import uuid4

from assistant_run_support import RunFixture
import angemedia_gateway.config as C
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.services.assistant_run_service import AssistantRunService


def _service_process(path, run_id, hold, output):
    C.DB_FILE = Path(path)
    async def main():
        repo = AssistantRunRepository(account_only=True)
        calls = 0
        async def runner(context):
            nonlocal calls
            calls += 1
            await context.reserve_call("model")
            output.put(("executing", context.lease.run_id))
            if hold:
                await asyncio.Event().wait()
            return RunResult(answer="另一进程恢复完成")
        service = AssistantRunService(repo, runner=runner, recovery_interval_ms=10, lease_ms=900, heartbeat_ms=100)
        await service.start()
        try:
            async def completed():
                while (await asyncio.to_thread(repo.get, run_id))["status"] != "succeeded":
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(completed(), 12)
        finally:
            await service.aclose()
        output.put(("closed", calls))
    asyncio.run(main())


class RunProcessesTest(RunFixture, unittest.TestCase):
    def context(self):
        return multiprocessing.get_context("spawn")

    def stop(self, processes, output):
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
            self.assertFalse(process.is_alive())
            process.close()
        output.close()
        output.join_thread()

    def test_forced_process_exit_is_recovered_by_a_new_application_service(self):
        run_id = self.repo.admit(self.session, uuid4().hex, {"message": "恢复"}).run["id"]
        context, processes = self.context(), []
        output = context.Queue()
        try:
            first = context.Process(target=_service_process, args=(str(self.repo.db_path), run_id, True, output))
            first.start()
            processes.append(first)
            self.assertEqual(output.get(timeout=10), ("executing", run_id))
            first.terminate()
            first.join(5)
            self.assertNotEqual(first.exitcode, 0)
            second = context.Process(target=_service_process, args=(str(self.repo.db_path), run_id, False, output))
            second.start()
            processes.append(second)
            self.assertEqual(output.get(timeout=10), ("executing", run_id))
            self.assertEqual(output.get(timeout=10), ("closed", 1))
            second.join(5)
            self.assertEqual(second.exitcode, 0)
            run = self.repo.get(run_id)
            self.assertEqual((run["status"], run["model_calls"]), ("succeeded", 2))
            self.assertIn("resumed", [event["event_type"] for event in self.repo.events(run_id)])
        finally:
            self.stop(processes, output)

    def test_two_application_processes_execute_one_durable_run_once(self):
        run_id = self.repo.admit(self.session, uuid4().hex, {"message": "争抢"}).run["id"]
        context, processes = self.context(), []
        output = context.Queue()
        try:
            for _ in range(2):
                process = context.Process(target=_service_process, args=(str(self.repo.db_path), run_id, False, output))
                process.start()
                processes.append(process)
            closed, executions = [], []
            while len(closed) < 2:
                kind, value = output.get(timeout=12)
                (closed if kind == "closed" else executions).append(value)
            for process in processes:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(closed), 1)
            self.assertEqual(executions, [run_id])
            self.assertEqual(self.repo.get(run_id)["model_calls"], 1)
        finally:
            self.stop(processes, output)


if __name__ == "__main__":
    unittest.main()

"""真实TCP连接上的断线继续、游标重连与打开订阅期间注销。"""
from __future__ import annotations

from contextlib import contextmanager
import socket
import threading
import time
import unittest
from uuid import uuid4

from assistant_run_support import RunFixture
from fastapi import FastAPI
import httpx
import uvicorn
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.repositories.admin_auth import delete_admin_session
from angemedia_gateway.routes import assistant_runs
from angemedia_gateway.security import admin_session_context
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan
from angemedia_gateway.services.assistant_run_service import AssistantRunService

BASE = "/v1/admin/assistant"


class RunHttpTest(RunFixture, unittest.TestCase):
    @contextmanager
    def server(self):
        release, calls = threading.Event(), []
        async def runner(context):
            import asyncio
            calls.append(context.lease.run_id)
            await context.append_event("tool_progress", {"stage": "waiting"})
            while not release.is_set():
                await asyncio.sleep(0.005)
            return RunResult(answer="断线后仍然完成")
        app = FastAPI(lifespan=assistant_lifespan)
        app.state.assistant_service_factory = lambda: AssistantRunService(self.repo, runner=runner,
            recovery_interval_ms=10, lease_ms=3000, heartbeat_ms=100)
        app.include_router(assistant_runs.router)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, ws="none", timeout_graceful_shutdown=3))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(server.started, "独立HTTP服务未启动")
            token = self.cookies["alice"]
            with httpx.Client(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", trust_env=False,
                              timeout=5, cookies={"am_admin_session": token},
                              headers={"X-Studio-Session-Context": admin_session_context(token)}) as client:
                yield client, release, calls
        finally:
            release.set()
            server.should_exit = True
            thread.join(10)
            listener.close()
            self.assertFalse(thread.is_alive(), "HTTP测试服务未收尾")

    def submit(self, client):
        response = client.post(f"{BASE}/conversations/{self.session}/runs", json={"request_key": uuid4().hex, "message": "真实TCP断线"})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["run"]["id"]

    @staticmethod
    def first_progress(lines):
        event_id = event_type = None
        for line in lines:
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("event: "):
                event_type = line[7:]
            elif not line and event_type == "tool_progress":
                return event_id
        raise AssertionError("未收到运行中的持久事件")

    def test_tcp_disconnect_does_not_cancel_and_reconnect_only_replays_unseen_events(self):
        with self.server() as (client, release, calls):
            run_id = self.submit(client)
            with client.stream("GET", f"{BASE}/runs/{run_id}/stream") as stream:
                self.assertEqual(stream.status_code, 200)
                acknowledged = self.first_progress(stream.iter_lines())
            self.assertEqual(self.repo.get(run_id)["status"], "running")
            release.set()
            deadline = time.monotonic() + 5
            while self.repo.get(run_id)["status"] != "succeeded" and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.repo.get(run_id)["status"], "succeeded")
            resumed = client.get(f"{BASE}/runs/{run_id}/stream", headers={"Last-Event-ID": acknowledged})
            ids = [int(line.split(":")[-1]) for line in resumed.text.splitlines() if line.startswith("id: ")]
            self.assertTrue(ids)
            self.assertTrue(all(value > int(acknowledged.split(":")[-1]) for value in ids))
            self.assertIn("断线后仍然完成", resumed.text)
            self.assertEqual(calls, [run_id])
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/stream", headers={"Last-Event-ID": f"{run_id}:{ids[-1]}"}).status_code, 204)

    def test_logout_terminates_an_open_tcp_subscription_without_canceling_execution(self):
        with self.server() as (client, release, calls):
            run_id = self.submit(client)
            with client.stream("GET", f"{BASE}/runs/{run_id}/stream") as stream:
                lines = stream.iter_lines()
                self.first_progress(lines)
                delete_admin_session(self.cookies["alice"])
                remaining = "\n".join(lines)
            self.assertIn('"code":"authentication_expired"', remaining)
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/stream").status_code, 401)
            self.assertEqual(self.repo.get(run_id)["status"], "running")
            self.assertEqual(calls, [run_id])
            release.set()


if __name__ == "__main__":
    unittest.main()

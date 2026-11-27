"""运行HTTP合同、持久事件重放与真实认证；旧聊天入口保持独立。"""
from __future__ import annotations

import re
import time
import unittest
from uuid import uuid4

from assistant_run_support import RunFixture
from fastapi import FastAPI
from studio_test_client import TestClient
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.repositories.admin_auth import delete_admin_session
from angemedia_gateway.repositories.gateway_keys import create_gateway_api_key
from angemedia_gateway.routes import assistant_runs
from angemedia_gateway.server import app as production_app
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan
from angemedia_gateway.services.assistant_run_service import AssistantRunService

BASE = "/v1/admin/assistant"


class RunApiTest(RunFixture, unittest.TestCase):
    def client(self, runner=None, **kwargs):
        service = AssistantRunService(self.repo, runner=runner, recovery_interval_ms=10,
            lease_ms=3000, heartbeat_ms=100, **kwargs)
        app = FastAPI(lifespan=assistant_lifespan)
        app.state.assistant_service_factory = lambda: service
        app.include_router(assistant_runs.router)
        client = TestClient(app)
        client.seed_session(self.cookies["alice"])
        return client, service

    def submit(self, client, *, key=None, message="保持蓝色"):
        return client.post(f"{BASE}/conversations/{self.session}/runs", json={"request_key": key or uuid4().hex, "message": message})

    def wait_status(self, client, run_id, status):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get(f"{BASE}/runs/{run_id}")
            self.assertEqual(response.status_code, 200, response.text)
            if response.json()["run"]["status"] == status:
                return response.json()["run"]
            time.sleep(0.005)
        self.fail("运行没有进入预期状态")

    def test_admin_cookie_is_required_for_all_new_routes(self):
        client, _ = self.client()
        with client:
            client.cookies.clear()
            key = create_gateway_api_key(name="run-contract")["key"]
            for headers, expected in (({}, 401), ({"Authorization": f"Bearer {key}"}, 403)):
                for method, path, body in (("GET", "/runtime", None), ("GET", "/runs/missing", None),
                    ("GET", "/runs/missing/events", None), ("GET", "/runs/missing/stream", None),
                    ("POST", "/runs/missing/cancel", None),
                    ("POST", f"/conversations/{self.session}/runs", {"request_key": uuid4().hex, "message": "提问"})):
                    with self.subTest(method=method, path=path, expected=expected):
                        self.assertEqual(client.request(method, BASE + path, json=body, headers=headers).status_code, expected)

    def test_production_lifespan_gates_disabled_and_unconfigured_business_assistant(self):
        from angemedia_gateway.repositories.settings import set_config_many
        with TestClient(production_app) as client:
            client.seed_session(self.cookies["alice"])
            set_config_many({"ANGE_ASSISTANT_ENABLED": "false"})
            self.assertEqual(client.get(BASE + "/runtime").json(), {"state": "assistant_disabled", "can_submit": False})
            response = self.submit(client)
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (503, "run_execution_unavailable"))
            set_config_many({"ANGE_ASSISTANT_ENABLED": "true", "ANGE_LLM_BASE_URL": "", "ANGE_LLM_MODEL": ""})
            self.assertEqual(client.get(BASE + "/runtime").json(), {"state": "assistant_not_configured", "can_submit": False})
            self.assertEqual(self.submit(client).status_code, 503)
        self.assertEqual(self.repo.ready(), [])
        self.assertFalse(hasattr(production_app.state, "assistant_run_service"))

    def test_post_get_retry_and_conflict_share_the_same_durable_run(self):
        calls = []
        async def runner(context):
            calls.append(context.lease.run_id)
            await context.publish_message("user", context.input["message"])
            return RunResult(answer="完成")
        client, _ = self.client(runner)
        with client:
            key = uuid4().hex
            accepted = self.submit(client, key=key)
            self.assertEqual(accepted.status_code, 202, accepted.text)
            self.assertEqual(accepted.headers["cache-control"], "no-store")
            run_id = accepted.json()["run"]["id"]
            completed = self.wait_status(client, run_id, "succeeded")
            replay = self.submit(client, key=key)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json(), {"created": False, "run": completed})
            self.assertEqual(self.submit(client, key=key, message="不同意图").status_code, 409)
            self.assertEqual(calls, [run_id])

    def test_cross_account_run_access_is_indistinguishable_from_missing(self):
        run_id = self.repo.admit(self.session, uuid4().hex, {}).run["id"]
        client, _ = self.client()
        with client:
            client.seed_session(self.cookies["bob"])
            self.assertEqual(self.submit(client).status_code, 404)
            for method, suffix in (("GET", ""), ("GET", "/events"), ("GET", "/stream"), ("POST", "/cancel")):
                forbidden = client.request(method, BASE + "/runs/" + run_id + suffix)
                missing = client.request(method, BASE + "/runs/missing" + suffix)
                self.assertEqual((forbidden.status_code, forbidden.json()), (404, missing.json()))
        self.assertEqual(self.repo.get(run_id)["status"], "queued")

    def test_sse_replays_every_page_and_binds_last_event_id_to_run(self):
        run_id = self.repo.admit(self.session, uuid4().hex, {}).run["id"]
        lease = self.repo.claim(run_id)
        for index in range(110):
            self.repo.append_event(lease, "tool_progress", {"index": index})
        self.repo.finish(lease, answer="全部完成")
        head = self.repo.get(run_id)["event_seq"]
        client, _ = self.client()
        with client:
            stream = client.get(f"{BASE}/runs/{run_id}/stream")
            self.assertEqual(stream.status_code, 200, stream.text)
            ids = [int(value) for value in re.findall(r"^id: " + run_id + r":([0-9]+)$", stream.text, re.M)]
            self.assertEqual(ids, list(range(1, head + 1)))
            self.assertIn("event: done", stream.text)
            self.assertIn("全部完成", stream.text)
            resumed = client.get(f"{BASE}/runs/{run_id}/stream", headers={"Last-Event-ID": f"{run_id}:2"})
            self.assertEqual([int(v) for v in re.findall(r"^id: " + run_id + r":([0-9]+)$", resumed.text, re.M)], list(range(3, head + 1)))
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/stream", headers={"Last-Event-ID": f"{run_id}:{head}"}).status_code, 204)
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/stream", headers={"Last-Event-ID": f"{uuid4().hex}:2"}).status_code, 400)
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/stream?after=1", headers={"Last-Event-ID": f"{run_id}:2"}).status_code, 400)
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/events?after={head+1}").status_code, 409)

    def test_json_event_pages_and_final_answer_match_sse(self):
        run_id = self.repo.admit(self.session, uuid4().hex, {}).run["id"]
        lease = self.repo.claim(run_id)
        self.repo.finish(lease, answer="JSON与SSE一致", output={"owner_token": "private-control"})
        client, _ = self.client()
        with client:
            page = client.get(f"{BASE}/runs/{run_id}/events?limit=1").json()
            self.assertEqual(page["next_after"], 1)
            self.assertTrue(page["has_more"])
            final = client.get(f"{BASE}/runs/{run_id}")
            stream = client.get(f"{BASE}/runs/{run_id}/stream")
            self.assertIn("JSON与SSE一致", stream.text)
            self.assertEqual(final.json()["run"], page["run"])
            for secret in ("private-control", self.cookies["alice"], self.access["alice"].session_hash):
                self.assertNotIn(secret, final.text + stream.text)

    def test_queue_full_returns_429_and_cancel_frees_durable_capacity(self):
        async def runner(context):
            import asyncio
            await asyncio.Event().wait()
        client, _ = self.client(runner, max_pending=1)
        with client:
            key = uuid4().hex
            first = self.submit(client, key=key)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(self.submit(client).status_code, 429)
            self.assertEqual(self.submit(client, key=key).status_code, 200)
            run_id = first.json()["run"]["id"]
            self.assertEqual(client.post(f"{BASE}/runs/{run_id}/cancel").json()["run"]["status"], "canceled")
            self.assertEqual(self.submit(client).status_code, 202)

    def test_disabled_runtime_keeps_existing_replay_read_and_cancel_available(self):
        key = uuid4().hex
        run_id = self.repo.admit(self.session, key, {"message": "保持蓝色"}).run["id"]
        client, _ = self.client()
        with client:
            self.assertEqual(self.submit(client, key=key).status_code, 200)
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}").status_code, 200)
            self.assertEqual(client.post(f"{BASE}/runs/{run_id}/cancel").json()["run"]["status"], "canceled")

    def test_cookie_revocation_blocks_reconnect(self):
        run_id = self.repo.admit(self.session, uuid4().hex, {}).run["id"]
        client, _ = self.client()
        with client:
            delete_admin_session(self.cookies["alice"])
            self.assertEqual(client.get(f"{BASE}/runs/{run_id}/stream").status_code, 401)

    def test_lifespan_shutdown_pauses_running_work_and_removes_service(self):
        async def runner(context):
            import asyncio
            await asyncio.Event().wait()
        client, service = self.client(runner)
        with client:
            run_id = self.submit(client).json()["run"]["id"]
            self.wait_status(client, run_id, "running")
        self.assertEqual(self.repo.get(run_id)["status"], "paused")
        self.assertEqual(service.supervisor.active_count, 0)
        self.assertFalse(hasattr(client.app.state, "assistant_run_service"))


if __name__ == "__main__":
    unittest.main()

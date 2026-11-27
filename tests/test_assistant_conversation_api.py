"""真实认证与HTTP入口上的账户隔离、旧入口拒绝和事件循环响应。"""
from __future__ import annotations

import asyncio
from contextlib import closing
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

import httpx
from studio_test_client import TestClient
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.admin_auth import create_admin_session
from angemedia_gateway.repositories.gateway_keys import create_gateway_api_key
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session
from angemedia_gateway.security import admin_session_context, hash_password
from angemedia_gateway.server import app

BASE = "/v1/admin/assistant/conversations"


class ConversationApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password("fixture-password")

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="assistant-conversation-api-"))
        self.original_db, self.original_gateway_key = C.DB_FILE, C.GATEWAY_API_KEY
        C.DB_FILE, C.GATEWAY_API_KEY = self.temp / "state.db", ""
        init_db()
        with closing(db_connect()) as conn:
            for name in ("alice", "bob"):
                conn.execute("INSERT INTO admin_users VALUES(?,?,?,?)", (name, self.password_hash, "old", "old"))
        self.client, self.other = TestClient(app), TestClient(app)
        self.token, _ = create_admin_session("alice")
        self.client.seed_session(self.token)
        token, _ = create_admin_session("bob")
        self.other.seed_session(token)

    def tearDown(self):
        self.client.close()
        self.other.close()
        C.DB_FILE, C.GATEWAY_API_KEY = self.original_db, self.original_gateway_key
        assert self.temp.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) and self.temp.name.startswith("assistant-conversation-api-")
        shutil.rmtree(self.temp)

    def create(self):
        response = self.client.post(BASE, json={"title": "蓝色山谷"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(set(response.json()["session"]), {"id", "title", "status", "created_at", "updated_at"})
        return response.json()["session"]["id"]

    def test_all_operations_require_admin_cookie_and_reject_gateway_key(self):
        with TestClient(app) as anonymous:
            key = create_gateway_api_key(name="conversation-api")["key"]
            for headers, expected in (({}, 401), ({"Authorization": f"Bearer {key}"}, 403)):
                for method, path, kwargs in (("GET", BASE, {}), ("POST", BASE, {"json": {"title": "秘密"}}),
                                             ("GET", BASE + "/missing", {}), ("DELETE", BASE + "/missing", {})):
                    with self.subTest(method=method, expected=expected):
                        self.assertEqual(anonymous.request(method, path, headers=headers, **kwargs).status_code, expected)

    def test_expired_cookie_is_rejected(self):
        token, _ = create_admin_session("alice", ttl_seconds=-1)
        self.client.seed_session(token)
        self.assertEqual(self.client.get(BASE).status_code, 401)

    def test_cross_account_access_matches_unknown_resource_and_cannot_archive(self):
        session = self.create()
        self.assertEqual(self.client.get(BASE).json()["total"], 1)
        self.assertEqual(self.other.get(BASE).json()["total"], 0)
        for method in ("GET", "DELETE"):
            forbidden = self.other.request(method, BASE + "/" + session)
            missing = self.other.request(method, BASE + "/missing")
            self.assertEqual((forbidden.status_code, forbidden.json()), (404, missing.json()))
        self.assertEqual(self.client.get(BASE + "/" + session).status_code, 200)

    def test_caller_cannot_set_owner_or_control_fields(self):
        for payload in ({"title": "秘密", "username": "bob"}, {"title": "秘密", "access_scope": "shared"},
                        {"title": "秘密", "owner_token": "fixture"}, {"title": ""}, {"title": " "}, {"title": 123}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(BASE, json=payload).status_code, 422)
        self.assertEqual(self.client.get(BASE).json()["total"], 0)
        session = self.create()
        self.assertEqual(self.other.get(BASE + "/" + session, params={"username": "alice"}).status_code, 404)

    def test_legacy_routes_cannot_continue_read_list_or_delete_new_session(self):
        session = self.create()
        for client in (self.client, self.other):
            self.assertEqual(client.get("/v1/admin/assistant/sessions").json()["total"], 0)
            self.assertEqual(client.get("/v1/admin/assistant/sessions/" + session).status_code, 404)
            self.assertEqual(client.delete("/v1/admin/assistant/sessions/" + session).status_code, 404)
            response = client.post("/v1/assistant/chat", json={"session_id": session, "message": "私人跟进"})
            self.assertEqual(response.status_code, 400, response.text)
            stream = client.post("/v1/assistant/chat/stream", json={"session_id": session, "message": "私人跟进"})
            self.assertEqual(stream.status_code, 400, stream.text)
            self.assertNotIn("event: done", stream.text)
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM assistant_sessions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM assistant_messages").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM assistant_runs").fetchone()[0], 0)

    def test_legacy_shared_session_is_not_implicitly_claimed_or_hidden(self):
        create_assistant_session("legacy", "历史共享")
        self.assertEqual(self.client.get(BASE).json()["total"], 0)
        self.assertEqual(self.other.get(BASE + "/legacy").status_code, 404)
        self.assertEqual(self.client.get("/v1/admin/assistant/sessions/legacy").status_code, 200)
        self.assertEqual(self.other.get("/v1/admin/assistant/sessions/legacy").status_code, 200)

    def test_archive_is_idempotent_but_history_becomes_invisible(self):
        session = self.create()
        for _ in range(2):
            self.assertEqual(self.client.delete(BASE + "/" + session).status_code, 200)
        self.assertEqual(self.client.get(BASE + "/" + session).status_code, 404)
        self.assertEqual(self.client.get(BASE).json()["items"], [])
        self.assertEqual(self.other.delete(BASE + "/" + session).status_code, 404)
        self.assertEqual(self.client.post("/v1/assistant/chat", json={"session_id": session, "message": "不可降回共享"}).status_code, 400)

    def test_real_account_rename_revokes_cookie_and_preserves_conversation(self):
        session = self.create()
        response = self.client.patch("/v1/admin/account", json={"current_password": "fixture-password", "new_username": "alice-renamed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(BASE).status_code, 401)
        login = self.client.post("/v1/admin/login", json={"username": "alice-renamed", "password": "fixture-password"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(self.client.get(BASE + "/" + session).status_code, 200)

    def test_pagination_and_projection_do_not_include_identity_or_internal_fields(self):
        session = self.create()
        self.client.post(BASE, json={"title": "第二个"})
        page = self.client.get(BASE, params={"limit": 1, "offset": 1})
        self.assertEqual(page.status_code, 200)
        self.assertEqual((page.json()["total"], len(page.json()["items"])), (2, 1))
        for forbidden in ("username", "access_scope", "owner_token", "password_hash"):
            self.assertNotIn(forbidden, page.text)
        self.assertEqual(self.client.get(BASE + "/" + session, params={"message_limit": 101}).status_code, 422)
        self.assertEqual(self.client.get(BASE, params={"limit": 0}).status_code, 422)

    def test_authentication_does_not_block_the_event_loop(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_auth(_):
            entered.set()
            release.wait(2)
            return {"username": "alice"}
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                         cookies={"am_admin_session": self.token},
                                         headers={"X-Studio-Session-Context": admin_session_context(self.token)}) as client:
                task = asyncio.create_task(client.get(BASE))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    # 仍在等待认证的线程；事件循环必须已经能继续运行。
                    self.assertFalse(release.is_set())
                    self.assertFalse(task.done())
                finally:
                    release.set()
                    await task
        with patch("angemedia_gateway.runtime.get_admin_session", blocked_auth):
            asyncio.run(exercise())

    def test_database_write_wait_does_not_block_the_event_loop(self):
        entered = threading.Event()
        from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository
        original_create = AssistantConversationRepository.create
        def create(repo, **kwargs):
            entered.set()
            return original_create(repo, **kwargs)
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                         cookies={"am_admin_session": self.token},
                                         headers={"X-Studio-Session-Context": admin_session_context(self.token)}) as client:
                with db_transaction(immediate=True):
                    task = asyncio.create_task(client.post(BASE, json={"title": "等待写锁"}))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    self.assertFalse(task.done())
                self.assertEqual((await task).status_code, 201)
        with patch.object(AssistantConversationRepository, "create", create):
            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()

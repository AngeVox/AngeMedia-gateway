"""旧聊天适配与个人会话交互合同；使用真实数据库、认证和后台服务。"""
from __future__ import annotations

from contextlib import closing
from threading import Event
from uuid import uuid4
import asyncio
import json
import unittest

from assistant_run_support import RunFixture
from fastapi import FastAPI
from studio_test_client import TestClient
from angemedia_gateway.assistant_execution import RunResult
from angemedia_gateway.assistant_runtime_contract import RunRequestConflict
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository, ConversationNotFound
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session, add_assistant_message
from angemedia_gateway.repositories.settings import set_config_many
from angemedia_gateway.routes import assistant_compat, assistant_conversations, assistant_runs
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan
from angemedia_gateway.services.assistant_run_service import AssistantRunService

BASE = '/v1/admin/assistant'


class DurableChatTest(RunFixture, unittest.TestCase):
    def client(self, runner):
        service = AssistantRunService(self.repo, runner=runner, recovery_interval_ms=10, lease_ms=3000, heartbeat_ms=100)
        app = FastAPI(lifespan=assistant_lifespan)
        app.state.assistant_service_factory = lambda: service
        for router in (assistant_runs.router, assistant_conversations.router, assistant_compat.router):
            app.include_router(router)
        client = TestClient(app)
        client.seed_session(self.cookies['alice'])
        set_config_many({'ANGE_ASSISTANT_ENABLED': 'true', 'ANGE_LLM_BASE_URL': 'http://fixture.invalid/v1', 'ANGE_LLM_MODEL': 'fixture'})
        return client

    def test_old_json_and_sse_share_one_run_and_native_message_ids(self):
        calls = []
        async def runner(context):
            calls.append(context.input['message'])
            await context.publish_message('user', context.input['message'])
            await context.append_event('tool_started', {'name': 'asset_search', 'call_id': 'fixture'})
            await context.append_event('tool_finished', {'name': 'asset_search', 'call_id': 'fixture', 'status': 'success'})
            return RunResult(answer='保持蓝色，改为竖幅。')
        with self.client(runner) as client:
            payload = {'message': '换个比例', 'request_key': uuid4().hex, 'language': 'zh'}
            first = client.post('/v1/assistant/chat', json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            body = first.json()
            self.assertEqual(body['status'], 'succeeded')
            self.assertEqual(body['message']['id'], body['run']['assistant_message_id'])
            self.assertEqual([m['role'] for m in body['messages']], ['user', 'assistant'])
            replay = client.post('/v1/assistant/chat/stream', json=payload)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertIn('tool_started', replay.text)
            self.assertIn('保持蓝色', replay.text)
            self.assertEqual(calls, ['换个比例'])
            self.assertEqual(client.get(BASE + '/conversations').json()['total'], 2)
            self.assertEqual(client.get(BASE + '/sessions').json()['total'], 0)
            self.assertEqual(client.post('/v1/assistant/chat', json={**payload, 'message': '不同意图'}).status_code, 409)
            self.assertEqual(client.post('/v1/assistant/chat/stream', json=payload, headers={'Last-Event-ID': 'bad'}).status_code, 400)

    def test_configured_service_failure_is_not_disguised_as_local_success(self):
        async def runner(context):
            raise RuntimeError('sk-PRIVATE /root/secret Traceback')
        with self.client(runner) as client:
            result = client.post('/v1/assistant/chat', json={'message': '最近失败的任务'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['status'], 'failed')
            self.assertEqual(result.json()['run']['output'], {'error_code': 'engine_error'})
            for secret in ('sk-PRIVATE', '/root/secret', 'Traceback', '本地资料'):
                self.assertNotIn(secret, result.text)

    def test_shared_history_is_read_only_and_copy_is_idempotent_and_private(self):
        source = create_assistant_session('old-shared', '旧对话')
        for index in range(104):
            add_assistant_message(str(index), source['id'], 'user' if index % 2 == 0 else 'assistant', f'第{index}轮')
        async def runner(context): return RunResult(answer='ok')
        with self.client(runner) as client:
            self.assertEqual(client.post('/v1/assistant/chat', json={'session_id': source['id'], 'message': '继续'}).status_code, 409)
            payload = {'title': '旧对话', 'request_key': uuid4().hex, 'source_session_id': source['id']}
            copied = client.post(BASE + '/conversations', json=payload)
            self.assertEqual(copied.status_code, 201, copied.text)
            sid = copied.json()['session']['id']
            self.assertEqual(client.post(BASE + '/conversations', json=payload).json()['session']['id'], sid)
            detail = client.get(BASE + '/conversations/' + sid + '?message_limit=100').json()
            self.assertEqual(detail['message_total'], 100)
            self.assertEqual(detail['messages'][0]['content'], '第4轮')
            self.assertEqual(client.get(BASE + '/sessions/old-shared').json()['session']['id'], source['id'])
            client.seed_session(self.cookies['bob'])
            self.assertEqual(client.get(BASE + '/conversations/' + sid).status_code, 404)
            self.assertEqual(client.post('/v1/assistant/chat', json={'session_id': sid, 'message': '越权'}).status_code, 404)
            self.assertEqual(client.post(BASE + '/conversations', json={'title': '越权', 'source_session_id': sid}).status_code, 404)

    def test_creation_retries_and_history_pagination_keep_equal_timestamp_order(self):
        repo = AssistantConversationRepository()
        key = uuid4().hex
        sid = repo.create(username='alice', title='蓝色', request_key=key)['id']
        self.assertEqual(repo.create(username='alice', title='蓝色', request_key=key)['id'], sid)
        with self.assertRaises(RunRequestConflict): repo.create(username='alice', title='红色', request_key=key)
        with self.assertRaises(ConversationNotFound): repo.create(username='bob', title='蓝色', request_key=key)
        with closing(db_connect()) as conn:
            for index in range(121):
                conn.execute("INSERT INTO assistant_messages(id,session_id,role,content,safe_payload_json,created_at) VALUES(?,?,'user',?,'{}','same')",
                             (f'm-{index}', sid, str(index)))
        messages, before = [], None
        while True:
            detail = repo.detail(sid, username='alice', before=before)
            messages = detail['messages'] + messages
            if not detail['has_older']: break
            before = detail['before']
        self.assertEqual([m['content'] for m in messages], [str(i) for i in range(121)])
        with self.assertRaises(ConversationNotFound): repo.detail(sid, username='alice', before='not-in-this-session')
        repo.archive(sid, username='alice')
        with self.assertRaises(ConversationNotFound): repo.create(username='alice', title='蓝色', request_key=key)

    def test_activity_recovers_without_browser_cache_and_cancel_reaches_background(self):
        entered, canceled = Event(), Event()
        async def runner(context):
            await context.publish_message('user', context.input['message']); entered.set()
            try: await asyncio.Event().wait()
            finally: canceled.set()
        with self.client(runner) as client:
            self.assertIsNone(client.get(f'{BASE}/conversations/{self.session}/activity').json()['run'])
            result = client.post(f'{BASE}/conversations/{self.session}/runs', json={'message': '诊断', 'request_key': uuid4().hex})
            run_id = result.json()['run']['id']
            self.assertTrue(entered.wait(3))
            activity = client.get(f'{BASE}/conversations/{self.session}/activity')
            self.assertEqual(activity.json()['run']['id'], run_id)
            self.assertEqual(client.post(f'{BASE}/runs/{run_id}/cancel').json()['run']['status'], 'canceled')
            self.assertTrue(canceled.wait(3))
            client.seed_session(self.cookies['bob'])
            self.assertEqual(client.get(f'{BASE}/conversations/{self.session}/activity').status_code, 404)

    def test_local_knowledge_has_no_conversation_or_model_side_effect(self):
        async def forbidden(context): self.fail('本地资料不得调用执行器')
        with self.client(forbidden) as client:
            result = client.get(BASE + '/knowledge?query=AngeMedia Jobs')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['mode'], 'local_knowledge')
            self.assertTrue(result.json()['items'])
            self.assertEqual(self.repo.ready(), [])

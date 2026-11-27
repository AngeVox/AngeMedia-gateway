"""同一生产SDK内核驱动旧JSON/SSE、无ID任务检索及自然创作跟进。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from assistant_run_support import RunFixture
from engine_lab import Fixture
from fastapi import FastAPI
import httpx
from angemedia_gateway.repositories.jobs import create_job, update_job_status
from angemedia_gateway.repositories.assets import save_asset
from angemedia_gateway.repositories.settings import set_config_many
from angemedia_gateway.routes import assistant_compat, assistant_conversations, assistant_runs
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan


class ChatEntrypointsTest(RunFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = Fixture()
        self.job = create_job(kind='image', stage='image_generate', prompt='private creative preference', model='fixture')
        update_job_status(self.job['id'], status='failed', error_code='provider_timeout')
        self.asset = uuid4().hex
        save_asset(id=self.asset, filename='random.png', storage_area='output', relative_path='random.png',
                   url_path='/generated/random.png', media_type='image', source='generated', job_id=self.job['id'])
        set_config_many({'ANGE_ASSISTANT_ENABLED': 'true', 'ANGE_LLM_MODEL': 'protocol-fixture',
                        'ANGE_LLM_BASE_URL': f'http://127.0.0.1:{self.fixture.server.server_port}/v1/chat-entry'})
        self.mode = 'jobs'; self.observed = []; self.fixture.reply = self.reply
        self.app = FastAPI(lifespan=assistant_lifespan)
        for router in (assistant_compat.router, assistant_conversations.router, assistant_runs.router): self.app.include_router(router)
        self.lifecycle = assistant_lifespan(self.app); await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://testserver',
                                       **self.studio_credentials("alice"))

    async def asyncTearDown(self):
        await self.client.aclose(); await self.lifecycle.__aexit__(None, None, None)
        await asyncio.to_thread(self.fixture.close)

    def reply(self, scenario, messages):
        self.observed.append(messages)
        if self.mode == 'creative': return {'role': 'assistant', 'content': '保留蓝色山间云雾，改成竖幅。'}, 'stop'
        tools = [message for message in messages if message['role'] == 'tool']
        def call(name, args):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': name, 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls'
        if not tools: return call('job_search', {'status': 'failed', 'limit': 1})
        last = json.loads(tools[-1]['content'])
        if len(tools) == 1: return call('job_safe_summary', {'job_id': last['data']['items'][0]['id']})
        if len(tools) == 2: return call('asset_search', {'job_id': last['data']['job_id']})
        return {'role': 'assistant', 'content': '关联作品：' + last['data']['items'][0]['id']}, 'stop'

    async def test_no_id_query_reaches_real_search_details_assets_and_terminal_replay_is_free(self):
        payload = {'message': '帮我找最近失败的任务，然后查关联作品', 'request_key': uuid4().hex, 'language': 'zh'}
        result = await self.client.post('/v1/assistant/chat', json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json(); self.assertEqual(body['status'], 'succeeded', body)
        self.assertIn(self.asset, body['answer'])
        self.assertEqual((body['run']['model_calls'], body['run']['tool_calls']), (4, 3))
        self.assertEqual(json.loads(self.observed[0][-1]['content']), {'question': payload['message'], 'language': 'zh'})
        count = len(self.fixture.requests)
        replay = await self.client.post('/v1/assistant/chat/stream', json=payload)
        self.assertIn(self.asset, replay.text); self.assertIn('job_search', replay.text)
        self.assertEqual(len(self.fixture.requests), count)

    async def test_natural_creative_followups_keep_current_question_once_and_safe_history(self):
        self.mode = 'creative'
        first = await self.client.post('/v1/assistant/chat', json={'message': '蓝色山间云雾，保留自然光。'})
        self.assertEqual(first.status_code, 200, first.text)
        sid = first.json()['session_id']
        for path, question in [('/v1/assistant/chat', '换个比例'), ('/v1/assistant/chat/stream', '再自然一点')]:
            result = await self.client.post(path, json={'message': question, 'session_id': sid})
            self.assertEqual(result.status_code, 200, result.text)
            latest = self.observed[-1]
            self.assertEqual(latest[-1]['content'], question)
            self.assertIn('蓝色山间云雾', repr(latest[:-1]))
            self.assertNotIn(question, repr(latest[:-1]))
            self.assertNotIn('refused', result.text)

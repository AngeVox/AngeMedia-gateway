"""生产原生工具闭环：模型只能提案，人类HTTP确认后进入真实worker与资产流水线。"""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from assistant_action_worker_support import ActionWorkerFixture
from engine_lab import Fixture
from fastapi import FastAPI
import httpx
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.settings import set_config_many, upsert_custom_provider
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from unittest.mock import patch
from angemedia_gateway import config as C
from angemedia_gateway.routes import assistant_runs, assistant_actions
from angemedia_gateway.services.assistant_lifecycle import assistant_lifespan
from angemedia_gateway.services.assistant_action_tools import action_tools

BASE = '/v1/admin/assistant'


class ActionAssistantTest(ActionWorkerFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = Fixture(); self.mode = 'image'; self.fixture.reply = self.reply
        set_config_many({'ANGE_LLM_MODEL': 'protocol-fixture', 'ANGE_LLM_API_KEY': 'action-model-credential',
                         'ANGE_LLM_BASE_URL': f'http://127.0.0.1:{self.fixture.server.server_port}/v1/actions'})
        self.app = FastAPI(lifespan=assistant_lifespan)
        self.app.include_router(assistant_runs.router); self.app.include_router(assistant_actions.router)
        self.lifecycle = assistant_lifespan(self.app); await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://testserver',
                                        **self.studio_credentials("alice"))

    async def asyncTearDown(self):
        await self.client.aclose(); await self.lifecycle.__aexit__(None, None, None)
        await asyncio.to_thread(self.fixture.close)

    def reply(self, scenario, messages):
        tools = [message for message in messages if message['role'] == 'tool']
        def call(name, args, call_id):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': call_id, 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls'
        if not tools:
            if self.mode == 'anonymous': return call('propose_image', {'request': {'prompt': '蓝色山谷', 'model': 'custom:anonymous-native'}}, 'native-anonymous')
            if self.mode == 'default': return call('propose_image', {'request': {'prompt': '蓝色山谷'}}, 'native-default')
            if self.mode == 'revise': return call('media_action_list', {}, 'list-current-proposals')
            if self.mode == 'forged': return call('confirm_action', {'confirmed': True}, 'forged-confirmation')
            if self.mode == 'video': return call('propose_video', {'request': {'prompt': '山谷推进', 'model': 'agnes-video-v2.5', 'seconds': '6'}}, 'native-video')
            return call('propose_image', {'request': {'prompt': '蓝色山谷', 'model': 'kolors'}}, 'native-image')
        if self.mode == 'revise' and len(tools) == 1:
            result = json.loads(tools[-1]['content'])
            proposal = result['data']['items'][0]
            return call('revise_media_proposal', {'action_id': proposal['id'], 'version': proposal['version'],
                'changes': {'size': '1024x2048'}}, 'native-revise')
        return {'role': 'assistant', 'content': '提案已准备，请核对卡片。尚未生成媒体。'}, 'stop'

    async def submit(self, key=None):
        response = await self.client.post(f'{BASE}/conversations/{self.session}/runs', json={
            'request_key': key or uuid4().hex, 'message': '帮我生成蓝色山谷，之后可以修改构图'})
        self.assertIn(response.status_code, (200, 202), response.text)
        run_id = response.json()['run']['id']
        async def wait():
            while True:
                run = (await self.client.get(f'{BASE}/runs/{run_id}')).json()['run']
                if run['status'] not in {'queued', 'running', 'paused'}: return run
                await asyncio.sleep(0.01)
        return await asyncio.wait_for(wait(), 15)

    async def test_native_proposal_revision_human_confirmation_and_worker_asset(self):
        key = uuid4().hex; run = await self.submit(key)
        self.assertEqual(run['status'], 'succeeded', run); self.assertEqual(self.counts(), (0, 0, 0))
        system = self.fixture.requests[0]['body']['messages'][0]['content']
        self.assertIn('not an execution log', system)
        self.assertIn('Distinguish your answer finishing from the media task finishing', system)
        original_calls = len(self.fixture.requests)
        await self.submit(key); self.assertEqual(len(self.fixture.requests), original_calls)
        self.mode = 'revise'; revised = await self.submit()
        self.assertEqual(revised['status'], 'succeeded', revised)
        page = (await self.client.get(f'{BASE}/conversations/{self.session}/actions')).json()
        self.assertEqual(page['total'], 1); action = page['items'][0]
        self.assertEqual(action['version'], 2); self.assertEqual(action['review']['request']['size'], '1024x2048')
        self.assertEqual(self.counts(), (0, 0, 0))
        response = await self.client.post(f'{BASE}/actions/{action["id"]}/confirm', headers={'X-AngeMedia-Action-CSRF': page['csrf_token']},
            json={'version': 2, 'binding_digest': action['binding_digest'], 'acknowledgements': action['review']['required_acknowledgements']})
        self.assertEqual(response.status_code, 202, response.text); job_id = response.json()['action']['job']['id']
        runtime = self.image_runtime()
        completed = await asyncio.to_thread(runtime.handle, self.message(job_id))
        self.assertEqual(completed['status'], 'succeeded')
        self.assertEqual(len(list_assets(job_id=job_id)), 1); self.assertEqual(self.counts(), (1, 1, 1))
        schemas = json.dumps(self.fixture.requests[0]['body']['tools'])
        for field in ('"runtime"', '"username"', '"csrf_token"', '"confirm_action"'): self.assertNotIn(field, schemas)
        with closing(db_connect()) as conn:
            self.assertNotIn(page['csrf_token'], repr(conn.execute('SELECT payload_json FROM assistant_run_events').fetchall()))

    async def test_modern_video_native_tool_does_not_insert_legacy_defaults(self):
        self.mode = 'video'; run = await self.submit(); self.assertEqual(run['status'], 'succeeded', run)
        page = (await self.client.get(f'{BASE}/conversations/{self.session}/actions')).json()
        self.assertEqual(page['total'], 1, page); self.assertEqual(page['items'][0]['review']['request']['seconds'], '6')
        self.assertNotIn('frame_rate', page['items'][0]['review']['request'])
        self.assertEqual(self.counts(), (0, 0, 0))
        action = page['items'][0]
        response = await self.client.post(f'{BASE}/actions/{action["id"]}/confirm',
            headers={'X-AngeMedia-Action-CSRF': page['csrf_token']}, json={'version': action['version'],
            'binding_digest': action['binding_digest'], 'acknowledgements': action['review']['required_acknowledgements']})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()['action']['job']['id']; runtime = self.video_runtime()
        for expected in ('scheduled', 'scheduled', 'succeeded'):
            result = await asyncio.to_thread(runtime.handle, self.message(job_id))
            self.assertEqual(result['status'], expected, result)
        self.assertEqual(self.poll_models, ['agnes-video-v2.5', 'agnes-video-v2.5'])
        self.assertEqual(len(list_assets(job_id=job_id)), 1)

    def test_schema_has_four_actions_but_no_model_confirmation_tool(self):
        tools = action_tools()
        self.assertEqual(len(tools), 4)
        for tool in tools: self.assertNotIn('runtime', tool.tool_call_schema.model_fields)
        self.assertTrue(next(tool for tool in tools if tool.name == 'media_action_list').metadata['read_only'])
        self.assertFalse(next(tool for tool in tools if tool.name == 'propose_image').metadata['read_only'])

    async def test_native_anonymous_proposal_keeps_human_csrf_cost_and_worker_boundary(self):
        upsert_custom_provider({'id':'anonymous-native','base_url':'https://anonymous.invalid/v1','default_model':'native-image','enabled':True})
        self.mode='anonymous';key=uuid4().hex
        run=await self.submit(key);self.assertEqual(run['status'],'succeeded',run)
        original_calls=len(self.fixture.requests)
        await self.submit(key);self.assertEqual(len(self.fixture.requests),original_calls)
        page=(await self.client.get(f'{BASE}/conversations/{self.session}/actions')).json();action=page['items'][0]
        self.assertTrue(action['review']['allowed']);self.assertEqual(self.counts(),(0,0,0))
        body={'version':action['version'],'binding_digest':action['binding_digest'],
              'acknowledgements':action['review']['required_acknowledgements']}
        denied=await self.client.post(f'{BASE}/actions/{action["id"]}/confirm',json=body)
        self.assertNotIn(denied.status_code,(200,202));self.assertEqual(self.counts(),(0,0,0))
        headers={'X-AngeMedia-Action-CSRF':page['csrf_token']}
        denied=await self.client.post(f'{BASE}/actions/{action["id"]}/confirm',headers=headers,json={**body,'acknowledgements':[]})
        self.assertNotIn(denied.status_code,(200,202));self.assertEqual(self.counts(),(0,0,0))
        accepted=await self.client.post(f'{BASE}/actions/{action["id"]}/confirm',headers=headers,json=body)
        self.assertEqual(accepted.status_code,202,accepted.text);job_id=accepted.json()['action']['job']['id']
        async def anonymous(request,provider):
            self.assertFalse(provider.get('api_key'));self.assertEqual(request.provider_model,'native-image')
            return self.image_result()
        runtime=self.image_runtime(custom_generate=anonymous);message=self.message(job_id)
        self.assertEqual((await asyncio.to_thread(runtime.handle,message))['status'],'succeeded')
        self.assertEqual((await asyncio.to_thread(runtime.handle,message))['status'],'duplicate')
        self.assertEqual(len(list_assets(job_id=job_id)),1)

    async def test_native_default_proposal_omits_missing_key_fallback_before_confirmation(self):
        update_provider_runtime_config('modelscope',enabled=True,api_key='fixture-native-modelscope')
        update_provider_runtime_config('siliconflow',enabled=True,api_key='')
        self.mode='default'
        with patch.object(C,'SILICONFLOW_API_KEY',''):
            run=await self.submit();self.assertEqual(run['status'],'succeeded',run)
        page=(await self.client.get(f'{BASE}/conversations/{self.session}/actions')).json();action=page['items'][0]
        self.assertTrue(action['review']['allowed']);self.assertTrue(all(r['provider']=='modelscope' for r in action['review']['routes']))
        self.assertEqual(self.counts(),(0,0,0))


if __name__ == '__main__': unittest.main()

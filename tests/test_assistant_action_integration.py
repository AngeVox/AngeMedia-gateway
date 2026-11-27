"""原生提案边界与人类HTTP确认使用真实租约、登录、SQLite和原入队事务。"""
from __future__ import annotations

from contextlib import closing
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from studio_test_client import TestClient
from assistant_action_support import ActionFixture
from angemedia_gateway.assistant_action_access import ProposalRunAccess
from angemedia_gateway.assistant_execution import RunExecution
from angemedia_gateway.assistant_runtime_contract import RunLeaseLost
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository
from angemedia_gateway.repositories.settings import set_config_many
from angemedia_gateway.routes import assistant_actions
from angemedia_gateway.services.assistant_action_tool_executor import execute_action_tool
from angemedia_gateway.services.assistant_action_service import AssistantActionService

BASE = '/v1/admin/assistant'
HEADER = 'X-AngeMedia-Action-CSRF'


class ProposalToolsTest(ActionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        set_config_many({'ANGE_LLM_MODEL': 'protocol-fixture', 'ANGE_LLM_BASE_URL': 'http://fixture.invalid/v1'})
        self.service = AssistantActionService(db_path=self.repo.db_path)

    def context(self):
        run = self.repo.admit(self.session, uuid4().hex, {'message': '生成山谷图片'}, access=self.alice).run
        lease = self.repo.claim(run['id'])
        return RunExecution(lease, {'message': '生成山谷图片'}, run['deadline_ms'], self.repo)

    async def create(self, context, call='native-image-call', prompt='蓝色山谷'):
        return await execute_action_tool(context, 'propose_image', {'request': {'prompt': prompt, 'model': 'kolors'}}, call)

    async def test_native_identity_replay_saves_one_draft_and_never_enqueues(self):
        context = self.context()
        first = await self.create(context); replay = await self.create(context)
        self.assertEqual(first['status'], 'done', first)
        self.assertEqual(first['data']['action']['id'], replay['data']['action']['id'])
        self.assertFalse(replay['data']['created']); self.assertEqual(self.counts(), (0, 0, 0))
        text = json.dumps(first)
        for private in ('binding_digest', 'csrf_token', 'session_hash', context.lease.owner_token): self.assertNotIn(private, text)

    async def test_revision_patch_preserves_full_prompt_beyond_model_excerpt(self):
        context = self.context(); prompt = '自然光蓝色山谷' * 350
        first = await self.create(context, prompt=prompt); action = first['data']['action']
        self.assertTrue(action['prompt_is_excerpt'])
        result = await execute_action_tool(context, 'revise_media_proposal', {'action_id': action['id'], 'version': 1,
            'changes': {'size': '1024x2048'}}, 'native-revision')
        self.assertEqual(result['status'], 'done', result)
        view = self.service.inspect(action['id'], access=self.alice)
        self.assertEqual(view['version'], 2); self.assertEqual(view['review']['request']['prompt'], prompt)
        self.assertEqual(view['review']['request']['size'], '1024x2048')

    async def test_video_25_tool_preserves_explicit_protocol_fields(self):
        result = await execute_action_tool(self.context(), 'propose_video', {'request': {'prompt': '山谷推进',
            'model': 'agnes-video-v2.5', 'seconds': '6'}}, 'native-video')
        self.assertEqual(result['status'], 'done', result); self.assertEqual(self.counts(), (0, 0, 0))

    async def test_model_cannot_confirm_even_with_real_revision_digest(self):
        context = self.context(); result = await self.create(context)
        view = self.service.inspect(result['data']['action']['id'], access=self.alice)
        self.assert_code('human_confirmation_required', self.service.confirm, view['id'], self.confirmation(view),
                         access=ProposalRunAccess(self.repo, context.lease))
        unknown = await execute_action_tool(context, 'confirm_action', {'confirmed': True}, 'forged-confirm')
        self.assertEqual(unknown['code'], 'unknown_tool'); self.assertEqual(self.counts(), (0, 0, 0))

    async def test_runtime_owner_and_confirmed_fields_cannot_be_forged(self):
        context = self.context()
        for field in ('runtime', 'username', 'confirmed', 'request_key'):
            result = await execute_action_tool(context, 'propose_image', {'request': {'prompt': '山谷'}, field: True}, 'forged-' + field)
            self.assertEqual(result['code'], 'invalid_tool_arguments')
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_cancel_during_preparation_revokes_proposal_write(self):
        from angemedia_gateway.services.assistant_action_preparation import ActionPreparer
        context = self.context(); prepare = ActionPreparer.prepare
        def cancel(preparer, *args):
            candidate = prepare(preparer, *args)
            self.repo.cancel(context.lease.run_id, access=self.alice)
            return candidate
        with patch.object(ActionPreparer, 'prepare', cancel):
            with self.assertRaises(RunLeaseLost): await self.create(context)
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_actions').fetchone()[0], 0)

    async def test_same_account_other_conversation_cannot_be_revised_by_current_run(self):
        other = AssistantConversationRepository().create(username='alice', title='其他对话')['id']
        proposal = self.service.propose(other, uuid4().hex, 'image', {'prompt': '私有其他对话', 'model': 'kolors'}, access=self.alice)['action']
        result = await execute_action_tool(self.context(), 'revise_media_proposal', {'action_id': proposal['id'], 'version': 1,
            'changes': {'prompt': '越界'}}, 'wrong-conversation')
        self.assertEqual(result['code'], 'action_not_found')
        self.assertEqual(self.service.inspect(proposal['id'], access=self.alice)['version'], 1)


class HumanActionHttpTest(ActionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI(); self.app.include_router(assistant_actions.router)
        self.client = TestClient(self.app)
        self.client.seed_session(self.cookies['alice'])

    def tearDown(self):
        self.client.close(); super().tearDown()

    def page(self):
        result = self.client.get(f'{BASE}/conversations/{self.session}/actions')
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def post(self, action, operation='confirm', *, body=None, headers=None):
        payload = body if body is not None else {'version': action['version'], 'binding_digest': action['binding_digest'],
            'acknowledgements': action['review']['required_acknowledgements']}
        return self.client.post(f'{BASE}/actions/{action["id"]}/{operation}', json=payload,
            headers=headers if headers is not None else {HEADER: self.page()['csrf_token']})

    def test_explicit_human_confirmation_creates_one_job_and_receipt_replays(self):
        action = self.propose()
        response = self.post(action); self.assertEqual(response.status_code, 202, response.text)
        replay = self.post(action); self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()['action']['job'], response.json()['action']['job'])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_reads_are_no_store_and_csrf_is_only_at_http_boundary(self):
        action = self.propose()
        result = self.client.get(f'{BASE}/actions/{action["id"]}')
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(len(result.json()['csrf_token']), 64)
        self.assertNotIn('csrf_token', self.service.inspect(action['id'], access=self.alice))
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_non_ascii_csrf_header_returns_fixed_denial(self):
        from starlette.requests import Request
        from fastapi import HTTPException
        from angemedia_gateway.services.assistant_action_csrf import require_action_csrf
        request = Request({'type': 'http', 'scheme': 'http', 'server': ('testserver', 80), 'path': '/',
            'headers': [(b'host', b'testserver'), (HEADER.lower().encode(), b'\xe9' * 64)]})
        with self.assertRaises(HTTPException) as caught: require_action_csrf(request, '0' * 64)
        self.assertEqual(caught.exception.status_code, 403)

    def test_missing_foreign_or_non_ascii_csrf_fails_without_job(self):
        action = self.propose()
        for headers in ({}, {HEADER: '0' * 64}, {HEADER: self.page()['csrf_token'], 'Origin': 'https://foreign.invalid'},
                        {HEADER: self.page()['csrf_token'], 'Sec-Fetch-Site': 'cross-site'}):
            response = self.post(action, headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_another_account_cannot_inspect_confirm_or_reuse_csrf(self):
        action = self.propose(); token = self.page()['csrf_token']
        self.client.seed_session(self.cookies['bob'])
        self.assertEqual(self.client.get(f'{BASE}/actions/{action["id"]}').status_code, 404)
        self.assertEqual(self.post(action, headers={HEADER: token}).status_code, 403)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_old_version_after_patch_is_rejected_and_only_new_version_can_confirm(self):
        action = self.propose()
        revised = self.post(action, 'revisions', body={'version': 1, 'request_key': uuid4().hex, 'changes': {'prompt': '新方案'}})
        self.assertEqual(revised.status_code, 200, revised.text)
        self.assertEqual(self.post(action).status_code, 409)
        self.assertEqual(self.post(revised.json()['action']).status_code, 202)

    def test_invalid_confirmation_never_echoes_raw_body_or_accepts_confirmed_true(self):
        action = self.propose()
        response = self.post(action, body={'version': True, 'binding_digest': 'private-secret-body', 'confirmed': True})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private-secret-body', response.text)
        self.assertEqual(response.json()['detail']['code'], 'invalid_action_request')
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_acknowledgements_are_required_and_cannot_override_admin_policy(self):
        action = self.propose()
        response = self.post(action, body={'version': 1, 'binding_digest': action['binding_digest'], 'acknowledgements': []})
        self.assertEqual(response.status_code, 409)
        set_config_many({'ANGE_ASSISTANT_ALLOW_PAID': 'false'})
        self.assertEqual(self.post(action).status_code, 409); self.assertEqual(self.counts(), (0, 0, 0))

    def test_revoke_only_revokes_draft_and_cannot_cancel_submitted_job(self):
        action = self.propose(); submitted = self.propose()
        self.assertEqual(self.post(action, 'revoke', body={}).json()['action']['status'], 'revoked')
        self.assertEqual(self.post(submitted).status_code, 202)
        self.assertEqual(self.post(submitted, 'revoke', body={}).status_code, 409)
        self.assertEqual(self.counts(), (1, 1, 1))


if __name__ == '__main__': unittest.main()

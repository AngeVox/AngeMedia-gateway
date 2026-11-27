"""确认至生成之间的配置、政策和意图变化；失败不能调用供应商或进入自动回退。"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from assistant_action_worker_support import ActionWorkerFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.media_execution_scope import current_execution_scope
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.errors import BackendUnavailable
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.settings import set_config, upsert_custom_provider, update_custom_provider_details


class ActionExecutionTest(ActionWorkerFixture, unittest.TestCase):
    def test_configured_legacy_credential_embedded_in_prompt_is_not_persisted(self):
        with patch.object(C, 'GATEWAY_API_KEY', 'am-legacy-configured-secret'):
            self.assert_code('action_contains_sensitive_value', self.propose,
                request={'prompt': 'prefix' + C.GATEWAY_API_KEY, 'model': 'kolors'})
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_confirmed_image_executes_once_and_receipt_replay_never_regenerates(self):
        action = self.propose(); job = self.job(action); runtime = self.image_runtime()
        message = self.message(job['id'])
        self.assertEqual(runtime.handle(message)['status'], 'succeeded')
        self.assertEqual(runtime.handle(message)['status'], 'duplicate')
        self.assertFalse(self.confirm(action)['created'])
        self.assertEqual(self.calls, [('image', '蓝色山谷')])
        self.assertEqual(len(list_assets(job_id=job['id'])), 1)
        self.assertIsNone(current_execution_scope())

    def test_endpoint_change_after_confirmation_denies_provider_call(self):
        job = self.job(self.propose())
        update_provider_runtime_config('siliconflow', base_url_override='https://changed.invalid/v1')
        self.assert_rejected(self.image_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_credential_rotation_after_confirmation_denies_provider_call(self):
        job = self.job(self.propose())
        update_provider_runtime_config('siliconflow', api_key='fixture-rotated-credential-123456789')
        self.assert_rejected(self.image_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_credential_rotation_between_proposal_and_confirmation_requires_revision(self):
        action = self.propose()
        update_provider_runtime_config('siliconflow', api_key='fixture-rotated-credential-123456789')
        self.assert_code('action_environment_changed', self.confirm, action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_disable_paid_or_assistant_after_enqueue_denies_new_generation(self):
        for key in ('ANGE_ASSISTANT_ALLOW_PAID', 'ANGE_ASSISTANT_ENABLED'):
            with self.subTest(key=key):
                job = self.job(self.propose())
                set_config(key, 'false')
                self.assert_rejected(self.image_runtime(), job)
                set_config(key, 'true')
        self.assertEqual(self.calls, [])

    def test_catalog_capability_change_after_enqueue_requires_new_confirmation(self):
        job = self.job(self.propose()); catalog = load_provider_catalog()
        model = next(item for item in catalog.models if item.id == 'kolors')
        modified = replace(model, capabilities={**model.capabilities, 'image_edit': not model.capabilities.get('image_edit', False)})
        changed = replace(catalog, models=tuple(modified if item.id == model.id else item for item in catalog.models))
        with patch('angemedia_gateway.services.assistant_action_policy.load_provider_catalog', return_value=changed):
            self.assert_rejected(self.image_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_archiving_or_deleting_session_scrubs_body_without_cancelling_approved_job(self):
        jobs = [self.job(self.propose()), self.job(self.propose())]
        with closing(db_connect()) as conn:
            conn.execute("UPDATE assistant_sessions SET status='archived' WHERE id=?", (self.session,))
        runtime = self.image_runtime()
        self.assertEqual(runtime.handle(self.message(jobs[0]['id']))['status'], 'succeeded')
        with closing(db_connect()) as conn:
            conn.execute('DELETE FROM assistant_sessions WHERE id=?', (self.session,))
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_action_revisions WHERE body_json IS NOT NULL').fetchone()[0], 0)
        self.assertEqual(runtime.handle(self.message(jobs[1]['id']))['status'], 'succeeded')
        self.assertEqual(len(self.calls), 2)

    def test_grant_and_marker_cannot_be_omitted_to_bypass_authorization(self):
        for remove in ('grant', 'marker'):
            with self.subTest(remove=remove):
                job = self.job(self.propose())
                with closing(db_connect()) as conn:
                    if remove == 'grant':
                        conn.execute('DELETE FROM assistant_action_execution_grants WHERE job_id=?', (job['id'],))
                    else:
                        payload = json.loads(job['input_json']); del payload['assistant_action']
                        conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id']))
                self.assert_rejected(self.image_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_mutating_persisted_prompt_or_request_does_not_change_approved_execution(self):
        for field in ('prompt', 'payload'):
            with self.subTest(field=field):
                job = self.job(self.propose())
                with closing(db_connect()) as conn:
                    if field == 'prompt': conn.execute("UPDATE jobs SET prompt='changed' WHERE id=?", (job['id'],))
                    else:
                        payload = json.loads(job['input_json']); payload['request']['prompt'] = 'changed'
                        conn.execute('UPDATE jobs SET input_json=? WHERE id=?', (json.dumps(payload), job['id']))
                self.assert_rejected(self.image_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_execution_grant_has_no_prompt_url_secret_and_is_immutable(self):
        job = self.job(self.propose())
        with closing(db_connect()) as conn:
            grant = dict(conn.execute('SELECT * FROM assistant_action_execution_grants WHERE job_id=?', (job['id'],)).fetchone())
            text = json.dumps(grant, ensure_ascii=False)
            for value in ('蓝色山谷', 'https://', C.SILICONFLOW_API_KEY): self.assertNotIn(value, text)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE assistant_action_execution_grants SET binding_digest='other' WHERE job_id=?", (job['id'],))
        self.assertEqual(self.image_runtime().handle(self.message(job['id']))['status'], 'succeeded')

    def test_grant_insert_failure_rolls_back_receipt_job_outbox_and_key(self):
        action = self.propose()
        with closing(db_connect()) as conn:
            conn.execute("CREATE TRIGGER fail_grant BEFORE INSERT ON assistant_action_execution_grants BEGIN SELECT RAISE(ABORT,'fixture_grant_failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.confirm(action)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.service.inspect(action['id'], access=self.alice)['status'], 'draft')

    def test_custom_default_change_uses_explicit_approved_model(self):
        upsert_custom_provider({'id': 'action-custom', 'base_url': 'https://example.invalid/v1', 'api_key': 'fixture-custom-123456789', 'default_model': 'model-a'})
        job = self.job(self.propose(request={'prompt': '山谷', 'model': 'custom:action-custom'}))
        update_custom_provider_details('action-custom', {'default_model': 'model-b'})
        seen = []
        async def generate(request, provider):
            seen.append(request.provider_model)
            return self.image_result()
        self.assertEqual(self.image_runtime(custom_generate=generate).handle(self.message(job['id']))['status'], 'succeeded')
        self.assertEqual(seen, ['model-a'])

    def test_policy_revocation_between_fallback_attempts_aborts_next_provider(self):
        self._fallback_case(revoke=True)

    def test_approved_fallback_without_policy_change_uses_existing_chain(self):
        self._fallback_case(revoke=False)

    def _fallback_case(self, *, revoke):
        credential = patch.object(C, 'MODELSCOPE_API_KEY', 'fixture-modelscope-credential-123456789')
        credential.start(); self.addCleanup(credential.stop)
        for provider in ('agnes_image', 'openai_image', 'pollinations', 'bytedance', 'agnes_video'):
            update_provider_runtime_config(provider, enabled=False)
        action = self.propose(request={'prompt': '山谷'})
        self.assertEqual(action['review']['routes'][0]['provider'], 'siliconflow')
        self.assertTrue(all(item['provider'] == 'modelscope' for item in action['review']['routes'][1:]))
        job = self.job(action); seen = []
        async def first(request, target):
            seen.append('first')
            if revoke: set_config('ANGE_ASSISTANT_ALLOW_PAID', 'false')
            raise BackendUnavailable('fixture unavailable')
        async def second(request, target):
            seen.append('second'); return 'fallback-modelscope-task'
        async def poll(task_id):
            from angemedia_gateway.providers.image.modelscope import ModelScopeImageTask
            return ModelScopeImageTask(task_id,'SUCCEED','https://example.invalid/output.png')
        runtime = self.image_runtime(providers={'siliconflow': SimpleNamespace(generate=first), 'modelscope': SimpleNamespace(submit=second,poll=poll)})
        if revoke:
            self.assert_rejected(runtime, job); self.assertEqual(seen, ['first'])
        else:
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'succeeded')
            self.assertEqual(seen, ['first', 'second'])
            self.assertEqual(list_assets(job_id=job['id'])[0]['provider'], 'modelscope')


if __name__ == '__main__': unittest.main()

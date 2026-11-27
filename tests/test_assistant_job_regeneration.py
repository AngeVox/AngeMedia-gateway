"""重新生成只复制原始参数到新提案，真实worker仍须收到新确认授权。"""
from contextlib import closing
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from types import SimpleNamespace

from fastapi import FastAPI
from studio_test_client import TestClient
from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.assistant_action_access import ProposalRunAccess
from angemedia_gateway.assistant_action_contract import ActionError
from angemedia_gateway.assistant_run_access import RunAuthenticationExpired
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.settings import set_config
from angemedia_gateway.routes import assistant_actions
from angemedia_gateway.services.job_control import JobControlError, recovery_state

BASE='/v1/admin/assistant'


class RegenerationFixture(ActionWorkerFixture):
    def ready(self,kind='image',request=None):
        action=self.propose(kind=kind,request=request or {'prompt':'山谷','model':'kolors' if kind=='image' else 'agnes-video-v2.5'})
        return action,self.job(action)

    def canceled(self,kind='image',request=None):
        action,job=self.ready(kind,request)
        self.service.cancel_job(action['id'],job['version'],access=self.alice)
        return action,get_job(job['id'])

    def regen(self,action,job,**kwargs):
        return self.service.regenerate(action['id'],job['version'],kwargs.pop('key',uuid4().hex),access=kwargs.pop('access',self.alice),**kwargs)

    def control_error(self,code,function,*args,**kwargs):
        with self.assertRaises(JobControlError) as caught:function(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)


class JobRegenerationTest(RegenerationFixture,unittest.TestCase):
    def test_canceled_image_and_video_create_only_draft_then_new_confirmed_job(self):
        for kind in ('image','video'):
            with self.subTest(kind=kind):
                source,old=self.canceled(kind);before=self.counts();key=uuid4().hex
                result=self.regen(source,old,key=key);draft=result['action']
                self.assertTrue(result['created']);self.assertEqual(draft['status'],'draft');self.assertIsNone(draft['job'])
                self.assertEqual(self.counts(),before);self.assertNotEqual(draft['id'],source['id'])
                self.assertEqual(result['source'],{'action_id':source['id'],'job_id':old['id'],'job_version':old['version']})
                self.assertFalse(self.regen(source,old,key=key)['created'])
                fresh=self.job(draft);self.assertNotEqual(fresh['id'],old['id'])
                self.assertEqual(self.regen(source,old,key=key)['action']['job']['id'],fresh['id'])
                runtime=self.image_runtime() if kind=='image' else self.video_runtime()
                for _ in range(1 if kind=='image' else 3):runtime.handle(self.message(fresh['id']))
                self.assertEqual(get_job(fresh['id'])['status'],'succeeded');self.assertEqual(get_job(old['id']),old)
                self.assertEqual(self.confirm(source)['action']['job']['id'],old['id'])
        self.assertEqual([row[0] for row in self.calls].count('image'),1)
        self.assertEqual([row[0] for row in self.calls].count('submit'),1)

    def test_completed_generation_is_a_new_proposal_and_never_reuses_confirmation(self):
        action,job=self.ready();runtime=self.image_runtime();runtime.handle(self.message(job['id']))
        old=get_job(job['id']);draft=self.regen(action,old)['action']
        self.assertEqual(self.calls,[('image','山谷')]);self.assertEqual(self.counts(),(1,1,1))
        self.assertFalse(self.confirm(action)['created'])
        self.assertEqual(self.service.inspect(draft['id'],access=self.alice)['status'],'draft')
        self.assertEqual(get_job(job['id']),old)

    def test_long_original_prompt_and_effective_parameters_are_copied_without_loss(self):
        prompt='完整长提示词，镜头和角色保持一致。'*400
        source,job=self.canceled(request={'prompt':prompt,'model':'kolors','size':'1024x1024'})
        self.assertGreater(len(prompt),4000);draft=self.regen(source,job)['action']
        with closing(db_connect()) as conn:
            old=json.loads(conn.execute('SELECT body_json FROM assistant_action_revisions WHERE action_id=?',(source['id'],)).fetchone()[0])
            new=json.loads(conn.execute('SELECT body_json FROM assistant_action_revisions WHERE action_id=?',(draft['id'],)).fetchone()[0])
        self.assertEqual(new['request'],old['request']);self.assertEqual(new['request']['prompt'],prompt)

    def test_ambiguous_submission_and_known_uncollected_task_cannot_be_shortcut_by_retryable(self):
        for kind in ('image','video'):
            with self.subTest(kind=kind):
                action,job=self.ready(kind)
                runtime=self.image_runtime(generate=lambda *args:(_ for _ in ()).throw(TimeoutError('timeout'))) if kind=='image' else self.video_runtime(on_submit=lambda *args:(_ for _ in ()).throw(TimeoutError('timeout')))
                runtime.handle(self.message(job['id']));failed=get_job(job['id'])
                self.control_error('submission_unresolved',self.regen,action,failed)
                controls=self.service.job_control(action['id'],access=self.alice)
                self.assertFalse(controls['regenerate']['enabled']);self.assertEqual(controls['recovery']['state'],'submission_unresolved')
        action,job=self.ready('video');runtime=self.video_runtime();runtime.handle(self.message(job['id']))
        with closing(db_connect()) as conn:
            conn.execute("UPDATE jobs SET status='failed',stage='finalize',error_code='video_poll_timeout',retryable=1 WHERE id=?",(job['id'],))
        failed=get_job(job['id']);before=self.counts()
        self.control_error('existing_task_unresolved',self.regen,action,failed)
        self.assertEqual(self.counts(),before);self.assertEqual(len(self.calls),3)

    def test_explicit_provider_rejection_allows_new_review_but_not_old_job_retry(self):
        from angemedia_gateway.providers.errors import ProviderAuthError
        action,job=self.ready('video')
        self.video_runtime(on_submit=lambda *args:(_ for _ in ()).throw(ProviderAuthError('rejected'))).handle(self.message(job['id']))
        failed=get_job(job['id']);self.assertEqual(recovery_state(failed),'submission_rejected')
        draft=self.regen(action,failed)['action'];self.assertTrue(draft['review']['allowed'])
        self.assertEqual(get_job(job['id']),failed);self.assertFalse(self.service.job_control(action['id'],access=self.alice)['retry']['enabled'])

    def test_known_provider_failure_and_other_unresolved_states_are_distinguished(self):
        base={'kind':'video','status':'failed','external_task_id':'private-task','error_code':'video_generation_failed'}
        self.assertEqual(recovery_state(base),'provider_failed')
        for code in ('video_asset_import_failed','video_pipeline_timeout','video_result_missing_url','action_execution_connection_changed'):
            self.assertEqual(recovery_state({**base,'error_code':code}),'existing_task_unresolved')
        self.assertEqual(recovery_state({**base,'status':'running'}),'existing_task_in_progress')
        self.assertEqual(recovery_state({'status':'canceled','error_code':'job_canceled_before_start','attempt_count':1}),'state_unavailable')

    def test_provider_confirmed_failure_can_prepare_new_generation_without_restarting_old_one(self):
        from angemedia_gateway.services.video_execution import VideoPollResult
        action,job=self.ready('video');runtime=self.video_runtime();runtime.handle(self.message(job['id']))
        worker=runtime.registry.get('video_poll').__self__
        with patch.object(worker.executor,'poll',new=AsyncMock(return_value=VideoPollResult('action-video-task','failed',error_message='generation failed'))):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
        old=get_job(job['id']);draft=self.regen(action,old)['action']
        self.assertEqual(draft['status'],'draft');self.assertEqual(self.counts(),(1,2,1));self.assertEqual(len(self.calls),1)
        self.assertEqual(get_job(job['id']),old)

    def test_active_source_and_invalid_versions_are_rejected(self):
        action,job=self.ready()
        self.control_error('task_in_progress',self.regen,action,job)
        for version in (True,-1,'0',9223372036854775808):
            self.control_error('invalid_job_version',self.service.regenerate,action['id'],version,uuid4().hex,access=self.alice)
        self.control_error('job_version_conflict',self.service.regenerate,action['id'],99,uuid4().hex,access=self.alice)

    def test_model_foreign_account_and_expired_login_have_no_regeneration_authority(self):
        action,job=self.canceled()
        self.assert_code('human_confirmation_required',self.regen,action,job,access=ProposalRunAccess(None,None))
        self.assert_code('action_not_found',self.regen,action,job,access=self.bob)
        with closing(db_connect()) as conn:conn.execute('DELETE FROM admin_sessions WHERE session_hash=?',(self.alice.session_hash,))
        with self.assertRaises(RunAuthenticationExpired):self.regen(action,job)

    def test_policy_and_current_connection_are_reviewed_anew(self):
        from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
        action,job=self.canceled();update_provider_runtime_config('siliconflow',api_key='fixture-new-credential-123456789')
        set_config('ANGE_ASSISTANT_ALLOW_PAID','false')
        draft=self.regen(action,job)['action'];self.assertFalse(draft['review']['allowed'])
        self.assertIn('paid_generation_disabled',draft['review']['blocked_reasons'])
        self.assert_code('action_policy_denied',self.confirm,draft)
        self.assertEqual(self.counts(),(1,1,1))

    def test_missing_reference_and_environment_drift_do_not_create_new_draft(self):
        action=self.video_action(reference=True);job=self.job(action)
        self.service.cancel_job(action['id'],job['version'],access=self.alice);job=get_job(job['id'])
        (self.uploads/'frame.png').unlink()
        with self.assertRaises(ActionError):self.regen(action,job)
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM assistant_actions').fetchone()[0],1)
        source,old=self.canceled();prepare=self.service.preparer.prepare
        def change(*args):
            candidate=prepare(*args);set_config('ANGE_ASSISTANT_ALLOW_PAID','false');return candidate
        with patch.object(self.service.preparer,'prepare',side_effect=change):
            self.assert_code('action_environment_changed',self.regen,source,old)
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM assistant_actions').fetchone()[0],2)

    def test_revoked_replay_returns_original_revoked_draft_and_keys_are_scoped_to_source(self):
        first,one=self.canceled();second,two=self.canceled();key=uuid4().hex
        draft=self.regen(first,one,key=key)['action'];self.service.revoke(draft['id'],access=self.alice)
        replay=self.regen(first,one,key=key);self.assertFalse(replay['created']);self.assertEqual(replay['action']['status'],'revoked')
        self.assertNotEqual(self.regen(second,two,key=key)['action']['id'],draft['id'])

    def test_erased_source_body_and_archived_session_disable_regeneration(self):
        action,job=self.canceled()
        with closing(db_connect()) as conn:conn.execute('UPDATE assistant_action_revisions SET body_json=NULL WHERE action_id=?',(action['id'],))
        self.control_error('action_source_unavailable',self.regen,action,job)
        self.assertEqual(self.service.job_control(action['id'],access=self.alice)['regenerate']['reason'],'action_source_unavailable')
        with closing(db_connect()) as conn:conn.execute("UPDATE assistant_sessions SET status='archived' WHERE id=?",(self.session,))
        self.assertFalse(self.service.job_control(action['id'],access=self.alice)['regenerate']['enabled'])

    def test_archive_during_prepare_cannot_reintroduce_erased_source_content(self):
        from angemedia_gateway.repositories.assistant_conversations import ConversationNotFound
        action,job=self.canceled();prepare=self.service.preparer.prepare
        def archive(*args):
            candidate=prepare(*args)
            with closing(db_connect()) as conn:conn.execute("UPDATE assistant_sessions SET status='archived' WHERE id=?",(self.session,))
            return candidate
        with patch.object(self.service.preparer,'prepare',side_effect=archive):
            with self.assertRaises(ConversationNotFound):self.regen(action,job)
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM assistant_actions').fetchone()[0],1)

    def test_control_does_not_expose_error_text_task_credentials_or_payloads(self):
        action,job=self.ready('video');runtime=self.video_runtime();runtime.handle(self.message(job['id']))
        controls=self.service.job_control(action['id'],access=self.alice)
        self.assertEqual(controls['recovery'],{'state':'existing_task_in_progress','automatic_resubmit':False})
        self.assertNotIn('action-video-task',json.dumps(controls));self.assertNotIn('prompt',json.dumps(controls))

    def test_image_fallback_authorization_error_does_not_prove_first_provider_was_never_called(self):
        import angemedia_gateway.config as C
        from angemedia_gateway.providers.errors import BackendUnavailable
        from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
        self.enterContext(patch.object(C,'MODELSCOPE_API_KEY','fixture-modelscope-credential-123456789'))
        for provider in ('agnes_image','openai_image','pollinations','bytedance','agnes_video'):
            update_provider_runtime_config(provider,enabled=False)
        action=self.propose(request={'prompt':'山谷'});job=self.job(action);seen=[]
        async def first(request,target):
            seen.append(target.provider);set_config('ANGE_ASSISTANT_ALLOW_PAID','false')
            raise BackendUnavailable('first submission outcome unknown')
        runtime=self.image_runtime(providers={'siliconflow':SimpleNamespace(generate=first)})
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
        failed=get_job(job['id']);self.assertEqual(seen,['siliconflow']);self.assertEqual(failed['error_category'],'action_authorization')
        self.control_error('submission_unresolved',self.regen,action,failed)
        self.assertFalse(self.service.job_control(action['id'],access=self.alice)['regenerate']['enabled'])


class JobRegenerationHttpTest(RegenerationFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.app=FastAPI();self.app.include_router(assistant_actions.router)
        self.client=TestClient(self.app);self.client.seed_session(self.cookies['alice'])
        self.action,self.old=self.canceled();self.url=f'{BASE}/actions/{self.action["id"]}'
        self.token=self.client.get(self.url+'/job-control').json()['csrf_token'];self.key=uuid4().hex

    def tearDown(self):self.client.close();super().tearDown()

    def post(self,body=None,headers=None):
        return self.client.post(self.url+'/regenerate',json=body if body is not None else {'job_version':self.old['version'],'request_key':self.key},
            headers=headers if headers is not None else {'X-AngeMedia-Action-CSRF':self.token})

    def test_http_returns_new_unconfirmed_draft_with_no_store_and_idempotence(self):
        result=self.post();self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertTrue(result.json()['created']);self.assertEqual(result.json()['action']['status'],'draft')
        self.assertFalse(self.post().json()['created']);self.assertEqual(self.counts(),(1,1,1))
        self.assertEqual(self.client.get(self.url+'/regenerate').status_code,405)

    def test_csrf_origin_account_and_missing_login_are_enforced(self):
        for headers in ({},{'X-AngeMedia-Action-CSRF':'0'*64},{'X-AngeMedia-Action-CSRF':self.token,'Origin':'https://foreign.invalid'}):
            self.assertEqual(self.post(headers=headers).status_code,403)
        self.client.seed_session(self.cookies['bob'])
        self.assertEqual(self.post().status_code,403)
        from angemedia_gateway.services.assistant_action_csrf import action_csrf_token
        other=action_csrf_token(self.service.db_path,self.bob)
        self.assertEqual(self.post(headers={'X-AngeMedia-Action-CSRF':other}).status_code,404)
        self.client.cookies.clear();self.assertEqual(self.post().status_code,401)

    def test_strict_request_rejects_identity_changes_parameters_and_fake_confirmation(self):
        for payload in ({'job_version':True,'request_key':self.key},{'job_version':1,'request_key':'x'*32},
                        {'job_version':1,'request_key':self.key,'changes':{'prompt':'injected'}},
                        {'job_version':1,'request_key':self.key,'confirmed':True},
                        {'job_version':1,'request_key':self.key,'job_id':'foreign-private'}):
            result=self.post(payload);self.assertEqual(result.status_code,422,result.text)
            self.assertNotIn('foreign-private',result.text)
        self.assertEqual(self.post({'job_version':99,'request_key':self.key}).status_code,409)

"""人类开始前撤销的领域/HTTP合同；真实worker、投递、提交身份和账户隔离均保留。"""
from contextlib import closing
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from studio_test_client import TestClient
from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.assistant_action_access import ProposalRunAccess
from angemedia_gateway.assistant_run_access import RunAuthenticationExpired
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.job_attempts import create_job_attempt
from angemedia_gateway.repositories.job_dispatches import claim_pending_dispatches, release_dispatch, mark_dispatch_published
from angemedia_gateway.routes import assistant_actions
from angemedia_gateway.services.job_control import JobControlError

BASE='/v1/admin/assistant'


class JobCancellationTest(ActionWorkerFixture, unittest.TestCase):
    def ready(self, kind='image'):
        action=self.propose(kind=kind,request={'prompt':'山谷','model':'kolors' if kind=='image' else 'agnes-video-v2.5'})
        job_id=self.confirm(action)['action']['job']['id']
        return action,get_job(job_id)

    def error(self,code,function,*args,**kwargs):
        with self.assertRaises(JobControlError) as caught:function(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)

    def test_cancel_image_and_video_prevents_real_worker_execution(self):
        for kind in ('image','video'):
            with self.subTest(kind=kind):
                action,job=self.ready(kind); message=self.message(job['id'])
                controls=self.service.job_control(action['id'],access=self.alice)
                self.assertTrue(controls['cancel']['enabled']);self.assertFalse(controls['retry']['enabled'])
                result=self.service.cancel_job(action['id'],job['version'],access=self.alice)
                self.assertTrue(result['created']);self.assertEqual(result['action']['job']['status'],'canceled')
                runtime=self.image_runtime() if kind=='image' else self.video_runtime()
                self.assertEqual(runtime.handle(message)['status'],'terminal');self.assertEqual(self.calls,[])
                self.assertFalse(self.service.cancel_job(action['id'],job['version'],access=self.alice)['created'])

    def test_cancel_keeps_consumed_submission_and_confirmation_replay(self):
        action,job=self.ready();self.service.cancel_job(action['id'],0,access=self.alice)
        replay=self.confirm(action)
        self.assertFalse(replay['created']);self.assertEqual(replay['action']['job']['id'],job['id'])
        self.assertEqual(replay['action']['job']['status'],'canceled');self.assertEqual(self.counts(),(1,1,1))
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_action_execution_grants').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM job_events WHERE event_type='job_canceled_before_start'").fetchone()[0],1)

    def test_disabling_new_generation_does_not_prevent_human_cancellation(self):
        from angemedia_gateway.repositories.settings import set_config_many
        action,job=self.ready()
        set_config_many({'ANGE_ASSISTANT_ENABLED':'false','ANGE_ASSISTANT_ALLOW_PAID':'false'})
        self.assertTrue(self.service.cancel_job(action['id'],job['version'],access=self.alice)['created'])

    def test_stale_job_version_and_malformed_versions_do_not_cancel(self):
        action,job=self.ready()
        for version,code in ((9,'job_version_conflict'),(-1,'invalid_job_version'),(True,'invalid_job_version')):
            self.error(code,self.service.cancel_job,action['id'],version,access=self.alice)
        self.assertEqual(get_job(job['id'])['status'],'queued')

    def test_running_or_ambiguous_video_is_not_canceled_or_resubmitted(self):
        action,job=self.ready('video');runtime=self.video_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        current=get_job(job['id']);self.error('job_already_started',self.service.cancel_job,action['id'],current['version'],access=self.alice)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'succeeded')
        self.assertEqual(sum(call[0]=='submit' for call in self.calls),1)

    def test_attempt_evidence_blocks_inconsistent_queued_job(self):
        action,job=self.ready()
        create_job_attempt(job_id=job['id'],attempt_number=1,stage=job['stage'],status='failed')
        self.error('job_already_started',self.service.cancel_job,action['id'],0,access=self.alice)

    def test_external_task_or_non_initial_stage_is_not_cancelable(self):
        for fields,reason in (({'external_task_id':'existing-upstream'},'job_already_started'),
                              ({'stage':'video_poll'},'job_stage_not_cancelable'),
                              ({'error_code':'video_submit_ambiguous'},'job_state_not_cancelable'),
                              ({'claim_token':'other-runtime'},'job_state_not_cancelable')):
            action,job=self.ready('video')
            with closing(db_connect()) as conn:
                for key,value in fields.items():conn.execute(f'UPDATE jobs SET {key}=? WHERE id=?',(value,job['id']))
            self.error(reason,self.service.cancel_job,action['id'],0,access=self.alice)

    def test_owner_and_human_access_are_required_even_for_replay(self):
        action,job=self.ready();self.service.cancel_job(action['id'],0,access=self.alice)
        self.assert_code('action_not_found',self.service.cancel_job,action['id'],0,access=self.bob)
        fake=ProposalRunAccess(self.repo,None)
        self.assert_code('human_confirmation_required',self.service.cancel_job,action['id'],0,access=fake)
        with closing(db_connect()) as conn:conn.execute('DELETE FROM admin_sessions WHERE session_hash=?',(self.alice.session_hash,))
        with self.assertRaises(RunAuthenticationExpired):self.service.cancel_job(action['id'],0,access=self.alice)

    def test_unsubmitted_and_cleaned_job_cannot_be_canceled(self):
        action=self.propose();self.error('action_not_submitted',self.service.cancel_job,action['id'],0,access=self.alice)
        job=self.confirm(action)['action']['job']
        with closing(db_connect()) as conn:conn.execute('DELETE FROM jobs WHERE id=?',(job['id'],))
        self.error('job_record_unavailable',self.service.cancel_job,action['id'],0,access=self.alice)

    def test_transaction_rolls_back_job_and_outbox_on_receipt_failure(self):
        action,job=self.ready()
        with patch.object(self.service,'_view',side_effect=RuntimeError('controlled failure')):
            with self.assertRaises(RuntimeError):self.service.cancel_job(action['id'],0,access=self.alice)
        self.assertEqual(get_job(job['id'])['status'],'queued')
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT status FROM job_dispatches WHERE job_id=?',(job['id'],)).fetchone()[0],'pending')
            self.assertEqual(conn.execute("SELECT count(*) FROM job_events WHERE event_type='job_canceled_before_start'").fetchone()[0],0)

    def test_pending_published_and_leased_delivery_cannot_restart_canceled_work(self):
        action,job=self.ready()
        leased=claim_pending_dispatches(claim_token='publisher',claim_expires_at='2099-01-01T00:00:00+00:00')
        self.assertEqual(len(leased),1);self.service.cancel_job(action['id'],0,access=self.alice)
        self.assertEqual(self.image_runtime().handle(self.message(job['id']))['status'],'terminal')
        release_dispatch(leased[0]['id'],claim_token='publisher',error_message='controlled publish failure')
        self.assertEqual(claim_pending_dispatches(claim_token='next',claim_expires_at='2099-01-01T00:00:00+00:00'),[])
        self.assertEqual(self.calls,[])

    def test_published_message_and_recovery_do_not_resubmit_canceled_job(self):
        from angemedia_gateway.repositories.job_dispatches import requeue_orphaned_published_dispatches
        action,job=self.ready();message=self.message(job['id'])
        leased=claim_pending_dispatches(claim_token='publisher',claim_expires_at='2099-01-01T00:00:00+00:00')[0]
        mark_dispatch_published(leased['id'],claim_token='publisher',broker_message_id='controlled-message')
        self.service.cancel_job(action['id'],0,access=self.alice)
        self.assertEqual(self.image_runtime().handle(message)['status'],'terminal')
        self.assertEqual(requeue_orphaned_published_dispatches(job_ids=[job['id']])['requeued_jobs'],0)
        self.assertEqual(self.calls,[])


class JobCancellationHttpTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp();self.app=FastAPI();self.app.include_router(assistant_actions.router)
        self.client=TestClient(self.app);self.client.seed_session(self.cookies['alice'])
        self.action=self.propose();self.confirm(self.action)
        self.url=f'{BASE}/actions/{self.action["id"]}'

    def tearDown(self):self.client.close();super().tearDown()

    def control(self):return self.client.get(self.url+'/job-control')
    def cancel(self,body=None,headers=None):
        return self.client.post(self.url+'/cancel-job',json={'job_version':0} if body is None else body,
            headers={'X-AngeMedia-Action-CSRF':self.control().json()['csrf_token']} if headers is None else headers)

    def test_human_http_cancel_is_idempotent_and_no_store(self):
        control=self.control();self.assertEqual(control.status_code,200);self.assertEqual(control.headers['cache-control'],'no-store')
        self.assertTrue(control.json()['cancel']['enabled'])
        first=self.cancel();self.assertEqual(first.status_code,200,first.text);self.assertTrue(first.json()['created'])
        self.assertFalse(self.cancel().json()['created']);self.assertFalse(self.control().json()['cancel']['enabled'])

    def test_missing_foreign_and_expired_csrf_cannot_cancel(self):
        token=self.control().json()['csrf_token']
        for headers in ({},{'X-AngeMedia-Action-CSRF':'0'*64},{'X-AngeMedia-Action-CSRF':token,'Origin':'https://foreign.invalid'}):
            self.assertEqual(self.cancel(headers=headers).status_code,403)
        self.client.seed_session(self.cookies['bob'])
        self.assertEqual(self.control().status_code,404)
        self.assertEqual(self.cancel(headers={'X-AngeMedia-Action-CSRF':token}).status_code,403)

    def test_payload_cannot_supply_job_identity_owner_or_arbitrary_parameters(self):
        for payload in ({'job_version':True},{'job_version':0,'job_id':'foreign-job'},
                        {'job_version':0,'confirmed':True,'username':'alice'},{'job_version':-1}):
            result=self.cancel(body=payload);self.assertEqual(result.status_code,422,result.text)
            self.assertEqual(result.json()['detail']['code'],'invalid_action_request')
            self.assertNotIn('foreign-job',result.text)

    def test_changed_version_returns_conflict_and_cancel_endpoint_is_not_a_get(self):
        self.assertEqual(self.cancel(body={'job_version':99}).status_code,409)
        self.assertEqual(self.client.get(self.url+'/cancel-job').status_code,405)

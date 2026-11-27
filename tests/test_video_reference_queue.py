"""普通队列与助手通过已有中转处理2.5/Flash本地图片，后续取回只依赖原视频连接。"""
from contextlib import closing, contextmanager
import json
import unittest
from unittest.mock import patch

import httpx

from video_reference_queue_support import ReferenceQueueFixture, FLASH, PAID, IMAGE, CONTENT, TOKEN, RELAY, PUBLIC
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.reference_relay_config import update_reference_relay_config
from angemedia_gateway.repositories.settings import set_config
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin


class VideoReferenceQueueTest(ReferenceQueueFixture, unittest.TestCase):
    def test_paid_and_flash_keyframes_use_real_relay_then_existing_worker_finalizes_once(self):
        for model in (PAID, FLASH):
            with self.subTest(model=model):
                self.configure_relay()
                job = self.modern_job(model=model, prompt=model, images=None, first_frame=IMAGE, last_frame=IMAGE)
                runtime, request, uploads = self.real_adapter_runtime(task_id=model+'-task')
                first = self.message(job['id'])
                for state in ('scheduled', 'scheduled', 'succeeded'):
                    self.assertEqual(runtime.handle(self.message(job['id']))['status'], state)
                self.assertEqual(len(uploads), 2)
                for url, authorization, content in uploads:
                    self.assertEqual((url, authorization), (RELAY, 'Bearer '+TOKEN))
                    self.assertIn(CONTENT, content)
                sent = request.call_args_list[0].kwargs['json']
                self.assertEqual((sent['first_frame'], sent['last_frame']), (PUBLIC, PUBLIC))
                self.assertEqual(request.call_args_list[1].kwargs['params']['model_name'], model)
                self.assertEqual(len(list_assets(job_id=job['id'])), 1)
                self.assertIsNotNone(get_video_job_origin(job['id'])['external_task_digest'])
                self.assertEqual(runtime.handle(first)['status'], 'duplicate')
                self.assertEqual(request.await_count, 3)

    def test_assistant_review_confirmation_and_grant_bind_relay_before_real_upload(self):
        self.configure_relay()
        action = self.propose(kind='video', request={'prompt': '参考图动画', 'model': FLASH, 'images': [IMAGE]})
        self.assertTrue(action['review']['allowed'])
        self.assertEqual(action['review']['references'][0]['kind'], 'gateway_image')
        self.assertEqual(self.counts(), (0, 0, 0))
        job = self.job(action)
        with closing(db_connect()) as conn:
            bindings = json.loads(conn.execute('SELECT connections_json FROM assistant_action_execution_grants WHERE job_id=?',
                                               (job['id'],)).fetchone()[0])
        self.assertEqual({item['provider'] for item in bindings}, {'agnes_video', 'reference_relay'})
        runtime, request, uploads = self.real_adapter_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(len(uploads), 1)
        self.assertEqual(request.call_args.kwargs['json']['images'], [PUBLIC])

    def test_relay_and_reference_removal_after_accept_does_not_cancel_assistant_collection(self):
        self.configure_relay(); job = self.modern_job(assistant=True)
        runtime, request, uploads = self.real_adapter_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        update_reference_relay_config(mode='disabled', upload_url=None, token=None)
        (self.uploads/'relay-frame.png').unlink()
        set_config('ANGE_ASSISTANT_ENABLED', 'false'); set_config('ANGE_ASSISTANT_ALLOW_PAID', 'false')
        for state in ('scheduled', 'succeeded'):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], state)
        self.assertEqual(len(uploads), 1); self.assertEqual(request.await_count, 3)
        self.assertEqual(len(list_assets(job_id=job['id'])), 1)

    def test_generic_followup_also_ignores_removed_relay_and_reference(self):
        self.configure_relay(); job = self.modern_job()
        runtime, request, uploads = self.real_adapter_runtime()
        runtime.handle(self.message(job['id']))
        update_reference_relay_config(mode='disabled'); (self.uploads/'relay-frame.png').unlink()
        for state in ('scheduled', 'succeeded'):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], state)
        self.assertEqual(len(uploads), 1); self.assertEqual(request.await_count, 3)

    def test_assistant_original_video_account_is_still_required_after_relay_removal(self):
        self.configure_relay(); job = self.modern_job(assistant=True)
        runtime, request, uploads = self.real_adapter_runtime()
        runtime.handle(self.message(job['id']))
        update_reference_relay_config(mode='disabled')
        update_provider_runtime_config('agnes_video', api_key='changed-video-account-fixture')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'action_execution_connection_changed')
        self.assertEqual(request.await_count, 1); self.assertEqual(len(uploads), 1)

    def test_capture_freezes_relay_token_endpoint_and_image_for_call_already_started(self):
        self.configure_relay(); job = self.modern_job()
        def rotate(request):
            update_reference_relay_config(upload_url='https://changed.invalid/upload', token='changed-relay-fixture')
            (self.uploads/'relay-frame.png').write_bytes(b'\x89PNG\r\n\x1a\nchanged-frame')
        runtime, request, uploads = self.real_adapter_runtime(on_prepare=rotate)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'scheduled')
        self.assertEqual(uploads[0][:2], (RELAY, 'Bearer '+TOKEN))
        self.assertIn(CONTENT, uploads[0][2]); self.assertNotIn(b'changed-frame', uploads[0][2])

    def test_relay_change_before_final_gate_blocks_submit_without_upload(self):
        from angemedia_gateway.services import video_job_origin as module
        self.configure_relay(); job = self.modern_job(); original = module.media_execution_scope
        @contextmanager
        def rotate(snapshot):
            with original(snapshot):
                update_reference_relay_config(token='changed-before-final-gate')
                yield
        runtime, request, uploads = self.real_adapter_runtime()
        with patch.object(module, 'media_execution_scope', side_effect=rotate):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'video_origin_connection_changed')
        self.assertEqual(uploads, []); request.assert_not_awaited()

    def test_disabled_missing_invalid_or_traversal_reference_never_admits_work(self):
        for configured, reference in ((False, IMAGE), (True, '/uploads/missing.png'),
                                      (True, '/uploads/../private.png'), (True, '/uploads/frame.txt')):
            with self.subTest(configured=configured, reference=reference):
                if configured: self.configure_relay()
                else: update_reference_relay_config(mode='disabled')
                with self.assertRaises(ValueError): self.modern_job(images=[reference])
        self.assertEqual(self.counts(), (0, 0, 0)); self.assertEqual(self.calls, [])

    def test_failure_or_private_publication_never_submits_generation(self):
        for status, payload in ((503, {}), (200, {'url': 'http://127.0.0.1/private.png'})):
            with self.subTest(status=status):
                self.configure_relay(); job = self.modern_job(prompt='failure-'+str(status))
                runtime, request, uploads = self.real_adapter_runtime(relay_response=lambda msg: httpx.Response(status, json=payload))
                self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
                request.assert_not_awaited(); self.assertEqual(len(uploads), 1)
                self.assertIsNone(get_job(job['id'])['external_task_id'])

    def test_paid_policy_and_user_confirmation_are_not_bypassed_by_local_reference(self):
        self.configure_relay(); set_config('ANGE_ASSISTANT_ALLOW_PAID', 'false')
        action = self.propose(kind='video', request={'prompt': 'x', 'model': FLASH, 'images': [IMAGE]})
        self.assertIn('paid_generation_disabled', action['review']['blocked_reasons'])
        self.assert_code('action_policy_denied', self.confirm, action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_assistant_relay_change_before_submit_is_rejected_before_upload(self):
        self.configure_relay(); job = self.modern_job(assistant=True)
        update_reference_relay_config(upload_url='https://changed.invalid/upload')
        runtime, request, uploads = self.real_adapter_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(get_job(job['id'])['error_code'], 'action_execution_conditions_changed')
        self.assertEqual(uploads, []); request.assert_not_awaited()

    def test_reference_urls_and_relay_secrets_are_not_persisted_into_execution_evidence(self):
        self.configure_relay(); job = self.modern_job(assistant=True)
        runtime, request, uploads = self.real_adapter_runtime(); runtime.handle(self.message(job['id']))
        with closing(db_connect()) as conn:
            rendered = repr([tuple(row) for table in ('video_job_origins', 'assistant_action_execution_grants', 'job_events', 'job_attempts')
                             for row in conn.execute('SELECT * FROM '+table)])
        for private in (TOKEN, RELAY, PUBLIC, CONTENT.decode('latin1')): self.assertNotIn(private, rendered)

"""提交前留下真实来源，旧同步/扩展/下载合同与已受理任务的可取回性同时保留。"""
import asyncio
import base64
from contextlib import asynccontextmanager, closing, contextmanager
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from video_direct_support import DirectVideoFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.media_execution_scope import current_execution_scope
from angemedia_gateway.providers.errors import ProviderAuthError, ProviderQueueFull, ProviderTimeout, ProviderTaskFailed
from angemedia_gateway.providers.runtime_config import resolve_provider_runtime_config
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job, cleanup_jobs
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.reference_relay_config import update_reference_relay_config
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin, external_task_digest
from angemedia_gateway.services.video_generation import get_video, create_video
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshError, VideoJobRefreshService
from angemedia_gateway.services.video_upgrade_inventory import inventory_video_upgrade
from angemedia_gateway.services.media_connection_binding import connection_binding
from angemedia_gateway.schemas import VideoRequest
from angemedia_gateway.services.video_execution import VideoProviderDisabled


class DirectVideoExecutionTest(DirectVideoFixture, unittest.TestCase):
    def test_source_and_real_job_exist_before_submit_without_fake_queue_delivery(self):
        provider,calls=self.provider()
        result=self.create_direct(provider)
        job=get_job(result['job_id']);origin=get_video_job_origin(job['id'])
        self.assertEqual(origin['external_task_digest'],external_task_digest(result['task_id']))
        self.assertEqual(job['provider_status'],'queued');self.assertEqual(len(calls),1)
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM job_dispatches').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM job_attempts').fetchone()[0],0)
        self.assertIsNone(current_execution_scope())

    def test_sync_response_and_remote_history_remain_available_without_forced_download(self):
        provider,calls=self.provider()
        with patch.object(C,'AUTO_DOWNLOAD_GENERATED',False), patch('angemedia_gateway.media.download_remote_media',
                new=AsyncMock(side_effect=AssertionError('不应下载'))):
            result=self.create_direct(provider,wait_for_completion=True)
        self.assertNotIn('job_id',result);self.assertEqual(result['status'],'completed')
        self.assertEqual(result['video_url'],'https://cdn.example.test/direct.mp4')
        job=self.direct_rows()[0];self.assertEqual(job['status'],'succeeded')
        self.assertEqual(self.histories()[0]['result_url'],result['video_url'])
        self.assertEqual(self.histories()[0]['id'],result['history_id']);self.assertEqual(list_assets(job_id=job['id']),[])
        self.assertEqual(len(calls),2)

    def test_extra_body_is_transmitted_without_persisting_private_input(self):
        provider,calls=self.provider()
        private={'motion':'pan','custom':'fixture-private-body-token','nested':{'text':'fixture-private-password'}}
        result=self.create_direct(provider,extra_body=private)
        self.assertEqual(calls[0]['json']['extra_body'],private)
        job=get_job(result['job_id']);self.assertIsNone(job['request_hash'])
        rendered=repr((job,self.histories(),get_video_job_origin(job['id'])))
        for secret in ('fixture-private-body-token','fixture-private-password'):
            self.assertNotIn(secret,rendered)

    def test_inline_image_is_transient_and_uses_original_relay_adapter(self):
        data=b'\x89PNG\r\n\x1a\nprivate-inline-picture'
        inline='data:image/png;base64,'+base64.b64encode(data).decode()
        update_reference_relay_config(mode='external_http',upload_url='https://relay.invalid/upload',token='fixture-token')
        uploads=[]
        def upload(request):
            uploads.append(request.content);return httpx.Response(200,json={'url':'https://cdn.example.test/frame.png'})
        @asynccontextmanager
        async def client(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(upload)) as value:yield value
        provider,calls=self.provider()
        with patch('angemedia_gateway.providers.reference_relay.outbound_client',new=client):
            result=self.create_direct(provider,model='agnes-video-2.5-flash',images=[inline])
        self.assertIn(data,uploads[0]);self.assertEqual(calls[0]['json']['images'],['https://cdn.example.test/frame.png'])
        self.assertNotIn(inline,get_job(result['job_id'])['input_json'])

    def test_credential_rotation_between_submit_and_wait_never_polls_new_account(self):
        provider,calls=self.provider(on_submit=lambda job:update_provider_runtime_config('agnes_video',api_key='changed-account'))
        with self.assertRaises(VideoJobRefreshError) as caught:
            self.create_direct(provider,wait_for_completion=True)
        self.assertFalse(caught.exception.detail['retryable']);self.assertEqual(len(calls),1)
        job=self.direct_rows()[0];self.assertEqual(job['external_task_id'],'direct-video-task')
        self.assertEqual(job['status'],'running')

    def test_refresh_requires_original_account_and_can_resume_after_restore(self):
        provider,calls=self.provider();result=self.create_direct(provider)
        service=VideoJobRefreshService(poll_task_func=provider.poll_task,min_poll_interval_seconds=0,
            localize_video_result_func=AsyncMock(return_value={'task_id':result['task_id'],'status':'completed'}))
        update_provider_runtime_config('agnes_video',api_key='changed-account')
        with self.assertRaises(VideoJobRefreshError) as caught:asyncio.run(service.refresh(result['job_id']))
        self.assertEqual(caught.exception.status_code,409);self.assertEqual(len(calls),1)
        update_provider_runtime_config('agnes_video',api_key=None)
        self.assertEqual(asyncio.run(service.refresh(result['job_id']))['provider_status'],'completed')
        self.assertEqual(len(calls),2)

    def test_marker_tampering_cannot_turn_new_source_into_unguarded_legacy_lookup(self):
        provider,calls=self.provider();result=self.create_direct(provider)
        job=get_job(result['job_id']);payload=json.loads(job['input_json']);payload.pop('origin_kind')
        with closing(db_connect()) as conn:conn.execute('UPDATE jobs SET input_json=? WHERE id=?',(json.dumps(payload),job['id']))
        service=VideoJobRefreshService(poll_task_func=provider.poll_task,min_poll_interval_seconds=0)
        with self.assertRaises(VideoJobRefreshError):asyncio.run(service.refresh(job['id']))
        self.assertEqual(len(calls),1)

    def test_direct_request_uses_frozen_picture_and_runtime_after_call_begins(self):
        data=b'\x89PNG\r\n\x1a\noriginal-direct-frame';path=self.uploads/'direct.png';path.write_bytes(data)
        provider,calls=self.provider();original=provider._prepare_runtime_request
        before=resolve_provider_runtime_config('agnes_video')
        async def change(request):
            update_provider_runtime_config('agnes_video',api_key='changed-late-key',base_url_override='https://changed.invalid/v1')
            path.write_bytes(b'\x89PNG\r\n\x1a\nchanged-direct-frame')
            return await original(request)
        with patch.object(provider,'_prepare_runtime_request',side_effect=change):
            result=self.create_direct(provider,image='/uploads/direct.png')
        self.assertEqual(calls[0]['headers']['Authorization'],'Bearer '+before.api_key)
        self.assertEqual(calls[0]['url'],before.base_url+'/videos')
        self.assertEqual(base64.b64decode(calls[0]['json']['image']),data)
        self.assertNotEqual(get_video_job_origin(result['job_id'])['connection_digest'],connection_binding('agnes_video'))

    def test_direct_connection_change_at_final_gate_stops_before_post(self):
        from angemedia_gateway.services import video_job_origin as module
        provider,calls=self.provider();original=module.media_execution_scope
        @contextmanager
        def change(snapshot):
            with original(snapshot):
                update_provider_runtime_config('agnes_video',api_key='changed-before-direct-gate')
                yield
        with patch.object(module,'media_execution_scope',side_effect=change):
            with self.assertRaises(VideoJobRefreshError) as caught:self.create_direct(provider)
        self.assertEqual(caught.exception.status_code,409);self.assertEqual(calls,[])

    def test_wait_timeout_keeps_accepted_task_and_returns_non_retryable_recovery_identity(self):
        provider,calls=self.provider(polls=[ProviderTimeout('fixture poll timeout')])
        with self.assertRaises(VideoJobRefreshError) as caught:self.create_direct(provider,wait_for_completion=True)
        self.assertEqual(caught.exception.detail['task_id'],'direct-video-task')
        self.assertFalse(caught.exception.detail['retryable']);job=self.direct_rows()[0]
        self.assertEqual((job['status'],job['provider_status']),('running','queued'))
        self.assertIsNotNone(get_video_job_origin(job['id'])['external_task_digest'])
        self.assertEqual(len(calls),2)

    def test_explicit_provider_failure_is_not_recorded_as_local_timeout(self):
        provider,calls=self.provider(polls=[{'status':'cancelled'}])
        with self.assertRaises(VideoJobRefreshError) as caught:self.create_direct(provider,wait_for_completion=True)
        job=self.direct_rows()[0]
        self.assertEqual((job['status'],job['provider_status'],job['error_code']),('failed','cancelled','video_generation_failed'))
        self.assertEqual(caught.exception.detail['code'],'video_generation_failed')

    def test_interrupted_sync_can_return_original_remote_result_without_forced_download(self):
        provider,calls=self.provider()
        provider.wait_for_video=AsyncMock(side_effect=ProviderTimeout('wait expired'))
        with self.assertRaises(VideoJobRefreshError):self.create_direct(provider,wait_for_completion=True)
        job=self.direct_rows()[0]
        with patch.object(C,'AUTO_DOWNLOAD_GENERATED',False), patch('angemedia_gateway.media.download_remote_media',
                new=AsyncMock(side_effect=AssertionError('不应下载'))):
            result=asyncio.run(get_video(job['external_task_id'],agnes_video_provider=provider))
            repeated=asyncio.run(get_video(job['external_task_id'],agnes_video_provider=provider))
        self.assertEqual(result['video_url'],'https://cdn.example.test/direct.mp4')
        self.assertEqual(repeated['video_url'],result['video_url']);self.assertFalse(result['localized'])
        self.assertEqual(get_job(job['id'])['status'],'succeeded');self.assertEqual(len(calls),2)
        self.assertEqual(list_assets(job_id=job['id']),[])

    def test_cancelled_http_wait_is_not_upstream_cancellation_or_resubmission(self):
        provider,calls=self.provider()
        provider.wait_for_video=AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):self.create_direct(provider,wait_for_completion=True)
        job=self.direct_rows()[0];self.assertEqual((job['status'],job['provider_status']),('running','queued'))
        self.assertEqual(job['error_code'],'video_wait_interrupted');self.assertEqual(len(calls),1)
        self.assertIsNone(current_execution_scope())

    def test_rejected_and_unconfirmed_submissions_keep_fixed_diagnostics(self):
        for error,code in ((ProviderAuthError('fixture-secret-text'),'video_submit_rejected'),
                           (ProviderTimeout('fixture-secret-text'),'video_submit_ambiguous'),
                           (ProviderQueueFull('fixture-secret-text', status_code=503),'video_submit_queue_full')):
            with self.subTest(code=code):
                provider,calls=self.provider()
                provider._request_json = AsyncMock(side_effect=error)
                with self.assertRaises(VideoJobRefreshError) as caught:
                    self.create_direct(provider, negative_prompt=code)
                self.assertFalse(caught.exception.detail['retryable'])
                self.assertEqual(caught.exception.detail['code'], code)
                if code == 'video_submit_ambiguous':
                    self.assertEqual(caught.exception.detail['human_hint'], '')
                elif code == 'video_submit_queue_full':
                    self.assertIn('本次未受理', caught.exception.detail['human_hint'])
                job=self.direct_rows()[-1];self.assertEqual(job['error_code'],code)
                self.assertEqual(job['status'], 'running' if code == 'video_submit_ambiguous' else 'failed')
                self.assertNotIn('fixture-secret-text',repr(job))
                self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])

    def test_diagnostic_write_failure_preserves_original_http_cancellation(self):
        provider,calls=self.provider()
        provider.wait_for_video=AsyncMock(side_effect=asyncio.CancelledError())
        from angemedia_gateway.services import video_direct_execution as module
        original=module.transition_job_in_connection;count=0
        def fail(*args,**kwargs):
            nonlocal count
            count+=1
            if count>1:raise RuntimeError('fixture-private-diagnostic-error')
            return original(*args,**kwargs)
        with patch.object(module,'transition_job_in_connection',side_effect=fail):
            with self.assertRaises(asyncio.CancelledError):self.create_direct(provider,wait_for_completion=True)
        self.assertEqual(len(calls),1);self.assertEqual(self.direct_rows()[0]['status'],'running')
        self.assertIsNone(current_execution_scope())

    def test_initial_sql_failure_prevents_all_provider_calls(self):
        provider,calls=self.provider()
        with patch('angemedia_gateway.services.video_direct_execution.get_job',side_effect=RuntimeError('fixture-sql')):
            with self.assertRaises(RuntimeError):self.create_direct(provider)
        self.assertEqual(calls,[])

    def test_disabled_provider_before_post_is_rejected_without_unknown_submit_reservation(self):
        provider,calls=self.provider();enabled=Mock(side_effect=[True,False])
        with self.assertRaises(VideoProviderDisabled):
            asyncio.run(create_video(VideoRequest(prompt='disabled-before-post'),agnes_video_provider=provider,
                                     builtin_provider_enabled_func=enabled))
        self.assertEqual(calls,[]);job=self.direct_rows()[0]
        self.assertEqual((job['status'],job['error_code']),('failed','video_provider_disabled'))

    def test_missing_picture_is_preparation_failure_and_restoring_it_allows_new_request(self):
        provider,calls=self.provider()
        with self.assertRaises(ValueError):self.create_direct(provider,image='/uploads/missing.png')
        self.assertEqual(calls,[]);job=self.direct_rows()[0]
        self.assertEqual((job['status'],job['error_code']),('failed','video_execution_preparation_failed'))
        (self.uploads/'missing.png').write_bytes(b'\x89PNG\r\n\x1a\nrestored-picture')
        result=self.create_direct(provider,image='/uploads/missing.png')
        self.assertIn('task_id',result);self.assertEqual(len(calls),1)

    def test_uncertain_submit_keeps_original_content_reservation_on_repeated_request(self):
        provider,calls=self.provider();provider._request_json=AsyncMock(side_effect=ProviderTimeout('unknown'))
        with self.assertRaises(VideoJobRefreshError):self.create_direct(provider)
        replay,replay_calls=self.provider()
        result=self.create_direct(replay)
        self.assertEqual(result.status_code,409);self.assertEqual(replay_calls,[])
        self.assertEqual(len(self.direct_rows()),1);self.assertEqual(self.direct_rows()[0]['status'],'running')

    def test_task_and_source_binding_rollback_together_on_accept_write_failure(self):
        provider,calls=self.provider()
        with patch('angemedia_gateway.services.video_direct_execution.transition_job_in_connection',side_effect=RuntimeError('fixture-bind')):
            with self.assertRaises(VideoJobRefreshError) as caught:self.create_direct(provider)
        self.assertEqual(caught.exception.detail['code'],'video_submit_persistence_pending')
        self.assertEqual(caught.exception.detail['task_id'],'direct-video-task');self.assertFalse(caught.exception.detail['retryable'])
        self.assertNotIn('fixture-bind',repr(caught.exception.detail))
        job=self.direct_rows()[0]
        self.assertIsNone(job['external_task_id']);self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])
        self.assertEqual(len(calls),1)

    def test_final_history_cache_and_asset_writes_rollback_if_job_cas_fails(self):
        provider,calls=self.provider()
        from angemedia_gateway.services import video_direct_execution as module
        original=module.transition_job_in_connection;count=0
        def fail(*args,**kwargs):
            nonlocal count
            count+=1
            if count==2:raise RuntimeError('fixture-final-cas')
            return original(*args,**kwargs)
        with patch.object(module,'transition_job_in_connection',side_effect=fail):
            with self.assertRaises(VideoJobRefreshError) as caught:self.create_direct(provider)
        self.assertEqual(caught.exception.detail['code'],'video_result_persistence_pending')
        self.assertEqual(caught.exception.detail['task_id'],'direct-video-task');self.assertFalse(caught.exception.detail['retryable'])
        self.assertNotIn('fixture-final-cas',repr(caught.exception.detail))
        self.assertEqual(self.histories(),[])
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM video_tasks').fetchone()[0],0)
        self.assertIsNotNone(get_video_job_origin(self.direct_rows()[0]['id'])['external_task_digest'])

    def test_history_id_updates_once_through_download_pending_and_success(self):
        provider,calls=self.provider(polls=[{'status':'completed','video_url':'https://cdn.example.test/direct.mp4'}]*2)
        result=self.create_direct(provider);path=self.output/'saved.mp4';path.write_bytes(b'fixture-video')
        localize=AsyncMock(side_effect=[{'task_id':result['task_id'],'status':'completed','video_url':'https://cdn.example.test/direct.mp4'},
            {'task_id':result['task_id'],'status':'completed','video_url':'/generated/saved.mp4','local_path':str(path),'localized':True}])
        service=VideoJobRefreshService(poll_task_func=provider.poll_task,localize_video_result_func=localize,min_poll_interval_seconds=0)
        self.assertEqual(asyncio.run(service.refresh(result['job_id']))['refresh_status'],'download_pending')
        self.assertEqual(asyncio.run(service.refresh(result['job_id']))['status'],'succeeded')
        self.assertEqual(len(self.histories()),1);history=self.histories()[0]
        self.assertEqual((history['id'],history['status'],history['result_url']),(result['history_id'],'completed','/generated/saved.mp4'))
        self.assertEqual(len(list_assets(job_id=result['job_id'])),1)

    def test_cleaned_job_uses_cached_read_and_does_not_reopen_external_lookup(self):
        provider,calls=self.provider()
        with patch.object(C,'AUTO_DOWNLOAD_GENERATED',False):result=self.create_direct(provider,wait_for_completion=True)
        cleanup_jobs(job_ids=[self.direct_rows()[0]['id']])
        observed=asyncio.run(get_video(result['task_id'],agnes_video_provider=provider))
        self.assertTrue(observed['record_only']);self.assertFalse(observed['origin_verified']);self.assertEqual(len(calls),2)

    def test_inventory_recognizes_direct_source_and_unbound_attempts(self):
        provider,calls=self.provider();result=self.create_direct(provider)
        self.assertEqual(inventory_video_upgrade(C.DB_FILE)['reason_counts'],{'original_connection_required':1})
        self.assertFalse(inventory_video_upgrade(C.DB_FILE)['details'][0]['worker_managed'])

    def test_unchanged_wait_convenience_adapter_still_submits_exactly_once(self):
        provider,calls=self.provider()
        # 便捷方法本身不属于应用来源记录；此处只核对抽取等待循环没有增加第二次POST。
        provider._request_json=AsyncMock(side_effect=[{'video_id':'adapter-only','status':'queued'},
                                                     {'status':'completed','video_url':'https://cdn.example.test/x.mp4'}])
        from angemedia_gateway.schemas import VideoRequest
        result=asyncio.run(provider.generate_video(VideoRequest(prompt='adapter')))
        self.assertEqual(result['task_id'],'adapter-only');self.assertEqual(provider._request_json.await_count,2)

"""真实任务链路区分本地失败与服务方事实，兼容读取不能绕开正式worker。"""
import asyncio
from contextlib import closing
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import create_job, get_job
from angemedia_gateway.routes.jobs import _job_list_item
from angemedia_gateway.services.job_control import recovery_state
from angemedia_gateway.services.video_asset_import import VideoResultMissingUrl
from angemedia_gateway.services.video_execution import VideoPollResult
from angemedia_gateway.services.video_generation import get_video
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshService
from angemedia_gateway.services.video_polling import stored_provider_status


class VideoStateFixture(ActionWorkerFixture):
    def submitted(self):
        action=self.video_action();job=self.job(action);runtime=self.video_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        return action,get_job(job['id']),runtime

    def task(self):
        with closing(db_connect()) as conn:
            row=conn.execute("SELECT * FROM video_tasks WHERE task_id='action-video-task'").fetchone()
        return dict(row) if row else None

    def reader(self):
        provider=SimpleNamespace(poll_task=AsyncMock(side_effect=AssertionError('不应调用服务方')))
        download=AsyncMock(side_effect=AssertionError('不应下载'))
        return provider,download

    def observe(self,provider,download):
        return asyncio.run(get_video('action-video-task',agnes_video_provider=provider,localize_video_result_func=download))


class VideoProviderStateTest(VideoStateFixture,unittest.TestCase):
    def test_poll_exception_preserves_last_observation_and_task_record_exactly(self):
        _,job,runtime=self.submitted();before=self.task();worker=runtime.registry.get('video_poll').__self__
        with patch.object(worker.executor,'poll',new=AsyncMock(side_effect=TimeoutError('poll unavailable'))):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        current=get_job(job['id']);self.assertEqual(current['provider_status'],'queued')
        self.assertEqual(json.loads(current['output_json'])['provider_status'],'queued');self.assertEqual(self.task(),before)
        self.assertEqual(current['error_code'],'video_poll_failed');self.assertEqual(len(self.calls),1)

    def test_poll_deadline_is_local_failure_not_provider_failure(self):
        _,job,runtime=self.submitted();before=self.task();worker=runtime.registry.get('video_poll').__self__
        with patch.object(type(worker.policy),'exhausted',return_value=True):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
        current=get_job(job['id']);self.assertEqual(current['provider_status'],'queued');self.assertEqual(self.task(),before)
        self.assertEqual(recovery_state(current),'existing_task_unresolved');self.assertEqual(len(self.calls),1)

    def test_connection_rotation_does_not_fabricate_provider_failure(self):
        from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
        _,job,runtime=self.submitted();before=self.task()
        update_provider_runtime_config('agnes_video',api_key='fixture-new-account-123456789')
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
        self.assertEqual(self.task(),before);self.assertEqual(get_job(job['id'])['provider_status'],'queued')
        self.assertEqual(len(self.calls),1)

    def test_import_timeout_records_latest_completed_observation_even_if_previous_status_was_running(self):
        _,job,runtime=self.submitted();runtime.handle(self.message(job['id']))
        with closing(db_connect()) as conn:conn.execute("UPDATE jobs SET provider_status='running' WHERE id=?",(job['id'],))
        worker=runtime.registry.get('asset_import').__self__
        with patch.object(worker.asset_importer,'import_completed',new=AsyncMock(side_effect=RuntimeError('download unavailable'))):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        current=get_job(job['id']);self.assertEqual(current['status'],'running');self.assertEqual(current['provider_status'],'completed')
        self.assertEqual(self.task()['status'],'completed');self.assertEqual(_job_list_item(current)['provider_status'],'completed')
        self.assertEqual(current['error_code'],'video_asset_import_failed')

    def test_local_import_errors_keep_completed_provider_result_and_never_resubmit(self):
        for error,code in ((VideoResultMissingUrl('missing'),'video_result_missing_url'),
                           (ValueError('unsafe'),'video_asset_url_rejected')):
            with self.subTest(code=code):
                _,job,runtime=self.submitted();runtime.handle(self.message(job['id']));worker=runtime.registry.get('asset_import').__self__
                with patch.object(worker.asset_importer,'import_completed',new=AsyncMock(side_effect=error)):
                    self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
                current=get_job(job['id']);self.assertEqual(current['error_code'],code);self.assertEqual(current['provider_status'],'completed')
                self.assertEqual(self.task()['status'],'completed');self.assertEqual(recovery_state(current),'existing_task_unresolved')
                self.assertIn(runtime.handle(self.message(job['id']))['status'],{'duplicate','terminal'})
        self.assertEqual([row[0] for row in self.calls].count('submit'),2)

    def test_import_poll_failure_and_exhausted_download_preserve_real_observation(self):
        _,job,runtime=self.submitted();runtime.handle(self.message(job['id']));before=self.task();worker=runtime.registry.get('asset_import').__self__
        with patch.object(worker.executor,'poll',new=AsyncMock(side_effect=RuntimeError('query failed'))):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        self.assertEqual(self.task(),before);self.assertEqual(get_job(job['id'])['provider_status'],'completed')
        with patch.object(worker.asset_importer,'import_completed',new=AsyncMock(side_effect=RuntimeError('download failed'))),patch.object(type(worker.policy),'exhausted',return_value=True):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
        current=get_job(job['id']);self.assertEqual(current['error_code'],'video_pipeline_timeout')
        self.assertEqual(current['provider_status'],'completed');self.assertEqual(self.task()['status'],'completed')

    def test_successful_collection_after_retry_clears_stale_errors_and_retry_schedule(self):
        _,job,runtime=self.submitted();runtime.handle(self.message(job['id']));worker=runtime.registry.get('asset_import').__self__
        with patch.object(worker.asset_importer,'import_completed',new=AsyncMock(side_effect=RuntimeError('temporary download error'))):
            self.assertEqual(runtime.handle(self.message(job['id']))['status'],'scheduled')
        self.assertTrue(get_job(job['id'])['error_code']);self.assertTrue(get_job(job['id'])['next_retry_at'])
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'succeeded')
        current=get_job(job['id']);self.assertEqual(current['provider_status'],'completed')
        for field in ('error_code','error_message','error_category','human_hint','next_retry_at'):self.assertFalse(current[field],field)
        self.assertFalse(current['retryable']);self.assertEqual([item[0] for item in self.calls].count('submit'),1)

    def test_actual_provider_failure_and_cancellation_are_recorded_at_both_poll_stages(self):
        for stage,status in (('video_poll','failed'),('asset_import','cancelled')):
            with self.subTest(stage=stage):
                _,job,runtime=self.submitted()
                if stage=='asset_import':runtime.handle(self.message(job['id']))
                worker=runtime.registry.get(stage).__self__
                with patch.object(worker.executor,'poll',new=AsyncMock(return_value=VideoPollResult('action-video-task',status,error_message='Bearer fixture-secret'))):
                    self.assertEqual(runtime.handle(self.message(job['id']))['status'],'failed')
                current=get_job(job['id']);self.assertEqual(current['provider_status'],status);self.assertEqual(self.task()['status'],status)
                self.assertEqual(json.loads(current['output_json'])['provider_status'],status)
                self.assertNotIn('fixture-secret',repr(current));self.assertEqual(recovery_state(current),'provider_failed')

    def test_observation_and_local_failure_roll_back_together_when_persistence_fails(self):
        _,job,runtime=self.submitted();before=self.task();worker=runtime.registry.get('video_poll').__self__
        with patch.object(worker.executor,'poll',new=AsyncMock(return_value=VideoPollResult('action-video-task','failed'))),patch('angemedia_gateway.services.video_job_worker.upsert_video_task',side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):runtime.handle(self.message(job['id']))
        current=get_job(job['id']);self.assertEqual(current['status'],'running');self.assertEqual(current['provider_status'],'queued')
        self.assertEqual(self.task(),before)

    def test_compatibility_get_only_reads_active_failed_and_completed_worker_jobs(self):
        _,job,runtime=self.submitted();provider,download=self.reader();before=self.task();version=get_job(job['id'])['version']
        active=self.observe(provider,download);self.assertTrue(active['worker_managed']);self.assertEqual(active['job_status'],'running')
        self.assertEqual(self.task(),before);self.assertEqual(get_job(job['id'])['version'],version)
        for _ in range(2):runtime.handle(self.message(job['id']))
        completed=self.observe(provider,download);self.assertEqual(completed['status'],'completed');self.assertTrue(completed['video_url'].startswith('/generated/'))
        provider.poll_task.assert_not_awaited();download.assert_not_awaited()
        from angemedia_gateway.repositories.jobs import cleanup_jobs
        cleanup_jobs(job_ids=[job['id']])
        _,failed,runtime=self.submitted();worker=runtime.registry.get('video_poll').__self__
        with patch.object(type(worker.policy),'exhausted',return_value=True):runtime.handle(self.message(failed['id']))
        result=self.observe(provider,download);self.assertEqual(result['job_status'],'failed');self.assertEqual(result['status'],'queued')
        self.assertNotIn('video_url',result);provider.poll_task.assert_not_awaited()

    def test_corrupted_payload_cannot_bypass_worker_boundary_in_get_or_refresh(self):
        _,job,runtime=self.submitted();provider,download=self.reader()
        with closing(db_connect()) as conn:conn.execute("UPDATE jobs SET input_json='{}' WHERE id=?",(job['id'],))
        self.assertTrue(self.observe(provider,download)['worker_managed'])
        service=VideoJobRefreshService(poll_task_func=provider.poll_task,min_poll_interval_seconds=0)
        self.assertEqual(asyncio.run(service.refresh(job['id']))['refresh_status'],'worker_managed')
        provider.poll_task.assert_not_awaited()

    def test_database_lookup_failure_does_not_fall_through_to_external_query(self):
        provider,download=self.reader()
        with self.assertRaisesRegex(RuntimeError,'fixture lookup failure'):
            asyncio.run(get_video('unknown-task',agnes_video_provider=provider,localize_video_result_func=download,
                get_job_by_external_task_id_func=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('fixture lookup failure'))))
        provider.poll_task.assert_not_awaited();download.assert_not_awaited()

    def test_local_terminal_status_without_provider_evidence_remains_unknown(self):
        for status in ('failed','canceled'):
            job=create_job(kind='video',status=status,provider='agnes_video',external_task_id='legacy-task')
            self.assertEqual(stored_provider_status(job),'unknown');self.assertEqual(_job_list_item(job)['provider_status'],'unknown')
            response=asyncio.run(VideoJobRefreshService(poll_task_func=AsyncMock(side_effect=AssertionError('no poll'))).refresh(job['id']))
            self.assertEqual(response['provider_status'],'unknown');self.assertFalse(response['polled'])
        self.assertEqual(stored_provider_status({'provider_status':'poll_error','status':'failed'}),'unknown')
        self.assertEqual(stored_provider_status({'output_json':'not-json','status':'failed'}),'unknown')


class LegacyVideoObservationTest(VideoStateFixture,unittest.IsolatedAsyncioTestCase):
    async def test_downloader_exception_keeps_completed_and_can_collect_later(self):
        job=create_job(kind='video',status='running',provider='agnes_video',model='agnes-video-v2.0',external_task_id='legacy-dl')
        poll=AsyncMock(return_value={'status':'completed','video_url':'https://example.invalid/private?token=private-secret'})
        downloader=AsyncMock(side_effect=RuntimeError('Bearer fixture-download-secret'))
        service=VideoJobRefreshService(poll_task_func=poll,localize_video_result_func=downloader,min_poll_interval_seconds=0)
        response=await service.refresh(job['id']);current=get_job(job['id'])
        self.assertEqual(response['refresh_status'],'download_pending');self.assertEqual(current['provider_status'],'completed')
        self.assertEqual(current['status'],'running');self.assertNotIn('fixture-download-secret',repr(current))
        file=self.output/'legacy.mp4';file.write_bytes(b'video')
        downloader.side_effect=None;downloader.return_value={'status':'completed','video_url':'/generated/legacy.mp4','local_path':str(file),'localized':True}
        result=await service.refresh(job['id']);self.assertEqual(result['status'],'succeeded');self.assertEqual(result['provider_status'],'completed')

    async def test_explicit_provider_failure_is_distinct_from_download_or_query_failure(self):
        job=create_job(kind='video',status='running',provider='agnes_video',external_task_id='legacy-failed')
        service=VideoJobRefreshService(poll_task_func=AsyncMock(return_value={'status':'cancelled'}),min_poll_interval_seconds=0)
        result=await service.refresh(job['id']);self.assertEqual(result['status'],'failed');self.assertEqual(result['provider_status'],'cancelled')
        self.assertEqual(get_job(job['id'])['provider_status'],'cancelled')

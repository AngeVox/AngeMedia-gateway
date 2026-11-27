"""旧任务GET/refresh统一的原子收尾、迟到结果与作品引用保留。"""
import asyncio
from contextlib import closing
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.repositories.assets import list_assets, save_asset
from angemedia_gateway.repositories.jobs import create_job, get_job
from angemedia_gateway.repositories.video_tasks import upsert_video_task
from angemedia_gateway.services.generation_assets import save_generated_asset
from angemedia_gateway.services.video_generation import get_video
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshService


class LegacyVideoFixture(ActionWorkerFixture):
    def legacy(self,task_id='legacy-collection'):
        return create_job(kind='video',status='running',provider='agnes_video',model='agnes-video-v2.0',
                          prompt='原任务正文',external_task_id=task_id)

    def localized(self,task_id='legacy-collection',name='collected.mp4'):
        path=self.output/name;path.write_bytes(b'fixture-video')
        return {'task_id':task_id,'status':'completed','video_url':'/generated/'+name,'local_path':str(path),'localized':True}

    def refresh_service(self,**kwargs):
        return VideoJobRefreshService(poll_task_func=AsyncMock(return_value={'status':'completed','video_url':'https://example.invalid/video?token=private'}),
            localize_video_result_func=AsyncMock(return_value=self.localized()),min_poll_interval_seconds=0,**kwargs)

    def task_row(self,task_id='legacy-collection'):
        with closing(db_connect()) as conn:
            row=conn.execute('SELECT * FROM video_tasks WHERE task_id=?',(task_id,)).fetchone()
        return dict(row) if row else None


class LegacyCollectionTest(LegacyVideoFixture,unittest.IsolatedAsyncioTestCase):
    async def test_get_and_refresh_share_committed_asset_then_terminal_read_does_no_io(self):
        job=self.legacy();localized=self.localized();provider=SimpleNamespace(poll_task=AsyncMock(return_value={'status':'completed'}))
        download=AsyncMock(return_value=localized)
        result=await get_video(job['external_task_id'],agnes_video_provider=provider,localize_video_result_func=download)
        self.assertEqual(result['job_status'],'succeeded');self.assertEqual(result['video_url'],'/generated/collected.mp4')
        self.assertNotIn('local_path',result);self.assertEqual(len(list_assets(job_id=job['id'])),1)
        service=VideoJobRefreshService(poll_task_func=provider.poll_task,localize_video_result_func=download,min_poll_interval_seconds=0)
        self.assertEqual((await service.refresh(job['id']))['refresh_status'],'terminal')
        self.assertEqual((await get_video(job['external_task_id'],agnes_video_provider=provider,localize_video_result_func=download))['job_status'],'succeeded')
        provider.poll_task.assert_awaited_once();download.assert_awaited_once()

    async def test_no_asset_is_pending_and_get_never_trusts_upstream_local_path(self):
        job=self.legacy();provider=SimpleNamespace(poll_task=AsyncMock(return_value={**self.localized(),'Authorization':'Bearer upstream-private'}))
        download=AsyncMock(side_effect=lambda value,**kwargs:value)
        result=await get_video(job['external_task_id'],agnes_video_provider=provider,localize_video_result_func=download)
        self.assertEqual(result['job_status'],'running');self.assertEqual(result['refresh_status'],'download_pending')
        self.assertNotIn('video_url',result);self.assertNotIn('upstream-private',repr(result));self.assertEqual(list_assets(job_id=job['id']),[])

    async def test_partial_asset_write_is_rolled_back_but_download_pending_can_retry(self):
        job=self.legacy()
        def partial(**kwargs):save_generated_asset(**kwargs);raise RuntimeError('Bearer fixture-private')
        service=self.refresh_service(save_generated_asset_func=partial)
        first=await service.refresh(job['id']);self.assertEqual(first['refresh_status'],'download_pending')
        self.assertEqual(list_assets(job_id=job['id']),[]);self.assertEqual(get_job(job['id'])['status'],'running')
        self.assertNotIn('fixture-private',repr(get_job(job['id'])));self.assertEqual(self.task_row()['status'],'completed')
        service.save_generated_asset_func=save_generated_asset
        self.assertEqual((await service.refresh(job['id']))['status'],'succeeded');self.assertEqual(len(list_assets(job_id=job['id'])),1)

    async def test_final_job_write_failure_rolls_back_task_asset_and_event(self):
        job=self.legacy();service=self.refresh_service()
        with patch('angemedia_gateway.services.video_job_refresh.transition_job_in_connection',side_effect=RuntimeError('final write failure')):
            with self.assertRaisesRegex(RuntimeError,'final write failure'):await service.refresh(job['id'])
        self.assertEqual(get_job(job['id']),job);self.assertIsNone(self.task_row());self.assertEqual(list_assets(job_id=job['id']),[])
        self.assertEqual((await service.refresh(job['id']))['status'],'succeeded')

    async def test_late_failed_response_cannot_replace_completed_job_or_task(self):
        job=self.legacy();started,release=asyncio.Event(),asyncio.Event()
        async def delayed(task_id):started.set();await release.wait();return {'status':'failed'}
        slow=VideoJobRefreshService(poll_task_func=delayed,min_poll_interval_seconds=0)
        pending=asyncio.create_task(slow.refresh(job['id']));await started.wait()
        await self.refresh_service().refresh(job['id']);before=get_job(job['id']);task=self.task_row();release.set()
        response=await pending;self.assertEqual(response['refresh_status'],'superseded');self.assertEqual(response['status'],'succeeded')
        self.assertEqual(get_job(job['id']),before);self.assertEqual(self.task_row(),task)

    async def test_late_poll_error_returns_current_committed_result_without_overwriting_it(self):
        job=self.legacy();started,release=asyncio.Event(),asyncio.Event()
        async def delayed(task_id):started.set();await release.wait();raise RuntimeError('late error')
        slow=VideoJobRefreshService(poll_task_func=delayed,min_poll_interval_seconds=0)
        pending=asyncio.create_task(slow.refresh(job['id']));await started.wait();await self.refresh_service().refresh(job['id']);release.set()
        self.assertEqual((await pending)['status'],'succeeded');self.assertFalse(get_job(job['id'])['error_code'])

    async def test_identity_change_without_version_update_still_blocks_old_result(self):
        job=self.legacy()
        async def changed(task_id):
            with closing(db_connect()) as conn:conn.execute("UPDATE jobs SET external_task_id='different-origin' WHERE id=?",(job['id'],))
            return {'status':'failed'}
        result=await VideoJobRefreshService(poll_task_func=changed,min_poll_interval_seconds=0).refresh(job['id'])
        self.assertEqual(result['refresh_status'],'superseded');self.assertEqual(get_job(job['id'])['status'],'running');self.assertIsNone(self.task_row())

    async def test_asset_owned_by_another_job_is_not_stolen_or_modified(self):
        old=create_job(kind='image',status='running');file=self.localized();save_generated_asset(media_type='video',result=file,prompt='旧作品',model='old',provider='old',duration_ms=5,job_id=old['id'])
        before=list_assets(job_id=old['id'])[0];job=self.legacy()
        self.assertEqual((await self.refresh_service().refresh(job['id']))['refresh_status'],'download_pending')
        self.assertEqual(list_assets(job_id=old['id'])[0],before);self.assertEqual(list_assets(job_id=job['id']),[])

    async def test_deleted_local_file_does_not_turn_old_asset_record_into_success(self):
        job=self.legacy();file=self.localized();save_generated_asset(media_type='video',result=file,prompt='原任务正文',model='old',provider='agnes_video',duration_ms=0,job_id=job['id'])
        (self.output/'collected.mp4').unlink()
        service=VideoJobRefreshService(poll_task_func=AsyncMock(return_value={'status':'completed'}),min_poll_interval_seconds=0)
        self.assertEqual((await service.refresh(job['id']))['refresh_status'],'download_pending')
        self.assertEqual(get_job(job['id'])['status'],'running')

    async def test_wrong_provider_is_not_sent_to_agnes(self):
        job=create_job(kind='video',status='running',provider='different',external_task_id='foreign-platform')
        provider=SimpleNamespace(poll_task=AsyncMock(side_effect=AssertionError('不应查询错误平台')))
        result=await get_video(job['external_task_id'],agnes_video_provider=provider)
        self.assertEqual(result['refresh_status'],'unsupported');provider.poll_task.assert_not_awaited()


class VideoTaskRetentionTest(LegacyVideoFixture,unittest.TestCase):
    def test_status_only_update_preserves_local_references_and_summary_without_faking_status(self):
        result=self.localized();upsert_video_task('legacy-collection','原文','model-a','completed',result,duration_ms=125)
        upsert_video_task('legacy-collection','不可覆盖原文','model-b','running',{'status':'running','video_url':'https://remote.invalid/?token=private'})
        row=self.task_row();summary=json.loads(row['raw_json'])
        self.assertEqual(row['status'],'running');self.assertEqual(row['video_url'],'/generated/collected.mp4')
        self.assertEqual(row['local_path'],result['local_path']);self.assertEqual(row['duration_ms'],125)
        self.assertEqual(row['model'],'model-a');self.assertEqual(summary['model'],'model-a')
        self.assertTrue(summary['localized']);self.assertTrue(summary['has_local_path']);self.assertNotIn('token=private',repr(row))

    def test_new_local_result_can_update_reference_but_original_task_identity_is_retained(self):
        first=self.localized();upsert_video_task('legacy-collection','原文','model-a','completed',first,duration_ms=20)
        second=self.localized(name='second.mp4');upsert_video_task('legacy-collection','后来原文','model-b','completed',second,duration_ms=40)
        row=self.task_row();self.assertEqual(row['video_url'],'/generated/second.mp4');self.assertEqual(row['duration_ms'],40)
        self.assertEqual(row['prompt'],'原文');self.assertEqual(row['model'],'model-a')

    def test_caller_transaction_rolls_back_reference_update(self):
        upsert_video_task('legacy-collection','原文','model-a','completed',self.localized());before=self.task_row()
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with db_transaction(immediate=True) as conn:
                upsert_video_task('legacy-collection','原文','model-a','failed',{},conn=conn);raise RuntimeError('rollback')
        self.assertEqual(self.task_row(),before)

    def test_invalid_old_summary_does_not_break_reference_preservation(self):
        upsert_video_task('legacy-collection','原文','model-a','completed',self.localized())
        with closing(db_connect()) as conn:conn.execute("UPDATE video_tasks SET raw_json='invalid-json'")
        upsert_video_task('legacy-collection','原文','model-a','unknown',{})
        row=self.task_row();self.assertEqual(row['video_url'],'/generated/collected.mp4');self.assertTrue(json.loads(row['raw_json'])['has_local_path'])

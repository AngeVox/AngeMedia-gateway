"""外部编号不等于任务身份；歧义、无记录与损坏标记都不得触发错误外查。"""
import asyncio
from contextlib import closing
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from assistant_action_worker_support import ActionWorkerFixture
from fastapi import FastAPI
from studio_test_client import TestClient
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import (AmbiguousExternalTaskIdError, cleanup_jobs, create_job,
                                                get_job, get_job_by_external_task_id)
from angemedia_gateway.repositories.job_attempts import create_job_attempt
from angemedia_gateway.repositories.job_dispatches import create_job_dispatch
from angemedia_gateway.queue.settings import WORKER_TASK_NAME
from angemedia_gateway.repositories.video_tasks import upsert_video_task
from angemedia_gateway.routes import media
from angemedia_gateway.services.video_generation import get_video
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshError, VideoJobRefreshService
from angemedia_gateway.services.video_job_admission import is_worker_managed_video_job


class TaskLookupFixture(ActionWorkerFixture):
    def legacy(self,task_id,kind='video'):
        return create_job(kind=kind,status='running',provider='agnes_video',model='agnes-video-v2.0',external_task_id=task_id)

    def reader(self):
        return SimpleNamespace(poll_task=AsyncMock(side_effect=AssertionError('不应外查'))),AsyncMock(side_effect=AssertionError('不应下载'))

    def query(self,task_id,provider=None,download=None):
        if provider is None:provider,download=self.reader()
        return asyncio.run(get_video(task_id,agnes_video_provider=provider,localize_video_result_func=download))


class VideoTaskLookupTest(TaskLookupFixture,unittest.TestCase):
    def test_duplicate_external_id_never_selects_newest_job(self):
        first=self.legacy('same-id');second=self.legacy('same-id')
        with self.assertRaises(AmbiguousExternalTaskIdError):get_job_by_external_task_id('same-id',kind='video')
        provider,download=self.reader()
        with self.assertRaises(AmbiguousExternalTaskIdError):self.query('same-id',provider,download)
        provider.poll_task.assert_not_awaited();download.assert_not_awaited()
        self.assertEqual(get_job(first['id']),first);self.assertEqual(get_job(second['id']),second)

    def test_media_kind_filter_is_applied_before_ambiguity_check(self):
        video=self.legacy('same-kind-id');self.legacy('same-kind-id','image')
        self.assertEqual(get_job_by_external_task_id('same-kind-id',kind='video')['id'],video['id'])
        with self.assertRaises(AmbiguousExternalTaskIdError):get_job_by_external_task_id('same-kind-id')

    def test_unmatched_id_and_provider_task_cache_are_not_remote_query_authority(self):
        upsert_video_task('cache-only','旧任务','old-model','completed',{'status':'completed','video_url':'https://unknown.invalid/video'})
        provider,download=self.reader()
        with self.assertRaises(VideoJobRefreshError) as caught:self.query('missing-private-id',provider,download)
        self.assertEqual(caught.exception.status_code,404)
        self.assertEqual(caught.exception.detail,{'code':'video_task_record_unavailable'})
        cached=self.query('cache-only',provider,download);self.assertTrue(cached['record_only']);self.assertFalse(cached['origin_verified'])
        self.assertNotIn('video_url',cached);self.assertNotIn('local_path',cached)
        provider.poll_task.assert_not_awaited();download.assert_not_awaited();self.assertEqual(self.counts(),(0,0,0))

    def test_cleanup_keeps_asset_but_does_not_reopen_provider_lookup(self):
        job=self.job(self.video_action());runtime=self.video_runtime()
        for _ in range(3):runtime.handle(self.message(job['id']))
        assets=list_assets(job_id=job['id']);self.assertEqual(len(assets),1)
        cleanup_jobs(job_ids=[job['id']]);provider,download=self.reader()
        cached=self.query('action-video-task',provider,download);self.assertTrue(cached['record_only'])
        self.assertNotIn('job_id',cached);self.assertTrue(cached['video_url'].startswith('/generated/'))
        self.assertEqual(list_assets()[0]['id'],assets[0]['id']);provider.poll_task.assert_not_awaited()
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM job_submission_keys').fetchone()[0],1)

    def test_cached_completed_result_is_readable_but_never_proves_original_account(self):
        path=self.output/'cached.mp4';path.write_bytes(b'cached-video')
        upsert_video_task('sync-result','原结果','old-model','completed',{'video_url':'/generated/cached.mp4','local_path':str(path),'localized':True})
        provider,download=self.reader();result=self.query('sync-result',provider,download)
        self.assertEqual(result['video_url'],'/generated/cached.mp4');self.assertTrue(result['localized'])
        self.assertTrue(result['record_only']);self.assertFalse(result['origin_verified']);self.assertNotIn('job_id',result)
        provider.poll_task.assert_not_awaited();download.assert_not_awaited()

    def test_missing_cached_file_does_not_trigger_download_or_claim_local_availability(self):
        upsert_video_task('missing-cache','旧任务','old-model','completed',{'video_url':'/generated/missing.mp4','localized':True})
        provider,download=self.reader();result=self.query('missing-cache',provider,download)
        self.assertFalse(result['localized']);self.assertNotIn('video_url',result);provider.poll_task.assert_not_awaited()

    def test_newer_legacy_match_cannot_hide_older_worker_match(self):
        job=self.job(self.video_action());runtime=self.video_runtime();runtime.handle(self.message(job['id']))
        self.legacy('action-video-task');provider,download=self.reader()
        with self.assertRaises(AmbiguousExternalTaskIdError):self.query('action-video-task',provider,download)
        self.assertEqual(get_job(job['id'])['stage'],'video_poll');provider.poll_task.assert_not_awaited()

    def test_independent_dispatch_attempt_and_submission_facts_keep_worker_boundary(self):
        for evidence in ('dispatch','attempt','submission'):
            with self.subTest(evidence=evidence):
                job=self.legacy('evidence-'+evidence)
                if evidence=='dispatch':
                    create_job_dispatch(job_id=job['id'],topic=WORKER_TASK_NAME,payload={'job_id':job['id']})
                elif evidence=='attempt':
                    create_job_attempt(job_id=job['id'],attempt_number=1,stage='video_poll',worker_kind='local',status='running')
                else:
                    with closing(db_connect()) as conn:
                        conn.execute('INSERT INTO job_submission_keys VALUES(?,?,?,?,?)',('fixture',uuid4().hex,'0'*64,job['id'],'2026-10-29'))
                self.assertTrue(is_worker_managed_video_job(job));provider,download=self.reader()
                self.assertTrue(self.query(job['external_task_id'],provider,download)['worker_managed'])
                response=asyncio.run(VideoJobRefreshService(poll_task_func=provider.poll_task).refresh(job['id']))
                self.assertEqual(response['refresh_status'],'worker_managed');provider.poll_task.assert_not_awaited()

    def test_submitted_action_receipt_survives_loss_of_mutable_worker_flags(self):
        action=self.video_action();job=self.job(action)
        with closing(db_connect()) as conn:
            conn.execute("UPDATE jobs SET input_json='{}',stage='admitted',worker_kind=NULL,dedupe_scope='content',external_task_id='receipt-id' WHERE id=?",(job['id'],))
            conn.execute('DELETE FROM job_dispatches WHERE job_id=?',(job['id'],))
            conn.execute('DELETE FROM job_submission_keys WHERE job_id=?',(job['id'],))
            conn.execute('DELETE FROM assistant_action_execution_grants WHERE job_id=?',(job['id'],))
        provider,download=self.reader();self.assertTrue(self.query('receipt-id',provider,download)['worker_managed'])
        provider.poll_task.assert_not_awaited()

    def test_worker_evidence_created_during_poll_stops_legacy_persistence(self):
        job=self.legacy('changed-worker')
        async def poll(task_id):
            create_job_dispatch(job_id=job['id'],topic=WORKER_TASK_NAME,payload={'job_id':job['id']})
            return {'status':'failed'}
        response=asyncio.run(VideoJobRefreshService(poll_task_func=poll,min_poll_interval_seconds=0).refresh(job['id']))
        self.assertEqual(response['refresh_status'],'superseded');self.assertEqual(get_job(job['id']),job)
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM video_tasks').fetchone()[0],0)


class VideoTaskLookupHttpTest(TaskLookupFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.app=FastAPI();self.app.include_router(media.router);self.client=TestClient(self.app)
        self.client.seed_session(self.cookies['alice'])

    def tearDown(self):self.client.close();super().tearDown()

    def test_missing_and_ambiguous_ids_return_fixed_codes_without_identity_echo(self):
        response=self.client.get('/v1/videos/private-unknown')
        self.assertEqual(response.status_code,404);self.assertEqual(response.json()['detail'],{'code':'video_task_record_unavailable'})
        self.legacy('private-duplicate');self.legacy('private-duplicate')
        response=self.client.get('/v1/videos/private-duplicate')
        self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail'],{'code':'video_task_identity_ambiguous'})
        self.assertNotIn('private-duplicate',response.text)

    def test_authentication_boundary_and_worker_read_remain_intact(self):
        self.client.cookies.clear();self.assertEqual(self.client.get('/v1/videos/unknown').status_code,401)
        self.client.seed_session(self.cookies['alice'])
        job=self.job(self.video_action());self.video_runtime().handle(self.message(job['id']))
        response=self.client.get('/v1/videos/action-video-task');self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['worker_managed']);self.assertNotIn('local_path',response.text)

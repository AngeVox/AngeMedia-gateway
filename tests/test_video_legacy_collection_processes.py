"""独立进程验证旧任务提交边界退出、并发CAS及状态更新保留作品引用。"""
import asyncio
from contextlib import closing
import multiprocessing
import os
import unittest
from unittest.mock import AsyncMock, patch

from test_assistant_action_processes import _service
from test_video_legacy_collection import LegacyVideoFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assets import list_assets
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.video_tasks import upsert_video_task
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshService


def child_service(path,job_id,name):
    _service(path)
    import angemedia_gateway.config as C
    helper=LegacyVideoFixture();helper.output=C.OUTPUT_DIR
    job=get_job(job_id);result=helper.localized(job['external_task_id'],name=name)
    return VideoJobRefreshService(poll_task_func=AsyncMock(return_value={'status':'completed'}),
        localize_video_result_func=AsyncMock(return_value=result),min_poll_interval_seconds=0)


def exit_child(path,job_id,boundary):
    service=child_service(path,job_id,job_id+'.mp4')
    from angemedia_gateway.services import video_job_refresh
    original=video_job_refresh.transition_job_in_connection
    def terminate(*args,**kwargs):original(*args,**kwargs);os._exit(23)
    if boundary=='before_commit':
        with patch.object(video_job_refresh,'transition_job_in_connection',side_effect=terminate):
            asyncio.run(service.refresh(job_id))
    asyncio.run(service.refresh(job_id));os._exit(24)


def collection_child(path,job_id,name,barrier,output):
    service=child_service(path,job_id,name)
    async def poll(task_id):barrier.wait(20);return {'status':'completed'}
    service.poll_task_func=poll
    response=asyncio.run(service.refresh(job_id));output.put(response['refresh_status'])


def reference_child(path,result,barrier,output):
    _service(path);barrier.wait(20)
    for _ in range(10):upsert_video_task('legacy-collection','原文','model-a','running',result)
    output.put(True)


class LegacyCollectionProcessesTest(LegacyVideoFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.context=multiprocessing.get_context('spawn');self.children=[];self.queues=[]

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(5)
        for queue in self.queues:queue.close();queue.join_thread()
        super().tearDown()

    def queue(self):
        value=self.context.Queue();self.queues.append(value);return value

    def spawn(self,function,*args):
        child=self.context.Process(target=function,args=(str(self.service.db_path),*args));self.children.append(child);child.start();return child

    def test_before_commit_and_after_reply_exit_recover_exactly_one_persisted_result(self):
        for boundary,code in (('before_commit',23),('after_commit',24)):
            with self.subTest(boundary=boundary):
                job=self.legacy('legacy-'+boundary);child=self.spawn(exit_child,job['id'],boundary)
                child.join(30);self.assertEqual(child.exitcode,code)
                current=get_job(job['id']);self.assertEqual(current['version'],0 if boundary=='before_commit' else 1)
                self.assertEqual(len(list_assets(job_id=job['id'])),0 if boundary=='before_commit' else 1)
                self.assertEqual(self.task_row(job['external_task_id']) is None,boundary=='before_commit')
                service=child_service(str(self.service.db_path),job['id'],job['id']+'.mp4')
                result=asyncio.run(service.refresh(job['id']));self.assertEqual(result['status'],'succeeded')
                self.assertEqual(len(list_assets(job_id=job['id'])),1);self.assertEqual(get_job(job['id'])['version'],1)

    def test_two_process_collectors_have_one_committed_job_asset_and_task_result(self):
        job=self.legacy();barrier=self.context.Barrier(2);output=self.queue()
        children=[self.spawn(collection_child,job['id'],name,barrier,output) for name in ('first.mp4','second.mp4')]
        results=[output.get(timeout=30) for _ in children]
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertCountEqual(results,['completed','superseded']);self.assertEqual(get_job(job['id'])['version'],1)
        assets=list_assets(job_id=job['id']);self.assertEqual(len(assets),1)
        self.assertEqual(self.task_row()['video_url'],assets[0]['url_path']);self.assertEqual(self.counts(),(1,0,0))

    def test_status_only_writer_cannot_erase_concurrent_completed_local_reference(self):
        barrier=self.context.Barrier(2);output=self.queue();result=self.localized()
        children=[self.spawn(reference_child,result,barrier,output),self.spawn(reference_child,{},barrier,output)]
        for _ in children:self.assertTrue(output.get(timeout=30))
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        row=self.task_row();self.assertEqual(row['video_url'],'/generated/collected.mp4');self.assertEqual(row['local_path'],result['local_path'])

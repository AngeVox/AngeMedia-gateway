"""即时提交的事务退出、已受理冷取回与相同意图并发，均不制造第二次生成。"""
import asyncio
from contextlib import closing
import multiprocessing
import os
import unittest
from unittest.mock import AsyncMock, patch

from video_direct_support import DirectVideoFixture
from test_assistant_action_processes import _service
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin
from angemedia_gateway.services.video_job_refresh import VideoJobRefreshService, VideoJobRefreshError


class ChildHelper(DirectVideoFixture,unittest.TestCase):
    pass


def helper(path):
    _service(path)
    value=ChildHelper();value.calls=[];value.output=C.OUTPUT_DIR;value.uploads=C.UPLOAD_DIR
    return value


def exit_child(path,boundary):
    fixture=helper(path);provider,calls=fixture.provider()
    if boundary=='before_bind_commit':
        from angemedia_gateway.services import video_direct_execution as module
        original=module.bind_video_job_origin
        def terminate(*args):original(*args);os._exit(23)
        with patch.object(module,'bind_video_job_origin',side_effect=terminate):fixture.create_direct(provider)
    fixture.create_direct(provider);os._exit(24)


def refresh_child(path,job_id,output):
    fixture=helper(path);provider,calls=fixture.provider()
    service=VideoJobRefreshService(poll_task_func=provider.poll_task,min_poll_interval_seconds=0,
        localize_video_result_func=AsyncMock(return_value={'status':'completed','task_id':'direct-video-task'}))
    try:
        result=asyncio.run(service.refresh(job_id));output.put((result['refresh_status'],len(calls)))
    except VideoJobRefreshError as error:
        output.put((error.status_code,len(calls)))


def concurrent_child(path,barrier,output):
    fixture=helper(path);provider,calls=fixture.provider()
    barrier.wait(20);result=fixture.create_direct(provider)
    output.put((result.status_code if hasattr(result,'status_code') else 200,len(calls)))


class DirectVideoProcessesTest(DirectVideoFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.context=multiprocessing.get_context('spawn');self.children=[];self.queues=[]

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(5)
        for queue in self.queues:queue.close();queue.join_thread()
        super().tearDown()

    def spawn(self,target,*args):
        child=self.context.Process(target=target,args=(str(C.DB_FILE),*args));self.children.append(child);child.start();return child

    def queue(self):
        value=self.context.Queue();self.queues.append(value);return value

    def test_process_exit_inside_bind_transaction_leaves_real_attempt_without_partial_task_id(self):
        child=self.spawn(exit_child,'before_bind_commit');child.join(30);self.assertEqual(child.exitcode,23)
        job=self.direct_rows()[0]
        self.assertIsNone(job['external_task_id']);self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])
        self.assertEqual(job['status'],'running');self.assertEqual(self.histories(),[])
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM job_dispatches').fetchone()[0],0)
        provider,calls=self.provider();replayed=self.create_direct(provider)
        self.assertEqual(replayed.status_code,409);self.assertEqual(calls,[])

    def test_after_commit_cold_refresh_rejects_new_account_and_restores_original_task_without_submit(self):
        child=self.spawn(exit_child,'after_commit');child.join(30);self.assertEqual(child.exitcode,24)
        job=self.direct_rows()[0];origin=get_video_job_origin(job['id']);history=self.histories()[0]['id']
        update_provider_runtime_config('agnes_video',api_key='changed-cold-account')
        output=self.queue();child=self.spawn(refresh_child,job['id'],output)
        self.assertEqual(output.get(timeout=30),(409,0));child.join(10);self.assertEqual(child.exitcode,0)
        update_provider_runtime_config('agnes_video',api_key=None)
        child=self.spawn(refresh_child,job['id'],output)
        state,calls=output.get(timeout=30);child.join(10);self.assertEqual(child.exitcode,0)
        self.assertEqual((state,calls),('download_pending',1))
        self.assertEqual(get_video_job_origin(job['id']),origin);self.assertEqual(self.histories()[0]['id'],history)

    def test_concurrent_same_request_reserves_one_execution_before_any_provider_post(self):
        barrier=self.context.Barrier(2);output=self.queue()
        children=[self.spawn(concurrent_child,barrier,output) for _ in range(2)]
        results=[output.get(timeout=30) for _ in children]
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertCountEqual([status for status,_ in results],[200,409])
        self.assertEqual(sum(calls for _,calls in results),1)
        self.assertEqual(len(self.direct_rows()),1);self.assertEqual(len(self.histories()),1)

"""现代本地图生视频的冷进程收尾、提交退出及双进程领取，复用真实worker和授权。"""
from contextlib import closing
import json
import multiprocessing
import os
import unittest
from unittest.mock import patch

from video_reference_queue_support import ReferenceQueueFixture
from test_assistant_action_processes import _service
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.reference_relay_config import update_reference_relay_config
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin


class ChildHelper(ReferenceQueueFixture, unittest.TestCase):
    pass


def child_runtime(path):
    _service(path)
    helper = ChildHelper(); helper.calls = []; helper.output = C.OUTPUT_DIR; helper.uploads = C.UPLOAD_DIR
    return helper, helper.real_adapter_runtime()


def submit_exit(path, job_id, boundary):
    helper, (runtime, request, uploads) = child_runtime(path)
    if boundary == 'before_commit':
        from angemedia_gateway.services import video_job_worker
        original = video_job_worker.bind_video_job_origin
        def terminate(*args):
            original(*args)
            (C.DB_FILE.parent/'boundary.json').write_text(json.dumps({'uploads':len(uploads),'submits':request.await_count}))
            os._exit(23)
        with patch.object(video_job_worker, 'bind_video_job_origin', side_effect=terminate):
            runtime.handle(helper.message(job_id))
    runtime.handle(helper.message(job_id)); os._exit(24)


def continue_child(path, job_id, output):
    helper, (runtime, request, uploads) = child_runtime(path)
    request.side_effect = [{'status':'completed','video_url':'https://example.invalid/complete.mp4'}]*2
    try:
        states = [runtime.handle(helper.message(job_id))['status'] for _ in range(2)]
        output.put((states,len(uploads),request.await_count))
    finally:
        helper.doCleanups()


def race_child(path, job_id, barrier, output):
    helper, (runtime, request, uploads) = child_runtime(path)
    try:
        message = helper.message(job_id); barrier.wait(20)
        state = runtime.handle(message)['status']
        output.put((state,len(uploads),request.await_count))
    finally:
        helper.doCleanups()


class VideoReferenceProcessesTest(ReferenceQueueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.context=multiprocessing.get_context('spawn'); self.children=[]; self.queues=[]

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive(): child.terminate(); child.join(5)
        for queue in self.queues: queue.close(); queue.join_thread()
        super().tearDown()

    def spawn(self, target, *args):
        child=self.context.Process(target=target,args=(str(C.DB_FILE),*args));self.children.append(child);child.start();return child

    def queue(self):
        value=self.context.Queue();self.queues.append(value);return value

    def test_cold_assistant_followup_needs_no_relay_or_reference_after_accepted_submit(self):
        self.configure_relay(); job=self.modern_job(assistant=True)
        child=self.spawn(submit_exit,job['id'],'after_commit');child.join(30);self.assertEqual(child.exitcode,24)
        origin=get_video_job_origin(job['id']);self.assertIsNotNone(origin['external_task_digest'])
        update_reference_relay_config(mode='disabled',upload_url=None,token=None)
        (self.uploads/'relay-frame.png').unlink()
        output=self.queue();child=self.spawn(continue_child,job['id'],output)
        states,uploads,polls=output.get(timeout=30);child.join(10);self.assertEqual(child.exitcode,0)
        self.assertEqual((states,uploads,polls),(['scheduled','succeeded'],0,2))
        self.assertEqual(get_video_job_origin(job['id']),origin)

    def test_exit_inside_task_bind_transaction_retains_attempt_and_never_reuploads_or_resubmits(self):
        self.configure_relay();job=self.modern_job()
        child=self.spawn(submit_exit,job['id'],'before_commit');child.join(30);self.assertEqual(child.exitcode,23)
        self.assertEqual(json.loads((C.DB_FILE.parent/'boundary.json').read_text()),{'uploads':1,'submits':1})
        self.assertIsNone(get_job(job['id'])['external_task_id'])
        self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])
        runtime,request,uploads=self.real_adapter_runtime()
        self.assertEqual(runtime.handle(self.message(job['id']))['status'],'duplicate')
        self.assertEqual(uploads,[]);request.assert_not_awaited()

    def test_two_process_claims_upload_and_submit_once_with_one_followup_outbox(self):
        self.configure_relay();job=self.modern_job(assistant=True)
        barrier=self.context.Barrier(2);output=self.queue()
        children=[self.spawn(race_child,job['id'],barrier,output) for _ in range(2)]
        results=[output.get(timeout=30) for _ in children]
        for child in children: child.join(10);self.assertEqual(child.exitcode,0)
        self.assertEqual(sum(uploads for _,uploads,_ in results),1)
        self.assertEqual(sum(calls for _,_,calls in results),1)
        self.assertEqual(sum(state=='scheduled' for state,_,_ in results),1)
        self.assertTrue(all(state in {'scheduled','duplicate','stale','ambiguous'} for state,_,_ in results))
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM job_dispatches WHERE job_id=? AND payload_json LIKE '%video_poll%'",
                                          (job['id'],)).fetchone()[0],1)

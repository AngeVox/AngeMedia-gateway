"""独立进程验证撤销/worker领取唯一胜者、重复撤销及提交边界退出恢复。"""
from contextlib import closing
import multiprocessing
import os
import unittest
from unittest.mock import patch

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.assistant_run_access import RunAccess
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.services.job_control import JobControlError
from test_assistant_action_processes import _service


def cancel_child(path,action,session_hash,barrier,output):
    service=_service(path);barrier.wait(20)
    try:
        result=service.cancel_job(action['id'],0,access=RunAccess('alice',session_hash))
        output.put(('cancel',result['created']))
    except JobControlError as error:output.put(('cancel',error.code))


def worker_child(path,job_id,barrier,output):
    _service(path)
    import angemedia_gateway.config as C
    helper=ActionWorkerFixture();helper.calls=[];helper.output=C.OUTPUT_DIR
    runtime=helper.image_runtime();message=helper.message(job_id)
    barrier.wait(20);result=runtime.handle(message)
    output.put(('worker',result['status'],len(helper.calls)))


def exit_child(path,action,session_hash,boundary):
    service=_service(path)
    if boundary=='before_commit':
        with patch.object(service,'_view',side_effect=lambda *args:os._exit(23)):
            service.cancel_job(action['id'],0,access=RunAccess('alice',session_hash))
    service.cancel_job(action['id'],0,access=RunAccess('alice',session_hash));os._exit(24)


class JobCancellationProcessesTest(ActionWorkerFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.context=multiprocessing.get_context('spawn');self.children=[];self.queues=[]

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive():child.terminate();child.join(5)
        for queue in self.queues:queue.close();queue.join_thread()
        super().tearDown()

    def spawn(self,function,*args):
        child=self.context.Process(target=function,args=(str(self.service.db_path),*args));self.children.append(child);child.start();return child

    def ready(self):
        action=self.propose();job_id=self.confirm(action)['action']['job']['id'];return action,job_id

    def queue(self):
        value=self.context.Queue();self.queues.append(value);return value

    def test_real_worker_and_cancel_have_one_winner(self):
        for _ in range(4):
            action,job_id=self.ready();barrier=self.context.Barrier(2);output=self.queue()
            children=[self.spawn(cancel_child,action,self.alice.session_hash,barrier,output),self.spawn(worker_child,job_id,barrier,output)]
            results=[output.get(timeout=30) for _ in children]
            for child in children:child.join(10);self.assertEqual(child.exitcode,0)
            canceled=next(item[1] for item in results if item[0]=='cancel')
            worker=next(item for item in results if item[0]=='worker')
            self.assertEqual(get_job(job_id)['status'],'canceled' if canceled is True else 'succeeded')
            self.assertEqual(worker[2],0 if canceled is True else 1)
            with closing(db_connect()) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM job_events WHERE job_id=? AND event_type='job_canceled_before_start'",(job_id,)).fetchone()[0],int(canceled is True))

    def test_two_cancellations_share_one_committed_effect(self):
        action,job_id=self.ready();barrier=self.context.Barrier(2);output=self.queue()
        children=[self.spawn(cancel_child,action,self.alice.session_hash,barrier,output) for _ in range(2)]
        results=[output.get(timeout=30) for _ in children]
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertEqual(sum(item[1] is True for item in results),1);self.assertEqual(get_job(job_id)['version'],1)

    def test_exit_before_commit_and_after_commit_replay_without_resubmission(self):
        for boundary,code in (('before_commit',23),('after_commit',24)):
            with self.subTest(boundary=boundary):
                action,job_id=self.ready();child=self.spawn(exit_child,action,self.alice.session_hash,boundary)
                child.join(30);self.assertEqual(child.exitcode,code)
                self.assertEqual(get_job(job_id)['status'],'queued' if boundary=='before_commit' else 'canceled')
                result=self.service.cancel_job(action['id'],0,access=self.alice)
                self.assertEqual(result['created'],boundary=='before_commit')
                self.assertFalse(self.service.cancel_job(action['id'],0,access=self.alice)['created'])
        self.assertEqual(self.counts(),(2,2,2))

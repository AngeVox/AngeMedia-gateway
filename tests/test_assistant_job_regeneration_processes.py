"""独立进程检验新提案幂等性及事务提交前后退出；不调用生成服务。"""
from contextlib import closing
import multiprocessing
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_action_support import ActionFixture
from angemedia_gateway.assistant_run_access import RunAccess
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from test_assistant_action_processes import _service


def race_child(path,action,version,key,session_hash,barrier,output):
    service=_service(path);barrier.wait(20)
    result=service.regenerate(action['id'],version,key,access=RunAccess('alice',session_hash))
    output.put((result['action']['id'],result['created']))


def exit_child(path,action,version,key,session_hash,boundary):
    service=_service(path)
    if boundary=='before_commit':
        with patch.object(service,'_view',side_effect=lambda *args:os._exit(23)):
            service.regenerate(action['id'],version,key,access=RunAccess('alice',session_hash))
    service.regenerate(action['id'],version,key,access=RunAccess('alice',session_hash));os._exit(24)


class JobRegenerationProcessesTest(ActionFixture,unittest.TestCase):
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
        action=self.propose();job=get_job(self.confirm(action)['action']['job']['id'])
        self.service.cancel_job(action['id'],job['version'],access=self.alice)
        return action,get_job(job['id'])

    def test_four_processes_create_one_unconfirmed_proposal(self):
        action,job=self.ready();barrier=self.context.Barrier(4);output=self.context.Queue();self.queues.append(output);key=uuid4().hex
        children=[self.spawn(race_child,action,job['version'],key,self.alice.session_hash,barrier,output) for _ in range(4)]
        results=[output.get(timeout=30) for _ in children]
        for child in children:child.join(10);self.assertEqual(child.exitcode,0)
        self.assertEqual(sum(created for _,created in results),1);self.assertEqual(len({identity for identity,_ in results}),1)
        self.assertEqual(self.counts(),(1,1,1));self.assertEqual(get_job(job['id']),job)
        with closing(db_connect()) as conn:self.assertEqual(conn.execute("SELECT count(*) FROM assistant_actions WHERE status='draft'").fetchone()[0],1)

    def test_exit_before_commit_and_after_reply_recovers_one_draft_without_submission(self):
        for boundary,code in (('before_commit',23),('after_commit',24)):
            with self.subTest(boundary=boundary):
                action,job=self.ready();key=uuid4().hex
                child=self.spawn(exit_child,action,job['version'],key,self.alice.session_hash,boundary)
                child.join(30);self.assertEqual(child.exitcode,code)
                result=self.service.regenerate(action['id'],job['version'],key,access=self.alice)
                self.assertEqual(result['created'],boundary=='before_commit');self.assertEqual(result['action']['status'],'draft')
                self.assertFalse(self.service.regenerate(action['id'],job['version'],key,access=self.alice)['created'])
                self.assertEqual(get_job(job['id']),job)
        self.assertEqual(self.counts(),(2,2,2))

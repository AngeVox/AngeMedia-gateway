"""跨进程入队与提交前后崩溃；重放必须保持同一任务和发件箱。"""
from __future__ import annotations

from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import unittest
from uuid import uuid4

from assistant_run_support import RunFixture
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.job_submission import JobSubmissionKey
from angemedia_gateway.services.job_admission import JobAdmissionService


def _admit(path, key):
    import angemedia_gateway.config as C
    C.DB_FILE = Path(path)
    return JobAdmissionService().admit(kind='image', stage='image_generate', request_hash='a'*64,
        request_hash_version=2, payload={'prompt': '跨进程请求'}, submission_key=JobSubmissionKey('assistant_action', key))


def _race(path, key, barrier, output):
    barrier.wait(15)
    result = _admit(path, key)
    output.put((result.job['id'], result.created))


def _crash(path, key, boundary):
    import angemedia_gateway.config as C
    C.DB_FILE = Path(path)
    if boundary == 'before_commit':
        with db_transaction(immediate=True) as conn:
            JobAdmissionService().admit(kind='image', stage='image_generate', request_hash='a'*64,
                request_hash_version=2, payload={'prompt': '跨进程请求'},
                submission_key=JobSubmissionKey('assistant_action', key), conn=conn)
            os._exit(23)
    _admit(path, key)
    os._exit(24)


class JobSubmissionProcessesTest(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.context = multiprocessing.get_context('spawn'); self.children = []; self.queues = []

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive(): child.terminate(); child.join(5)
        for queue in self.queues: queue.close(); queue.join_thread()
        super().tearDown()

    def spawn(self, function, *args):
        child = self.context.Process(target=function, args=(str(self.repo.db_path), *args))
        self.children.append(child); child.start()
        return child

    def test_four_processes_with_same_key_commit_one_job_and_dispatch(self):
        barrier, output = self.context.Barrier(4), self.context.Queue(); self.queues.append(output)
        key = uuid4().hex
        children = [self.spawn(_race, key, barrier, output) for _ in range(4)]
        results = [output.get(timeout=20) for _ in range(4)]
        for child in children: child.join(10); self.assertEqual(child.exitcode, 0)
        self.assertEqual(len({item[0] for item in results}), 1); self.assertEqual(sum(item[1] for item in results), 1)
        with closing(db_connect()) as conn:
            for table in ('jobs', 'job_events', 'job_dispatches', 'job_submission_keys'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1)

    def test_process_death_before_commit_or_before_reply_never_duplicates_admission(self):
        for boundary, expected_exit in [('before_commit', 23), ('after_commit', 24)]:
            with self.subTest(boundary=boundary):
                key = uuid4().hex; child = self.spawn(_crash, key, boundary)
                child.join(20); self.assertEqual(child.exitcode, expected_exit)
                recovered = _admit(str(self.repo.db_path), key)
                self.assertEqual(recovered.created, boundary == 'before_commit')
                self.assertEqual(_admit(str(self.repo.db_path), key).job['id'], recovered.job['id'])
        with closing(db_connect()) as conn:
            for table in ('jobs', 'job_events', 'job_dispatches', 'job_submission_keys'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 2)

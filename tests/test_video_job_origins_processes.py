"""独立进程的提交退出、事务退出及账户恢复，不重提已发生的尝试。"""
from contextlib import closing
import multiprocessing
import os
import unittest
from unittest.mock import patch

from video_job_origin_support import VideoOriginFixture
from test_assistant_action_processes import _service
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import get_job
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin


def child_runtime(path):
    _service(path)
    import angemedia_gateway.config as C
    helper = VideoOriginFixture(); helper.calls = []; helper.output = C.OUTPUT_DIR
    return helper, helper.video_runtime()


def exit_child(path, job_id, boundary):
    helper, runtime = child_runtime(path)
    from angemedia_gateway.services import video_job_worker
    original = video_job_worker.bind_video_job_origin
    def terminate(*args):
        original(*args)
        os._exit(23)
    if boundary == 'before_commit':
        with patch.object(video_job_worker, 'bind_video_job_origin', side_effect=terminate):
            runtime.handle(helper.message(job_id))
    if boundary == 'after_response':
        worker = runtime.registry.get('video_submit').__self__
        with patch.object(worker, '_schedule', side_effect=lambda *args, **kwargs: os._exit(25)):
            runtime.handle(helper.message(job_id))
    runtime.handle(helper.message(job_id)); os._exit(24)


def continue_child(path, job_id, steps, output):
    helper, runtime = child_runtime(path)
    states = [runtime.handle(helper.message(job_id))['status'] for _ in range(steps)]
    output.put((states, helper.calls))


class VideoJobOriginProcessesTest(VideoOriginFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.context = multiprocessing.get_context('spawn'); self.children = []; self.queues = []

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive(): child.terminate(); child.join(5)
        for queue in self.queues: queue.close(); queue.join_thread()
        super().tearDown()

    def spawn(self, target, *args):
        child = self.context.Process(target=target, args=(str(self.service.db_path), *args))
        self.children.append(child); child.start(); return child

    def test_exit_before_source_bind_commit_retains_attempt_but_not_partial_task_binding(self):
        job = self.ordinary_job(); child = self.spawn(exit_child, job['id'], 'before_commit')
        child.join(30); self.assertEqual(child.exitcode, 23)
        self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])
        self.assertIsNone(get_job(job['id'])['external_task_id'])
        self.assertEqual(self.video_runtime().handle(self.message(job['id']))['status'], 'duplicate')
        self.assertEqual(self.calls, [])
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM job_dispatches WHERE job_id=?', (job['id'],)).fetchone()[0], 1)

    def test_exit_after_provider_response_never_guesses_task_id_or_resubmits(self):
        job = self.ordinary_job(); child = self.spawn(exit_child, job['id'], 'after_response')
        child.join(30); self.assertEqual(child.exitcode, 25)
        self.assertIsNone(get_video_job_origin(job['id'])['external_task_digest'])
        self.assertIsNone(get_job(job['id'])['external_task_id'])
        self.assertEqual(self.video_runtime().handle(self.message(job['id']))['status'], 'duplicate')
        self.assertEqual(self.calls, [])

    def test_exit_after_commit_and_cold_connection_restore_continue_same_task_without_submit(self):
        job = self.ordinary_job(); child = self.spawn(exit_child, job['id'], 'after_commit')
        child.join(30); self.assertEqual(child.exitcode, 24)
        origin = get_video_job_origin(job['id'])
        self.assertIsNotNone(origin['external_task_digest'])
        update_provider_runtime_config('agnes_video', api_key='cold-process-other-account')
        output = self.context.Queue(); self.queues.append(output)
        child = self.spawn(continue_child, job['id'], 1, output)
        states, calls = output.get(timeout=30); child.join(10); self.assertEqual(child.exitcode, 0)
        self.assertEqual(states, ['scheduled']); self.assertEqual(calls, [])
        update_provider_runtime_config('agnes_video', api_key=None)
        child = self.spawn(continue_child, job['id'], 2, output)
        states, calls = output.get(timeout=30); child.join(10); self.assertEqual(child.exitcode, 0)
        self.assertEqual(states, ['scheduled', 'succeeded'])
        self.assertEqual([row[0] for row in calls], ['poll', 'poll', 'import'])
        self.assertEqual(get_video_job_origin(job['id']), origin)

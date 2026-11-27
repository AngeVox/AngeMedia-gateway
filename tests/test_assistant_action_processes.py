"""跨进程确认及提交前/后退出；正式动作事务必须与任务和收据一起恢复。"""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from assistant_action_support import ActionFixture
from angemedia_gateway.assistant_action_contract import ActionConfirmation
from angemedia_gateway.assistant_run_access import RunAccess
from angemedia_gateway.services.assistant_action_service import AssistantActionService


def _service(path):
    import angemedia_gateway.config as C
    C.DB_FILE = Path(path)
    C.OUTPUT_DIR = C.DB_FILE.parent / 'generated'
    C.UPLOAD_DIR = C.DB_FILE.parent / 'uploads'
    C.SILICONFLOW_API_KEY = 'fixture-siliconflow-credential-123456789'
    C.AGNES_API_KEY = 'fixture-agnes-credential-123456789'
    return AssistantActionService(db_path=C.DB_FILE, clock=lambda: 1800000000000, ttl_ms=1000)


def _race(path, action, session_hash, barrier, output):
    service = _service(path)
    confirmation = ActionConfirmation(action['version'], action['binding_digest'], frozenset(action['review']['required_acknowledgements']))
    barrier.wait(20)
    result = service.confirm(action['id'], confirmation, access=RunAccess('alice', session_hash))
    output.put((result['action']['job']['id'], result['created']))


def _crash(path, action, session_hash, boundary):
    from angemedia_gateway.repositories import assistant_actions
    service = _service(path)
    confirmation = ActionConfirmation(action['version'], action['binding_digest'], frozenset(action['review']['required_acknowledgements']))
    if boundary == 'before_commit':
        submit = assistant_actions.submit
        def terminate(*args):
            submit(*args)
            os._exit(23)
        with patch.object(assistant_actions, 'submit', side_effect=terminate):
            service.confirm(action['id'], confirmation, access=RunAccess('alice', session_hash))
    service.confirm(action['id'], confirmation, access=RunAccess('alice', session_hash))
    os._exit(24)


class ActionProcessesTest(ActionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.context = multiprocessing.get_context('spawn')
        self.children, self.queues = [], []

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive(): child.terminate(); child.join(5)
        for queue in self.queues:
            queue.close(); queue.join_thread()
        super().tearDown()

    def spawn(self, function, *args):
        child = self.context.Process(target=function, args=(str(self.service.db_path), *args))
        self.children.append(child); child.start()
        return child

    def test_four_process_confirmations_produce_one_receipt_job_and_outbox(self):
        action = self.propose()
        barrier, output = self.context.Barrier(4), self.context.Queue()
        self.queues.append(output)
        children = [self.spawn(_race, action, self.alice.session_hash, barrier, output) for _ in range(4)]
        results = [output.get(timeout=30) for _ in children]
        for child in children:
            child.join(10); self.assertEqual(child.exitcode, 0)
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({job_id for job_id, _ in results}), 1)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_process_exit_before_commit_or_reply_recovers_same_confirmed_effect(self):
        for boundary, expected_exit in [('before_commit', 23), ('after_commit', 24)]:
            with self.subTest(boundary=boundary):
                action = self.propose()
                child = self.spawn(_crash, action, self.alice.session_hash, boundary)
                child.join(30); self.assertEqual(child.exitcode, expected_exit)
                result = self.confirm(action)
                self.assertEqual(result['created'], boundary == 'before_commit')
                replay = self.confirm(action)
                self.assertFalse(replay['created'])
                self.assertEqual(replay['action']['job']['id'], result['action']['job']['id'])
        self.assertEqual(self.counts(), (2, 2, 2))


if __name__ == '__main__': unittest.main()

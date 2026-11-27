"""不同进程间的初始化、会话互斥、接管隔离和事件顺序验收。"""
from __future__ import annotations

import multiprocessing
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session, list_assistant_messages
from angemedia_gateway.assistant_runtime_contract import RunBusy, RunLeaseLost


def _contest(path, session, run_id, ready, release, output):
    C.DB_FILE = Path(path)
    ready.wait(15)
    repo = AssistantRunRepository()
    try:
        lease = repo.claim(run_id)
    except RunBusy:
        output.put('busy')
    else:
        output.put('claimed')
        release.wait(15)
        repo.publish_message(lease, 'user', '独占持有者消息')
        repo.finish(lease, answer='完成')


def _initialize_and_admit(path, session, ready, output, new_session=False):
    C.DB_FILE = Path(path)
    ready.wait(15)
    init_db()
    if new_session:
        create_assistant_session(session, "新库会话")
    run = AssistantRunRepository().admit(session, uuid4().hex, {'message': '新请求'})
    output.put(run.run['id'])


def _stale_writer(path, run_id, ready, replaced, output):
    C.DB_FILE = Path(path)
    repo = AssistantRunRepository(clock=lambda: 1000)
    old = repo.claim(run_id, lease_ms=100)
    ready.set()
    replaced.wait(15)
    try:
        repo.publish_message(old, 'assistant', '旧进程迟到结果')
        output.put('unexpected-write')
    except RunLeaseLost:
        output.put('fenced')


def _append_events(path, lease, ready, output):
    C.DB_FILE = Path(path)
    ready.wait(15)
    repo = AssistantRunRepository()
    for i in range(8):
        repo.append_event(lease, 'tool_progress', {'progress': i})
    output.put('done')


def _claim_and_exit(path, run_id, ready):
    C.DB_FILE = Path(path)
    AssistantRunRepository(clock=lambda: 1000).claim(run_id, lease_ms=100)
    ready.set()
    os._exit(23)


class AssistantRuntimeProcessesTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='assistant-runtime-processes-'))
        self.original = C.DB_FILE
        C.DB_FILE = self.temp / 'state.db'
        init_db()
        self.session = uuid4().hex
        create_assistant_session(self.session, '跨进程会话')
        self.ctx = multiprocessing.get_context('spawn')
        self.children = []
        self.queues = []

    def tearDown(self):
        for child in self.children:
            child.join(2)
            if child.is_alive():
                child.terminate()
                child.join(5)
        for queue in self.queues:
            queue.close()
            queue.join_thread()
        C.DB_FILE = self.original
        assert self.temp.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) and self.temp.name.startswith('assistant-runtime-processes-')
        shutil.rmtree(self.temp)

    def queue(self):
        queue = self.ctx.Queue()
        self.queues.append(queue)
        return queue

    def spawn(self, target, *args):
        child = self.ctx.Process(target=target, args=(str(C.DB_FILE), *args))
        self.children.append(child)
        child.start()

    def joined(self):
        for child in self.children:
            child.join(20)
            self.assertEqual(child.exitcode, 0)

    def test_four_process_claim_has_one_owner_and_one_pair_of_messages(self):
        repo = AssistantRunRepository()
        run_id = repo.admit(self.session, uuid4().hex, {}).run['id']
        ready, release, output = self.ctx.Event(), self.ctx.Event(), self.queue()
        for _ in range(4):
            self.spawn(_contest, self.session, run_id, ready, release, output)
        ready.set()
        outcomes = [output.get(timeout=20) for _ in range(4)]
        self.assertEqual(outcomes.count('claimed'), 1)
        self.assertEqual(outcomes.count('busy'), 3)
        release.set()
        self.joined()
        self.assertEqual(repo.get(run_id)['status'], 'succeeded')
        self.assertEqual(len(list_assistant_messages(self.session)), 2)

    def test_parallel_upgrade_rechecks_migration_and_preserves_all_admissions(self):
        # 模拟旧库尚未执行新增迁移，多个启动进程同时升级并创建运行。
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(C.DB_FILE, isolation_level=None)) as conn:
            conn.execute('DROP TABLE assistant_session_leases')
            conn.execute('DROP TABLE assistant_run_events')
            conn.execute('DROP TABLE assistant_runs')
            conn.execute("DELETE FROM schema_migrations WHERE version='assistant_runtime_v1'")
            conn.execute("CREATE TABLE assistant_runs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('succeeded','failed','refused')),skill_id TEXT,input_json TEXT NOT NULL,output_json TEXT NOT NULL,timeline_json TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT NOT NULL)")
        ready, output = self.ctx.Event(), self.queue()
        for _ in range(4):
            self.spawn(_initialize_and_admit, self.session, ready, output)
        ready.set()
        run_ids = [output.get(timeout=25) for _ in range(4)]
        self.joined()
        self.assertEqual(len(set(run_ids)), 4)
        for run_id in run_ids:
            self.assertEqual(AssistantRunRepository().get(run_id)['status'], 'queued')

    def test_dead_process_replaced_lease_rejects_late_write_even_with_old_clock(self):
        repo = AssistantRunRepository(clock=lambda: 1000)
        run_id = repo.admit(self.session, uuid4().hex, {}).run['id']
        ready, replaced, output = self.ctx.Event(), self.ctx.Event(), self.queue()
        self.spawn(_stale_writer, run_id, ready, replaced, output)
        self.assertTrue(ready.wait(15))
        current = AssistantRunRepository(clock=lambda: 1100)
        lease = current.claim(run_id)
        replaced.set()
        self.assertEqual(output.get(timeout=15), 'fenced')
        self.joined()
        current.finish(lease, answer='新进程结果')
        messages = list_assistant_messages(self.session)
        self.assertEqual([m['content'] for m in messages], ['新进程结果'])

    def test_fresh_database_bootstrap_is_serialized_across_processes(self):
        C.DB_FILE = self.temp / 'fresh.db'
        ready, output = self.ctx.Event(), self.queue()
        for _ in range(6):
            self.spawn(_initialize_and_admit, uuid4().hex, ready, output, True)
        ready.set()
        ids = [output.get(timeout=25) for _ in range(6)]
        self.joined()
        self.assertEqual(len(set(ids)), 6)
        for run_id in ids:
            self.assertEqual(AssistantRunRepository().get(run_id)['status'], 'queued')

    def test_owner_process_exit_preserves_run_for_fenced_takeover(self):
        repo = AssistantRunRepository(clock=lambda: 1000)
        run_id = repo.admit(self.session, uuid4().hex, {}).run['id']
        ready = self.ctx.Event()
        self.spawn(_claim_and_exit, run_id, ready)
        self.assertTrue(ready.wait(15))
        self.children[0].join(15)
        self.assertEqual(self.children[0].exitcode, 23)
        current = AssistantRunRepository(clock=lambda: 1100)
        lease = current.claim(run_id)
        current.finish(lease, answer='恢复后完成')
        self.assertEqual(current.get(run_id)['status'], 'succeeded')

    def test_parallel_events_have_no_duplicate_or_missing_sequence(self):
        repo = AssistantRunRepository()
        run_id = repo.admit(self.session, uuid4().hex, {}).run['id']
        lease = repo.claim(run_id)
        ready, output = self.ctx.Event(), self.queue()
        for _ in range(4):
            self.spawn(_append_events, lease, ready, output)
        ready.set()
        self.assertEqual([output.get(timeout=20) for _ in range(4)], ['done']*4)
        self.joined()
        events = repo.events(run_id)
        self.assertEqual(len(events), 34)
        self.assertEqual([item['sequence'] for item in events], list(range(1, 35)))


if __name__ == '__main__':
    unittest.main()

"""运行迁移、顺序、租约失效和持久终态的可观察合同。"""
from __future__ import annotations

import json
from contextlib import closing
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.assistant_runtime_contract import RunBudgetExceeded, RunBusy, RunClosed, RunLeaseLost, RunNotFound, RunRequestConflict
from angemedia_gateway.assistant_sanitizer import runtime_payload
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import add_assistant_message, add_assistant_run, create_assistant_session, delete_assistant_session, list_assistant_messages


class AssistantRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="assistant-runtime-"))
        self.original = C.DB_FILE
        C.DB_FILE = self.temp / 'state.db'
        init_db()
        self.session = uuid4().hex
        create_assistant_session(self.session, '会话')
        self.now = 1000
        self.repo = AssistantRunRepository(clock=lambda: self.now)

    def tearDown(self):
        C.DB_FILE = self.original
        assert self.temp.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()) and self.temp.name.startswith("assistant-runtime-")
        shutil.rmtree(self.temp)

    def admit(self, **kwargs):
        return self.repo.admit(self.session, uuid4().hex, {'message': '保持蓝色'}, **kwargs).run['id']

    def test_request_retry_reuses_even_terminal_run_and_rejects_changed_intent(self):
        key = uuid4().hex
        first = self.repo.admit(self.session, key, {'a': 1, 'b': 2})
        repeat = self.repo.admit(self.session, key, {'b': 2, 'a': 1})
        self.assertFalse(repeat.created)
        self.assertEqual(first.run['id'], repeat.run['id'])
        for fields in ({'input_payload': {'a': 9}}, {'input_payload': {'a': 1, 'b': 2}, 'max_model_calls': 9}):
            with self.subTest(fields=fields), self.assertRaises(RunRequestConflict):
                self.repo.admit(self.session, key, **fields)
        lease = self.repo.claim(first.run['id'])
        self.repo.finish(lease, answer='完成', output={'result': 'ok'})
        replay = self.repo.admit(self.session, key, {'a': 1, 'b': 2})
        self.assertFalse(replay.created)
        self.assertEqual(replay.run['status'], 'succeeded')
        with self.assertRaises(RunClosed):
            self.repo.claim(first.run['id'])

    def test_queue_is_fifo_and_paused_head_must_finish_before_next_turn(self):
        first, second = self.admit(), self.admit()
        with self.assertRaises(RunBusy):
            self.repo.claim(second)
        lease = self.repo.claim(first)
        self.repo.pause(lease)
        with self.assertRaises(RunBusy):
            self.repo.claim(second)
        resumed = self.repo.claim(first)
        self.assertGreater(resumed.fence, lease.fence)
        self.repo.finish(resumed)
        next_lease = self.repo.claim(second)
        self.assertGreater(next_lease.fence, resumed.fence)

    def test_expired_owner_cannot_write_messages_events_budget_or_terminal_state(self):
        run_id = self.admit()
        old = self.repo.claim(run_id, lease_ms=100)
        self.now = 1100
        new = self.repo.claim(run_id)
        self.assertGreater(new.fence, old.fence)
        before = self.repo.get(run_id)
        operations = [lambda: self.repo.publish_message(old, 'assistant', '迟到的答复'),
                      lambda: self.repo.append_event(old, 'tool_finished'), lambda: self.repo.reserve_call(old, 'model'),
                      lambda: self.repo.renew(old), lambda: self.repo.pause(old), lambda: self.repo.finish(old)]
        for operation in operations:
            with self.assertRaises(RunLeaseLost):
                operation()
        self.assertEqual(before, self.repo.get(run_id))
        self.assertEqual(list_assistant_messages(self.session), [])
        self.repo.finish(new, answer='当前持有者答复')
        self.assertEqual(len(list_assistant_messages(self.session)), 1)
        self.assertNotIn(old.owner_token, repr(old))

    def test_renew_does_not_resurrect_expired_lease_or_extend_overall_deadline(self):
        run_id = self.admit(timeout_ms=200)
        lease = self.repo.claim(run_id, lease_ms=100)
        self.now = 1050
        self.repo.renew(lease, lease_ms=300)
        with closing(sqlite3.connect(C.DB_FILE)) as conn:
            self.assertEqual(conn.execute('SELECT expires_ms FROM assistant_session_leases').fetchone()[0], 1200)
        self.now = 1200
        with self.assertRaises(RunLeaseLost):
            self.repo.renew(lease)
        self.assertEqual(self.repo.expire_due(), 1)
        self.assertEqual(self.repo.get(run_id)['status'], 'timed_out')
        self.assertEqual(self.repo.expire_due(), 0)

    def test_expired_queued_turn_is_explicitly_reaped_before_next_turn(self):
        first = self.admit(timeout_ms=10)
        second = self.admit()
        self.now += 10
        with self.assertRaises(RunBusy):
            self.repo.claim(second)
        self.assertEqual(self.repo.expire_due(session_id=self.session), 1)
        self.assertEqual(self.repo.get(first)['status'], 'timed_out')
        self.repo.claim(second)

    def test_recovery_discovers_only_unleased_session_heads(self):
        first, second = self.admit(), self.admit()
        self.assertEqual([r['id'] for r in self.repo.ready()], [first])
        lease = self.repo.claim(first, lease_ms=100)
        self.assertEqual(self.repo.ready(), [])
        self.now += 100
        self.assertEqual([r['id'] for r in self.repo.ready()], [first])
        replacement = self.repo.claim(first)
        self.repo.finish(replacement)
        self.assertEqual([r['id'] for r in self.repo.ready()], [second])

    def test_budget_is_reserved_before_call_and_exhaustion_survives_exception(self):
        for kind in ['model', 'tool']:
            with self.subTest(kind=kind):
                run_id = self.admit(max_model_calls=1, max_tool_calls=1)
                lease = self.repo.claim(run_id)
                self.repo.reserve_call(lease, kind)
                with self.assertRaises(RunBudgetExceeded):
                    self.repo.reserve_call(lease, kind)
                run = self.repo.get(run_id)
                self.assertEqual(run['status'], 'budget_exceeded')
                self.assertEqual(run[kind + '_calls'], 1)
                self.assertEqual(self.repo.events(run_id)[-1]['event_type'], 'budget_exceeded')
                with self.assertRaises(RunLeaseLost):
                    self.repo.publish_message(lease, 'assistant', '不能再回答')

    def test_cancel_queued_running_paused_and_terminal_replay(self):
        for status in ['queued', 'running', 'paused']:
            with self.subTest(status=status):
                run_id = self.admit()
                lease = None
                if status != 'queued':
                    lease = self.repo.claim(run_id)
                    if status == 'paused':
                        self.repo.pause(lease)
                canceled = self.repo.cancel(run_id)
                self.assertEqual(canceled['status'], 'canceled')
                self.assertIsNotNone(canceled['cancel_requested_at'])
                self.assertEqual(canceled, self.repo.cancel(run_id))
                if lease:
                    with self.assertRaises(RunLeaseLost):
                        self.repo.finish(lease)

    def test_messages_are_idempotent_and_final_answer_commits_with_run(self):
        run_id = self.admit()
        lease = self.repo.claim(run_id)
        first = self.repo.publish_message(lease, 'user', '请保持蓝色')
        self.assertEqual(self.repo.publish_message(lease, 'user', '请保持蓝色')['id'], first['id'])
        with self.assertRaises(RunRequestConflict):
            self.repo.publish_message(lease, 'user', '改成红色')
        before = self.repo.get(run_id)
        import angemedia_gateway.repositories.assistant_runtime as runtime_module
        original_event = runtime_module._event
        def event_failure(conn, run_id, event_type, payload=None):
            if event_type == 'succeeded':
                raise RuntimeError('模拟终态事件落盘失败')
            return original_event(conn, run_id, event_type, payload)
        with patch.object(runtime_module, '_event', side_effect=event_failure):
            with self.assertRaises(RuntimeError):
                self.repo.finish(lease, answer='完整答复')
        self.assertEqual(self.repo.get(run_id), before)
        self.assertEqual(len(list_assistant_messages(self.session)), 1)
        self.repo.finish(lease, answer='完整答复')
        self.assertEqual(len(list_assistant_messages(self.session)), 2)

    def test_events_have_durable_cursor_and_redact_control_credentials(self):
        run_id = self.admit()
        lease = self.repo.claim(run_id)
        self.repo.append_event(lease, 'tool_finished', {'owner_token': lease.owner_token, 'api_key': 'sk-private-123456789', 'text': '日志 /root/private/file.txt', 'url': 'https://example.com/x?token=private'})
        events = self.repo.events(run_id)
        self.assertEqual([e['sequence'] for e in events], list(range(1, len(events)+1)))
        self.assertEqual(self.repo.events(run_id, after=events[-2]['sequence']), events[-1:])
        text = json.dumps(events)
        for marker in [lease.owner_token, 'sk-private', '/root/private', 'token=private']:
            self.assertNotIn(marker, text)
        for name in ['succeeded', 'started', 'admitted']:
            with self.assertRaises(ValueError):
                self.repo.append_event(lease, name)

    def test_archive_revokes_lease_and_all_write_paths_reject_late_results(self):
        run_id = self.admit()
        lease = self.repo.claim(run_id)
        self.assertTrue(delete_assistant_session(self.session))
        with self.assertRaises(RunNotFound):
            self.repo.finish(lease)
        for action in [lambda: add_assistant_message(uuid4().hex, self.session, 'assistant', '迟到消息'),
                       lambda: add_assistant_run(uuid4().hex, self.session, 'succeeded', None, {}, {}, []),
                       lambda: self.admit()]:
            with self.assertRaises(ValueError):
                action()
        with closing(sqlite3.connect(C.DB_FILE)) as conn:
            for table in ['assistant_messages', 'assistant_runs', 'assistant_run_events', 'assistant_session_leases']:
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_invalid_budgets_payloads_and_legacy_ids_cannot_create_controlled_run(self):
        for fields in [{'timeout_ms': 0}, {'timeout_ms': True}, {'max_model_calls': 0}, {'max_tool_calls': -1}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.admit(**fields)
        with self.assertRaises(ValueError):
            self.repo.admit(self.session, 'not-a-request-id', {})
        for value in [{'data': '字'*20000}, {'data': [0]*1001}, {'data': float('nan')}, {'lease': object()}]:
            with self.assertRaises(ValueError):
                runtime_payload(value)
        legacy = add_assistant_run(uuid4().hex, self.session, 'refused', None, {}, {}, [])
        with self.assertRaises(RunNotFound):
            self.repo.claim(legacy['id'])

    def _legacy_database(self):
        path = self.temp / 'legacy.db'
        with closing(sqlite3.connect(path, isolation_level=None)) as conn:
            conn.executescript("""
                CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE assistant_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                INSERT INTO assistant_sessions VALUES('legacy','旧会话','active','old','old');
                CREATE TABLE assistant_runs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('succeeded','failed','refused')),
                    skill_id TEXT,input_json TEXT NOT NULL,output_json TEXT NOT NULL,timeline_json TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES assistant_sessions(id) ON DELETE CASCADE);
            """)
            for status in ['succeeded', 'failed', 'refused']:
                conn.execute('INSERT INTO assistant_runs VALUES(?,?,?,?,?,?,?,?,?)',
                             (status, 'legacy', status, 'skill', '{"原文":1}', '{"result":2}', '[{"工具":3}]', 'created', 'completed'))
        return path

    def test_legacy_rows_survive_upgrade_and_repeated_initialization(self):
        legacy = self._legacy_database()
        C.DB_FILE = legacy
        self.repo = AssistantRunRepository(clock=lambda: self.now)
        init_db()
        init_db()
        with closing(sqlite3.connect(legacy)) as conn:
            rows = conn.execute('SELECT id,session_id,status,skill_id,input_json,output_json,timeline_json,created_at,completed_at FROM assistant_runs ORDER BY id').fetchall()
            expected = [(s, 'legacy', s, 'skill', '{"原文":1}', '{"result":2}', '[{"工具":3}]', 'created', 'completed') for s in ['failed', 'refused', 'succeeded']]
            self.assertEqual(rows, expected)
            self.assertEqual(conn.execute('SELECT sum(runtime_managed) FROM assistant_runs').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations WHERE version='assistant_runtime_v1'").fetchone()[0], 1)
        self.session = 'legacy'
        self.assertEqual(self.repo.get(self.admit())['status'], 'queued')

    def test_missing_marker_never_rebuilds_existing_managed_data(self):
        run_id = self.admit()
        lease = self.repo.claim(run_id)
        with closing(sqlite3.connect(C.DB_FILE, isolation_level=None)) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version='assistant_runtime_v1'")
        with self.assertRaises(RuntimeError):
            init_db()
        self.assertEqual(self.repo.get(run_id)['status'], 'running')
        self.repo.finish(lease, answer='保留运行')

    def test_failed_rebuild_rolls_back_all_schema_and_legacy_records(self):
        import angemedia_gateway.db.migrations as migrations
        legacy = self._legacy_database()
        C.DB_FILE = legacy
        self.repo = AssistantRunRepository(clock=lambda: self.now)
        original = dict(migrations.MIGRATIONS)['assistant_runtime_v1']
        def fail(conn):
            original(conn)
            raise RuntimeError('迁移故障注入')
        injected = tuple((version, fail if version == 'assistant_runtime_v1' else migration)
                         for version, migration in migrations.MIGRATIONS)
        with patch.object(migrations, 'MIGRATIONS', injected):
            with self.assertRaises(RuntimeError):
                init_db()
        with closing(sqlite3.connect(legacy)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_runs').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            self.assertNotIn('runtime_managed', [row[1] for row in conn.execute('PRAGMA table_info(assistant_runs)')])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        init_db()
        self.assertEqual(self.repo.get(self.repo.admit('legacy', uuid4().hex, {}).run['id'])['status'], 'queued')


if __name__ == '__main__':
    unittest.main()

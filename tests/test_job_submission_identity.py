"""真实SQLite验证持久提交、原子授权事务、清理后防重放与迁移回滚。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_run_support import RunFixture
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.db import migrations
from angemedia_gateway.job_submission import JobSubmissionConflict, JobSubmissionGone, JobSubmissionKey, PreparedJobAdmission
from angemedia_gateway.repositories.jobs import cleanup_jobs, get_job, update_job_status
from angemedia_gateway.repositories.assistant_conversations import AssistantConversationRepository
from angemedia_gateway.services.job_admission import JobAdmissionService
from angemedia_gateway.assistant_run_access import authorize_run_access
from angemedia_gateway.repositories.assistant_conversations import ConversationNotFound


class JobSubmissionIdentityTest(RunFixture, unittest.TestCase):
    def key(self, namespace='assistant_action'):
        return JobSubmissionKey(namespace, uuid4().hex)

    def admit(self, key=None, **overrides):
        arguments = dict(kind='image', stage='image_generate', request_hash='a'*64, request_hash_version=2,
                         payload={'request': {'prompt': '蓝色山间云雾'}, 'route': {'provider': 'fixture'}},
                         prompt='蓝色山间云雾', provider='fixture', model='fixture-model', submission_key=key)
        arguments.update(overrides)
        return JobAdmissionService().admit(**arguments)

    def counts(self):
        with closing(db_connect()) as conn:
            return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                         for table in ('jobs', 'job_events', 'job_dispatches', 'job_submission_keys'))

    def test_same_key_returns_original_job_for_all_terminal_states(self):
        for terminal in ('succeeded', 'failed', 'canceled'):
            with self.subTest(status=terminal):
                key = self.key(); first = self.admit(key)
                self.assertTrue(first.created)
                update_job_status(first.job['id'], status='running')
                update_job_status(first.job['id'], status=terminal)
                before = self.counts()
                replay = self.admit(key)
                self.assertFalse(replay.created); self.assertIsNone(replay.dispatch)
                self.assertEqual((replay.job['id'], replay.job['status']), (first.job['id'], terminal))
                self.assertEqual(self.counts(), before)

    def test_new_keys_can_intentionally_generate_identical_content_while_old_api_still_dedupes(self):
        first, second = self.admit(self.key()), self.admit(self.key())
        self.assertNotEqual(first.job['id'], second.job['id'])
        self.assertEqual(self.counts(), (2, 2, 2, 2))
        plain = self.admit()
        self.assertTrue(plain.created)
        self.assertFalse(self.admit().created)
        self.assertEqual(self.counts(), (3, 3, 3, 2))

    def test_changed_intent_never_mutates_or_rebinds_consumed_key(self):
        key = self.key(); original = self.admit(key); before = self.counts()
        for changes in ({'prompt': '红色'}, {'model': 'another'}, {'provider': 'another'}, {'priority': 1},
                        {'max_attempts': 1}, {'scheduled_at': '2099-01-01'}, {'payload_schema_version': 2},
                        {'request_hash': 'b'*64}, {'request_hash_version': 1},
                        {'kind': 'video', 'stage': 'video_submit'}, {'payload': {'request': {'prompt': '红色'}}}):
            with self.subTest(changes=list(changes)), self.assertRaises(JobSubmissionConflict): self.admit(key, **changes)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.admit(key).job['id'], original.job['id'])

    def test_fingerprint_is_order_independent_but_raw_secret_changes_do_not_collapse(self):
        key = self.key()
        first = self.admit(key, payload={'a': 1, 'b': {'first': '月光', 'last': 2}})
        self.assertEqual(self.admit(key, payload={'b': {'last': 2, 'first': '月光'}, 'a': 1}).job['id'], first.job['id'])
        sensitive_key = self.key()
        self.admit(sensitive_key, payload={'api_key': 'first-private-credential'})
        with self.assertRaises(JobSubmissionConflict): self.admit(sensitive_key, payload={'api_key': 'second-private-credential'})
        with closing(db_connect()) as conn:
            ledger = [dict(row) for row in conn.execute('SELECT * FROM job_submission_keys')]
        for private in ('first-private-credential', 'second-private-credential', '蓝色山间云雾', 'api_key'):
            self.assertNotIn(private, str(ledger))

    def test_separate_namespaces_do_not_share_identity_and_uuid_spelling_is_canonical(self):
        value = uuid4(); key = JobSubmissionKey('assistant_action', str(value).upper())
        first = self.admit(key)
        self.assertEqual(self.admit(JobSubmissionKey('assistant_action', value.hex)).job['id'], first.job['id'])
        self.assertNotEqual(self.admit(JobSubmissionKey('studio_action', value.hex)).job['id'], first.job['id'])
        for namespace, value in (('', uuid4().hex), ('../model', uuid4().hex), ('A', uuid4().hex), ('assistant_action', 'not-a-uuid')):
            with self.subTest(namespace=namespace), self.assertRaises(ValueError): JobSubmissionKey(namespace, value)

    def test_atomic_with_outer_authorization_and_mapping_transaction(self):
        key = self.key()
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with db_transaction(immediate=True) as conn:
                authorize_run_access(conn, self.access['alice'], self.session)
                admitted = self.admit(key, conn=conn)
                conn.execute("INSERT INTO config(key,value,updated_at) VALUES('fixture_action_mapping',?,'now')", (admitted.job['id'],))
                raise RuntimeError('rollback')
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        with closing(db_connect()) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM config WHERE key='fixture_action_mapping'").fetchone())
        self.assertTrue(self.admit(key).created)

    def test_submission_key_is_not_authorization_and_cannot_bypass_outer_owner_guard(self):
        with self.assertRaises(ConversationNotFound):
            with db_transaction(immediate=True) as conn:
                authorize_run_access(conn, self.access['bob'], self.session)
                self.admit(self.key(), conn=conn)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_local_failure_rollback_remains_atomic_even_if_outer_caller_handles_error(self):
        key = self.key()
        with db_transaction(immediate=True) as conn:
            conn.execute("INSERT INTO config VALUES('outer_marker','keep','now')")
            conn.execute("CREATE TEMP TRIGGER reject_binding BEFORE INSERT ON job_submission_keys BEGIN SELECT RAISE(ABORT,'reject'); END")
            with self.assertRaises(sqlite3.IntegrityError): self.admit(key, conn=conn)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT value FROM config WHERE key='outer_marker'").fetchone()[0], 'keep')

    def test_outbox_failure_rolls_back_identity_job_and_event(self):
        with closing(db_connect()) as conn:
            conn.execute("CREATE TRIGGER reject_dispatch BEFORE INSERT ON job_dispatches BEGIN SELECT RAISE(ABORT,'reject'); END")
        key = self.key()
        with self.assertRaises(sqlite3.IntegrityError): self.admit(key)
        self.assertEqual(self.counts(), (0, 0, 0, 0))
        with closing(db_connect()) as conn: conn.execute('DROP TRIGGER reject_dispatch')
        self.assertTrue(self.admit(key).created)

    def test_caller_cannot_supply_autocommit_or_cross_readonly_transaction(self):
        from angemedia_gateway.db.readonly_scope import readonly_database
        with closing(db_connect()) as conn, self.assertRaises(ValueError): self.admit(self.key(), conn=conn)
        with readonly_database(self.repo.db_path), self.assertRaises(sqlite3.OperationalError): self.admit(self.key())
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_simultaneous_threads_admit_one_job_and_changed_intent_loses_atomically(self):
        key = self.key()
        with ThreadPoolExecutor(8) as pool: results = list(pool.map(lambda _: self.admit(key), range(16)))
        self.assertEqual(sum(result.created for result in results), 1)
        self.assertEqual(len({result.job['id'] for result in results}), 1)
        self.assertEqual(self.counts(), (1, 1, 1, 1))

    def test_archival_and_real_job_cleanup_do_not_make_old_key_reusable(self):
        run = self.repo.admit(self.session, uuid4().hex, {'message': '不会复活'})
        key = JobSubmissionKey('assistant_action', run.run['id'])
        first = self.admit(key)
        update_job_status(first.job['id'], status='running')
        update_job_status(first.job['id'], status='succeeded')
        AssistantConversationRepository().archive(self.session, username='alice')
        self.assertEqual(self.admit(key).job['id'], first.job['id'])
        cleanup_jobs(job_ids=[first.job['id']], statuses=['succeeded'])
        self.assertIsNone(get_job(first.job['id']))
        before = self.counts()
        with self.assertRaises(JobSubmissionGone) as error: self.admit(key)
        self.assertEqual(error.exception.job_id, first.job['id']); self.assertEqual(self.counts(), before)
        self.assertEqual(before[-1], 1)

    def test_key_mapping_is_immutable_and_initialization_is_repeatable(self):
        key = self.key(); first = self.admit(key)
        init_db(); init_db()
        with closing(db_connect()) as conn:
            with self.assertRaises(sqlite3.IntegrityError): conn.execute('UPDATE job_submission_keys SET job_id=?', (uuid4().hex,))
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations WHERE version='job_submission_identity_v1'").fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.admit(key).job['id'], first.job['id'])

    def test_retention_cleanup_preserves_the_small_submission_tombstone(self):
        from angemedia_gateway.services.maintenance_retention import retention_cleanup
        key = self.key(); result = self.admit(key)
        update_job_status(result.job['id'], status='failed')
        with closing(db_connect()) as conn:
            conn.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (result.job['id'],))
            conn.execute("UPDATE assistant_sessions SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (self.session,))
        retention_cleanup({'confirm': 'CLEAN_OLD_RECORDS', 'older_than_days': 1})
        self.assertIsNone(get_job(result.job['id']))
        with self.assertRaises(JobSubmissionGone): self.admit(key)
        self.assertEqual(self.counts()[-1], 1)

    def test_migration_failure_rolls_back_and_retry_preserves_existing_jobs(self):
        import angemedia_gateway.config as C
        C.DB_FILE = self.temp / 'legacy.db'
        prior = tuple((version, fn) for version, fn in migrations.MIGRATIONS if version != 'job_submission_identity_v1')
        with patch.object(migrations, 'MIGRATIONS', prior): init_db()
        job_id = uuid4().hex
        original = dict(migrations.MIGRATIONS)['job_submission_identity_v1']
        with closing(db_connect()) as conn:
            conn.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES(?,'image','queued','old','old')", (job_id,))
            original_index = conn.execute("SELECT sql FROM sqlite_master WHERE name='uq_jobs_active_request_hash'").fetchone()[0]
        def fail(conn): original(conn); raise RuntimeError('injected migration failure')
        injected = tuple((version, fail if version == 'job_submission_identity_v1' else fn) for version, fn in migrations.MIGRATIONS)
        with patch.object(migrations, 'MIGRATIONS', injected), self.assertRaises(RuntimeError): init_db()
        with closing(db_connect()) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='job_submission_keys'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM schema_migrations WHERE version='job_submission_identity_v1'").fetchone())
            self.assertNotIn('dedupe_scope', [row[1] for row in conn.execute('PRAGMA table_info(jobs)')])
            self.assertEqual(conn.execute("SELECT sql FROM sqlite_master WHERE name='uq_jobs_active_request_hash'").fetchone()[0], original_index)
        init_db()
        self.assertEqual(get_job(job_id)['status'], 'queued')
        self.assertEqual(get_job(job_id)['dedupe_scope'], 'content')

    def test_partial_migration_waits_for_jobs_and_does_not_record_a_false_success(self):
        with closing(sqlite3.connect(self.temp / 'partial.db', isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            handler = dict(migrations.MIGRATIONS)['job_submission_identity_v1']
            with patch.object(migrations, 'MIGRATIONS', (('job_submission_identity_v1', handler),)):
                migrations.run_migrations(conn)
                self.assertIsNone(conn.execute("SELECT 1 FROM schema_migrations WHERE version='job_submission_identity_v1'").fetchone())
                conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,request_hash TEXT,request_hash_version INTEGER)')
                migrations.run_migrations(conn)
                self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations WHERE version='job_submission_identity_v1'").fetchone()[0], 1)
                self.assertIn('dedupe_scope', [row[1] for row in conn.execute('PRAGMA table_info(jobs)')])

    def test_prepared_snapshot_cannot_be_mutated_or_confused_with_authorization(self):
        arguments = {'payload': {'request': {'prompt': 'blue'}}, 'dedupe_hashes': (('a'*64, 1),)}
        prepared = PreparedJobAdmission.from_arguments(**arguments)
        arguments['payload']['request']['prompt'] = 'red'
        copy = prepared.arguments(); copy['payload']['request']['prompt'] = 'green'
        self.assertEqual(prepared.arguments()['payload']['request']['prompt'], 'blue')
        self.assertEqual(prepared.arguments()['dedupe_hashes'], (('a'*64, 1),))
        self.assertNotIn('blue', repr(prepared))
        with self.assertRaises(FrozenInstanceError): prepared._arguments_json = '{}'
        with self.assertRaises(ValueError): self.admit({'namespace': 'assistant_action', 'value': uuid4().hex})
        with self.assertRaises(ValueError): self.admit(self.key(), payload={'bad': float('nan')})

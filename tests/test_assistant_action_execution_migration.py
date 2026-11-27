"""执行迁移不回填人类授权；密钥和凭据重放、故障回滚均有明确边界。"""
from __future__ import annotations

from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.migrations import run_migrations
from angemedia_gateway.db.assistant_action_execution import migrate_assistant_action_execution


class ActionExecutionMigrationTest(ActionWorkerFixture, unittest.TestCase):
    def test_repeated_migration_preserves_binding_key_grant_and_existing_job(self):
        job = self.job(self.propose())
        with closing(db_connect()) as conn:
            key = conn.execute('SELECT value FROM assistant_action_binding_key').fetchone()[0]
            grant = tuple(conn.execute('SELECT * FROM assistant_action_execution_grants').fetchone())
            run_migrations(conn); run_migrations(conn)
            self.assertEqual(conn.execute('SELECT value FROM assistant_action_binding_key').fetchone()[0], key)
            self.assertEqual(tuple(conn.execute('SELECT * FROM assistant_action_execution_grants').fetchone()), grant)
        self.assertEqual(self.image_runtime().handle(self.message(job['id']))['status'], 'succeeded')

    def test_new_migration_never_backfills_a_grant_for_an_old_submitted_receipt(self):
        job = self.job(self.propose())
        with closing(db_connect()) as conn:
            conn.execute('DROP TABLE assistant_action_execution_grants')
            conn.execute('DROP TABLE assistant_action_binding_key')
            conn.execute("DELETE FROM schema_migrations WHERE version='assistant_action_execution_v1'")
            run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_action_execution_grants').fetchone()[0], 0)
        self.assert_rejected(self.image_runtime(), job)
        self.assertEqual(self.calls, [])

    def test_partial_database_defers_execution_migration(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY)')
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('assistant_action_execution_v1', migrate_assistant_action_execution),)):
                run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='assistant_action_binding_key'").fetchone())

    def test_execution_migration_fault_rolls_back_key_tables_triggers_and_marker(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY)')
            conn.execute('CREATE TABLE assistant_actions(id TEXT PRIMARY KEY,status TEXT,job_id TEXT,version INTEGER)')
            conn.execute('CREATE TABLE assistant_action_revisions(action_id TEXT,version INTEGER,binding_digest TEXT,PRIMARY KEY(action_id,version))')
            def fail(connection):
                migrate_assistant_action_execution(connection)
                raise RuntimeError('fixture-after-grant-migration')
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('assistant_action_execution_v1', fail),)):
                with self.assertRaises(RuntimeError): run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            names = {row[0] for row in conn.execute('SELECT name FROM sqlite_master')}
            self.assertNotIn('assistant_action_binding_key', names)
            self.assertFalse(any(name.startswith('assistant_action_grant') or name.startswith('assistant_action_key') for name in names))


if __name__ == '__main__': unittest.main()

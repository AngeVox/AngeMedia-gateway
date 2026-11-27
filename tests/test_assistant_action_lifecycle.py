"""归档/保留清理、数据库不变约束与迁移故障回滚。"""
from __future__ import annotations

from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

from assistant_action_support import ActionFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.migrations import run_migrations
from angemedia_gateway.db.assistant_actions import migrate_assistant_actions


class ActionLifecycleTest(ActionFixture, unittest.TestCase):
    def test_archive_scrubs_all_versions_and_preserves_submitted_receipt(self):
        draft, submitted = self.propose(), self.propose()
        receipt = self.confirm(submitted)['action']['job']
        with closing(db_connect()) as conn:
            conn.execute("UPDATE assistant_sessions SET status='archived' WHERE id=?", (self.session,))
            self.assertEqual(conn.execute('SELECT count(*) FROM assistant_action_revisions WHERE body_json IS NOT NULL').fetchone()[0], 0)
        self.assertEqual(self.service.inspect(draft['id'], access=self.alice)['status'], 'revoked')
        replay = self.confirm(submitted)
        self.assertFalse(replay['created']); self.assertEqual(replay['action']['job'], receipt)
        self.assertIsNone(replay['action']['review'])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_session_deletion_keeps_account_receipt_without_body_or_session(self):
        draft, submitted = self.propose(), self.propose()
        self.confirm(submitted)
        with closing(db_connect()) as conn:
            conn.execute('DELETE FROM assistant_sessions WHERE id=?', (self.session,))
        self.assertEqual(self.service.inspect(draft['id'], access=self.alice)['status'], 'revoked')
        replay = self.confirm(submitted)
        self.assertIsNone(replay['action']['session_id']); self.assertIsNone(replay['action']['review'])
        self.assertFalse(replay['created'])
        self.assert_code('action_not_found', self.service.inspect, submitted['id'], access=self.bob)

    def test_revision_cannot_be_rewritten_extended_or_resurrected_after_scrub(self):
        action = self.propose()
        with closing(db_connect()) as conn:
            for assignment in ("binding_digest='changed'", 'expires_ms=expires_ms+1', "body_json='{}'"):
                with self.subTest(assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f'UPDATE assistant_action_revisions SET {assignment} WHERE action_id=?', (action['id'],))
            conn.execute('UPDATE assistant_action_revisions SET body_json=NULL WHERE action_id=?', (action['id'],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE assistant_action_revisions SET body_json='{}' WHERE action_id=?", (action['id'],))

    def test_submitted_identity_and_receipt_cannot_be_rebound(self):
        action = self.propose(); self.confirm(action)
        with closing(db_connect()) as conn:
            for assignment in ("job_id='other'", "status='draft'", 'version=version+1', "creation_key='changed'"):
                with self.subTest(assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f'UPDATE assistant_actions SET {assignment} WHERE id=?', (action['id'],))

    def test_owner_rename_cascades_receipt_and_old_cookie_fails(self):
        from angemedia_gateway.assistant_run_access import RunAccess, RunAuthenticationExpired
        from angemedia_gateway.repositories.admin_auth import create_admin_session
        from angemedia_gateway.security import hash_token
        action = self.propose(); self.confirm(action)
        with closing(db_connect()) as conn:
            conn.execute("UPDATE admin_users SET username='alice-new' WHERE username='alice'")
        with self.assertRaises(RunAuthenticationExpired): self.confirm(action)
        token, _ = create_admin_session('alice-new')
        self.assertFalse(self.confirm(action, access=RunAccess('alice-new', hash_token(token)))['created'])

    def test_migration_repeats_without_changing_existing_rows(self):
        action = self.propose()
        with closing(db_connect()) as conn:
            before = [tuple(row) for row in conn.execute('SELECT * FROM assistant_actions')]
            run_migrations(conn); run_migrations(conn)
            self.assertEqual(before, [tuple(row) for row in conn.execute('SELECT * FROM assistant_actions')])
        self.assertEqual(self.service.inspect(action['id'], access=self.alice)['version'], 1)

    def test_partial_database_defers_migration_without_false_marker(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('assistant_actions_v1', migrate_assistant_actions),)):
                run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='assistant_actions'").fetchone())

    def test_migration_failure_rolls_back_tables_triggers_and_marker(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
            conn.execute('CREATE TABLE admin_users(username TEXT PRIMARY KEY)')
            conn.execute('CREATE TABLE assistant_sessions(id TEXT PRIMARY KEY,status TEXT)')
            def fail(connection):
                migrate_assistant_actions(connection)
                raise RuntimeError('fixture-after-ddl')
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('assistant_actions_v1', fail),)):
                with self.assertRaises(RuntimeError): run_migrations(conn)
            names = {row[0] for row in conn.execute('SELECT name FROM sqlite_master')}
            self.assertNotIn('assistant_actions', names)
            self.assertFalse(any(name.startswith('assistant_action') for name in names))
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()

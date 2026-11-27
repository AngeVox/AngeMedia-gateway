"""来源表迁移幂等、无历史补造，DDL与迁移标记同事务回滚。"""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

from video_job_origin_support import VideoOriginFixture
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.migrations import run_migrations
from angemedia_gateway.db.video_job_origins import migrate_video_job_origins
from angemedia_gateway.repositories.video_job_origins import get_video_job_origin


class VideoJobOriginMigrationTest(VideoOriginFixture, unittest.TestCase):
    def test_repeated_migration_preserves_receipt_and_shared_key(self):
        job, _ = self.submitted_origin(); before = get_video_job_origin(job['id'])
        with closing(db_connect()) as conn:
            key = conn.execute('SELECT value FROM assistant_action_binding_key').fetchone()[0]
            run_migrations(conn); run_migrations(conn)
            self.assertEqual(conn.execute('SELECT value FROM assistant_action_binding_key').fetchone()[0], key)
        self.assertEqual(get_video_job_origin(job['id']), before)

    def test_upgrade_does_not_backfill_existing_submitted_job(self):
        job, runtime = self.submitted_origin()
        with closing(db_connect()) as conn:
            conn.execute('DROP TABLE video_job_origins')
            conn.execute("DELETE FROM schema_migrations WHERE version='video_job_origins_v1'")
            run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM video_job_origins').fetchone()[0], 0)
        self.assertEqual(runtime.handle(self.message(job['id']))['status'], 'failed')
        self.assertEqual(len(self.calls), 1)

    def test_partial_database_defers_receipt_migration_without_key(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT)')
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('video_job_origins_v1', migrate_video_job_origins),)):
                run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='video_job_origins'").fetchone())

    def test_migration_fault_rolls_back_table_triggers_and_marker_then_can_retry(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT)')
            conn.execute('CREATE TABLE assistant_action_binding_key(value BLOB)')
            def fail(connection):
                migrate_video_job_origins(connection)
                raise RuntimeError('fixture migration interruption')
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('video_job_origins_v1', fail),)):
                with self.assertRaises(RuntimeError): run_migrations(conn)
            self.assertFalse(any(row[0].startswith('video_job_origin') for row in conn.execute('SELECT name FROM sqlite_master')))
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 0)
            with patch('angemedia_gateway.db.migrations.MIGRATIONS', (('video_job_origins_v1', migrate_video_job_origins),)):
                run_migrations(conn); run_migrations(conn)
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 1)

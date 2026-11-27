"""命令行冷启动只读指定库，WAL与并发写入不会让盘点遗漏或混合证据。"""
from contextlib import closing
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from video_job_origin_support import VideoOriginFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.job_attempts import create_job_attempt
from angemedia_gateway.repositories.jobs import create_job
from angemedia_gateway.services.video_upgrade_inventory import inventory_video_upgrade
from test_assistant_action_processes import _service

ROOT = Path(__file__).resolve().parents[1]


def writer_child(path, job_id, ready, committed):
    _service(path)
    if not ready.wait(20): raise RuntimeError('fixture wait expired')
    create_job_attempt(job_id=job_id, attempt_number=1, stage='video_submit', worker_kind='local')
    committed.set()


class VideoInventoryProcessesTest(VideoOriginFixture, unittest.TestCase):
    def cli(self, database, *options):
        environment = dict(os.environ)
        environment.update(IMAGE_PROXY_STATE_DIR=str(self.temp / 'must-not-create-state'),
                           ANGEMEDIA_DB_FILE=str(self.temp / 'must-not-open-default.sqlite'))
        process = subprocess.run([sys.executable, str(ROOT/'tools/video_upgrade_preflight.py'), '--database',
                                  str(database), *options], cwd=self.temp, env=environment, capture_output=True,
                                 text=True, encoding='utf-8', timeout=30)
        self.assertFalse((self.temp / 'must-not-create-state').exists())
        self.assertFalse((self.temp / 'must-not-open-default.sqlite').exists())
        self.assertEqual(process.stderr, '')
        return process.returncode, json.loads(process.stdout)

    def test_cold_cli_exit_codes_have_fixed_errors_and_do_not_create_missing_database(self):
        code, report = self.cli(C.DB_FILE)
        self.assertEqual(code, 0); self.assertFalse(report['deployment_ready'])
        job = create_job(kind='video', status='running', provider='agnes_video', external_task_id='private-cli-task')
        code, report = self.cli(C.DB_FILE, '--detail-limit', '0')
        self.assertEqual(code, 2); self.assertEqual(report['blocker_count'], 1); self.assertEqual(report['details'], [])
        missing = self.temp/'private-missing-directory'/'private-file.sqlite'
        code, report = self.cli(missing)
        self.assertEqual(code, 1); self.assertFalse(missing.parent.exists())
        self.assertEqual(report['error_code'], 'video_inventory_unavailable')
        self.assertNotIn('private', json.dumps(report))
        code, report = self.cli(C.DB_FILE, '--detail-limit', '-1')
        self.assertEqual(code, 1)
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM jobs WHERE id=?', (job['id'],)).fetchone()[0], 1)

    def test_cold_cli_sees_committed_uncheckpointed_wal_without_writing_jobs_or_migrations(self):
        with closing(db_connect()) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            create_job(kind='video', status='running', provider='agnes_video', external_task_id='wal-cold-private-task')
            before_jobs = [tuple(row) for row in writer.execute('SELECT * FROM jobs')]
            before_migrations = [tuple(row) for row in writer.execute('SELECT * FROM schema_migrations')]
            code, report = self.cli(C.DB_FILE)
            self.assertEqual(code, 2); self.assertEqual(report['reason_counts'], {'unverified_legacy_task': 1})
            self.assertEqual([tuple(row) for row in writer.execute('SELECT * FROM jobs')], before_jobs)
            self.assertEqual([tuple(row) for row in writer.execute('SELECT * FROM schema_migrations')], before_migrations)
            self.assertNotIn('wal-cold-private-task', json.dumps(report))

    def test_concurrent_child_commit_changes_next_scan_but_not_current_snapshot(self):
        from angemedia_gateway.services import video_upgrade_inventory as module
        job = self.ordinary_job(); context = multiprocessing.get_context('spawn')
        ready, committed = context.Event(), context.Event()
        child = context.Process(target=writer_child, args=(str(C.DB_FILE), job['id'], ready, committed))
        child.start(); original = module.is_worker_managed_video_job
        def coordinate(*args, **kwargs):
            ready.set(); self.assertTrue(committed.wait(25))
            return original(*args, **kwargs)
        try:
            with patch.object(module, 'is_worker_managed_video_job', side_effect=coordinate):
                self.assertEqual(inventory_video_upgrade(C.DB_FILE)['reason_counts'], {'pending_first_submission': 1})
            child.join(10); self.assertEqual(child.exitcode, 0)
            self.assertEqual(inventory_video_upgrade(C.DB_FILE)['reason_counts'], {'submission_outcome_unknown': 1})
        finally:
            if child.is_alive(): child.terminate(); child.join(5)

"""冷CLI/WAL/跨进程快照验证；未知输入固定失败，不读取默认状态目录。"""
from contextlib import closing, redirect_stdout
from io import StringIO
import json
import multiprocessing
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_action_worker_support import ActionWorkerFixture
from angemedia_gateway import config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.services.upgrade_inventory import inventory_application_upgrade

ROOT=Path(__file__).resolve().parents[1]


def _admit_child(database,session,ready,committed):
    if not ready.wait(20):raise RuntimeError('夹具同步超时')
    AssistantRunRepository(db_path=Path(database)).admit(session,uuid4().hex,{'message':'fixture-child'})
    committed.set()


class UpgradeInventoryProcessesTest(ActionWorkerFixture,unittest.TestCase):
    def test_preflight_commands_refuse_foreign_owner_before_creating_wal_sidecars(self):
        from angemedia_gateway.cli.upgrade_preflight import main as inventory

        database = self.temp / 'fixture-owner.sqlite'
        with closing(sqlite3.connect(database)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE fixture(value TEXT)')
        before = database.read_bytes()
        sidecars = [database.with_name(database.name + suffix) for suffix in ('-wal', '-shm')]
        video = runpy.run_path(str(ROOT / 'tools/video_upgrade_preflight.py'))['main']
        for command in (inventory, video):
            with self.subTest(command=command.__module__):
                output = StringIO()
                with patch.object(os, 'geteuid', return_value=database.stat().st_uid + 1, create=True), \
                     patch.object(sys, 'path', sys.path.copy()), redirect_stdout(output):
                    self.assertEqual(command(['--database', str(database)]), 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report['error_code'], 'preflight_database_owner_mismatch')
                self.assertFalse(report['scan_complete'])
                self.assertNotIn(database.name, output.getvalue())
                self.assertEqual(database.read_bytes(), before)
                self.assertTrue(all(not path.exists() for path in sidecars))

    def test_legacy_schema_reports_only_known_missing_structure_without_migration(self):
        old=self.temp/'legacy-private.sqlite'
        with closing(sqlite3.connect(old)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT,kind TEXT,status TEXT,provider TEXT,external_task_id TEXT,private_column TEXT)')
            conn.execute("INSERT INTO jobs VALUES('private-old-id','image','running','modelscope','private-task','private-content')")
            conn.execute('CREATE TABLE private_table(private_secret TEXT)')
        before=old.read_bytes()
        for options in ((),('--detail-limit','0')):
            with self.subTest(options=options):
                code,report=self.cli(old,*options)
                self.assertEqual(code,1)
                self.assertEqual(report['error_code'],'upgrade_inventory_schema_unsupported')
                self.assertEqual(report['inventory_gate'],'unavailable')
                self.assertFalse(report['scan_complete']);self.assertFalse(report['deployment_ready'])
                self.assertEqual(report['missing_columns']['jobs'],['input_json','model','payload_schema_version','stage'])
                self.assertIn('assistant_runs',report['missing_tables'])
                self.assertNotIn('job_counts',report)
                self.assertNotIn('private',json.dumps(report))
        self.assertEqual(old.read_bytes(),before)

    def cli(self,database,*options):
        environment=dict(os.environ)
        environment.update(PYTHONPATH=str(ROOT/'scripts'),IMAGE_PROXY_STATE_DIR=str(self.temp/'must-not-create-state'),
                           ANGEMEDIA_DB_FILE=str(self.temp/'must-not-open-default.sqlite'))
        p=subprocess.run([sys.executable,'-m','angemedia_gateway.cli.upgrade_preflight','--database',str(database),*options],
            cwd=self.temp,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertFalse((self.temp/'must-not-create-state').exists());self.assertFalse((self.temp/'must-not-open-default.sqlite').exists())
        self.assertEqual(p.stderr,'');return p.returncode,json.loads(p.stdout)

    def test_cli_empty_active_missing_and_unsupported_have_distinct_exit_codes(self):
        code,report=self.cli(C.DB_FILE);self.assertEqual(code,0);self.assertFalse(report['deployment_ready'])
        self.repo.admit(self.session,uuid4().hex,{'message':'private-cli-agent'},access=self.alice)
        code,report=self.cli(C.DB_FILE,'--detail-limit','0');self.assertEqual(code,2);self.assertEqual(report['details'],[])
        self.assertIn('assistant_run_active',report['reason_counts']);self.assertNotIn('private-cli-agent',json.dumps(report))
        projected_code, projected = self.cli(C.DB_FILE,'--detail-limit','0','--include-legacy-reviews')
        self.assertEqual(projected_code, code)
        self.assertEqual(projected.pop('legacy_reviews')['reviewed_jobs'], 0)
        self.assertEqual(projected, report)
        missing=self.temp/'private-dir'/'missing.db';code,report=self.cli(missing)
        self.assertEqual(code,1);self.assertFalse(missing.parent.exists());self.assertEqual(report['error_code'],'upgrade_inventory_unavailable')
        wrong=self.temp/'private-wrong.db'
        with closing(sqlite3.connect(wrong)) as conn:conn.execute('CREATE TABLE unrelated(value TEXT)')
        before=wrong.read_bytes();code,report=self.cli(wrong)
        self.assertEqual(code,1);self.assertEqual(wrong.read_bytes(),before);self.assertNotIn('private',json.dumps(report))
        code,report=self.cli(C.DB_FILE,'--detail-limit','-1');self.assertEqual(code,1)

    def test_cold_cli_observes_uncheckpointed_wal_without_changing_main_database(self):
        with closing(db_connect()) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            self.repo.admit(self.session,uuid4().hex,{'message':'fixture-wal-agent'},access=self.alice)
            self.assertTrue(Path(str(C.DB_FILE)+'-wal').exists())
            before_jobs=[tuple(r) for r in writer.execute('SELECT * FROM jobs')]
            before_migrations=[tuple(r) for r in writer.execute('SELECT * FROM schema_migrations')]
            code,report=self.cli(C.DB_FILE)
            self.assertEqual(code,2);self.assertEqual(report['assistant_counts']['runs'],1)
            self.assertEqual([tuple(r) for r in writer.execute('SELECT * FROM jobs')],before_jobs)
            self.assertEqual([tuple(r) for r in writer.execute('SELECT * FROM schema_migrations')],before_migrations)

    def test_snapshot_path_with_uri_reserved_characters_is_literal(self):
        names=['space # percent %.sqlite']+(['question ?.sqlite'] if os.name!='nt' else [])
        for name in names:
            with self.subTest(name=name):
                unusual=self.temp/name
                with closing(db_connect()) as src,closing(sqlite3.connect(unusual)) as dest:src.backup(dest)
                code,report=self.cli(unusual);self.assertEqual(code,0);self.assertFalse(report['deployment_ready'])

    def test_cross_process_commit_cannot_mix_video_and_agent_snapshots(self):
        from angemedia_gateway.services import upgrade_inventory as module
        context=multiprocessing.get_context('spawn');ready,committed=context.Event(),context.Event()
        child=context.Process(target=_admit_child,args=(str(C.DB_FILE),self.session,ready,committed))
        child.start();original=module.scan_video_upgrade
        def coordinate(*args):
            ready.set();self.assertTrue(committed.wait(25));return original(*args)
        try:
            with patch.object(module,'scan_video_upgrade',side_effect=coordinate):
                self.assertEqual(inventory_application_upgrade(C.DB_FILE)['assistant_counts']['runs'],0)
            child.join(10);self.assertEqual(child.exitcode,0)
            self.assertEqual(inventory_application_upgrade(C.DB_FILE)['assistant_counts']['runs'],1)
        finally:
            if child.is_alive():child.terminate();child.join(5)

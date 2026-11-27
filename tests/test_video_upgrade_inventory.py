"""只读升级盘点使用真实队列/收据，旧库不迁移，来源证据不冒充执行授权。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from video_job_origin_support import VideoOriginFixture
import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.jobs import create_job, get_job
from angemedia_gateway.repositories.job_attempts import create_job_attempt
from angemedia_gateway.repositories.video_tasks import upsert_video_task
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.services.video_upgrade_inventory import inventory_video_upgrade


class VideoUpgradeInventoryTest(VideoOriginFixture, unittest.TestCase):
    def scan(self, **kwargs):
        return inventory_video_upgrade(C.DB_FILE, **kwargs)

    def remove_origins(self):
        with closing(db_connect()) as conn:
            conn.execute('DROP TRIGGER video_job_origin_retained')
            conn.execute('DELETE FROM video_job_origins')

    def test_empty_inventory_is_not_deployment_authorization(self):
        report = self.scan()
        self.assertEqual(report['task_gate'], 'no_recorded_blockers')
        self.assertFalse(report['deployment_ready'])
        self.assertTrue(report['scan_complete'])
        self.assertIn('ingress_and_workers_drained', report['not_verified'])

    def test_first_queued_submission_has_no_source_yet_and_is_not_submitted(self):
        job = self.ordinary_job()
        report = self.scan()
        self.assertEqual(report['reason_counts'], {'pending_first_submission': 1})
        self.assertEqual(get_job(job['id']), job)
        self.assertEqual(self.calls, [])
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM video_job_origins').fetchone()[0], 0)

    def test_recorded_origin_still_requires_real_execution_connection_check(self):
        job, _ = self.submitted_origin()
        update_provider_runtime_config('agnes_video', api_key='changed-offline-account')
        before = get_job(job['id']); report = self.scan()
        self.assertEqual(report['reason_counts'], {'original_connection_required': 1})
        self.assertEqual(report['details'][0]['source_evidence'], 'recorded_execution')
        self.assertIn('original_connection_available', report['not_verified'])
        self.assertEqual(get_job(job['id']), before)

    def test_old_queued_followup_blocks_without_creating_a_receipt(self):
        job, _ = self.submitted_origin(); self.remove_origins()
        report = self.scan()
        self.assertEqual(report['reason_counts'], {'unverified_queued_task': 1})
        self.assertEqual(report['blocker_count'], 1)
        self.assertEqual(get_job(job['id'])['status'], 'running')
        self.assertEqual(len(self.calls), 1)

    def test_old_assistant_source_links_original_grant_without_backfill(self):
        job = self.job(self.video_action()); self.video_runtime().handle(self.message(job['id'])); self.remove_origins()
        report = self.scan()
        self.assertEqual(report['blocker_count'], 0)
        self.assertEqual(report['details'][0]['source_evidence'], 'linked_assistant_grant')
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM video_job_origins').fetchone()[0], 0)

    def test_source_receipt_does_not_replace_missing_original_assistant_grant(self):
        job = self.job(self.video_action()); self.video_runtime().handle(self.message(job['id']))
        with closing(db_connect()) as conn:
            conn.execute('DROP TABLE assistant_action_execution_grants')
        report = self.scan()
        self.assertEqual(report['reason_counts'], {'invalid_assistant_grant': 1})
        self.assertEqual(report['blocker_count'], 1)

    def test_changed_intent_or_external_id_invalidates_recorded_source(self):
        job, _ = self.submitted_origin()
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET external_task_id=? WHERE id=?', ('unrelated-task', job['id']))
        self.assertEqual(self.scan()['reason_counts']['invalid_execution_origin'], 1)
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET external_task_id=?,prompt=? WHERE id=?', ('action-video-task', 'changed-intent', job['id']))
        self.assertEqual(self.scan()['reason_counts'], {'invalid_execution_origin': 1})

    def test_started_submission_without_task_id_is_unknown_even_with_receipt(self):
        job = self.ordinary_job()
        runtime = self.video_runtime(on_submit=lambda request: (_ for _ in ()).throw(TimeoutError('uncertain-submit')))
        runtime.handle(self.message(job['id']))
        report = self.scan()
        self.assertEqual(report['reason_counts'], {'submission_unresolved': 1})
        self.assertEqual(report['blocker_count'], 1)
        self.assertEqual(len(self.calls), 1)

    def test_attempt_evidence_prevents_pending_classification_even_when_fields_damaged(self):
        job = self.ordinary_job()
        create_job_attempt(job_id=job['id'], attempt_number=1, stage='video_submit', worker_kind='local')
        self.assertEqual(self.scan()['reason_counts'], {'submission_outcome_unknown': 1})

    def test_terminal_local_failure_with_external_task_remains_unresolved(self):
        job, runtime = self.submitted_origin()
        with closing(db_connect()) as conn:
            conn.execute("UPDATE jobs SET status='failed',stage='finalize',error_code='video_pipeline_timeout' WHERE id=?", (job['id'],))
        self.assertEqual(self.scan()['reason_counts'], {'existing_task_unresolved': 1})

    def test_legacy_active_task_has_no_historical_account_proof(self):
        create_job(kind='video', status='running', provider='agnes_video', external_task_id='legacy-fixture')
        self.assertEqual(self.scan()['reason_counts'], {'unverified_legacy_task': 1})

    def test_cached_only_terminal_and_unresolved_records_have_different_gates(self):
        upsert_video_task('completed-cache', 'private-prompt', 'model', 'completed', {})
        upsert_video_task('running-cache', 'private-prompt', 'model', 'running', {})
        report = self.scan()
        self.assertEqual(report['cached_only_records'], 2)
        self.assertEqual(report['blocker_count'], 1)
        self.assertEqual(report['reason_counts'], {'cached_only_terminal_record': 1, 'cached_only_unresolved_task': 1})

    def test_ambiguity_is_media_scoped_and_no_raw_external_id_is_reported(self):
        for kind in ('video', 'video', 'image'):
            create_job(kind=kind, status='succeeded', external_task_id='private-external-task')
        report = self.scan()
        self.assertEqual(report['reason_counts'], {'ambiguous_external_identity': 1, 'terminal_local_record': 2})
        self.assertEqual(report['details'][0]['matching_jobs'], 2)
        self.assertNotIn('private-external-task', json.dumps(report))

    def test_bounded_details_prioritize_blockers_but_counts_always_scan_all(self):
        create_job(kind='video', status='succeeded')
        create_job(kind='video', status='running', provider='agnes_video', external_task_id='legacy-fixture')
        report = self.scan(detail_limit=1)
        self.assertEqual(report['findings_total'], 2)
        self.assertEqual(report['details_omitted'], 1)
        self.assertTrue(report['details'][0]['blocks_task_gate'])
        self.assertEqual(self.scan(detail_limit=0)['blocker_count'], 1)
        self.assertEqual(self.scan(detail_limit=0)['details'], [])

    def test_malformed_source_and_unknown_status_do_not_return_clean_gate(self):
        job = self.ordinary_job()
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET input_json=? WHERE id=?', ('invalid-json-private-content', job['id']))
        report = self.scan()
        self.assertEqual(report['reason_counts'], {'invalid_source_evidence': 1})
        self.assertNotIn('invalid-json-private-content', json.dumps(report))

    def test_legacy_partial_schema_is_read_without_migration_or_configuration(self):
        path = self.temp / 'old-schema.sqlite'
        with closing(sqlite3.connect(path, isolation_level=None)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT,kind TEXT,status TEXT,provider TEXT,external_task_id TEXT)')
            conn.execute("INSERT INTO jobs VALUES('old-private-id','video','running','agnes_video','old-private-task')")
        before = path.read_bytes()
        report = inventory_video_upgrade(path)
        self.assertEqual(report['reason_counts'], {'unverified_legacy_task': 1})
        self.assertIn('video_job_origins', report['missing_evidence_tables'])
        self.assertNotIn('old-private-id', json.dumps(report))
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('jobs',)])

    def test_missing_database_and_unsupported_schema_are_never_initialized(self):
        path = self.temp / 'not-created' / 'missing.sqlite'
        with self.assertRaises(FileNotFoundError): inventory_video_upgrade(path)
        self.assertFalse(path.parent.exists())
        path = self.temp / 'wrong-schema.sqlite'
        with closing(sqlite3.connect(path)) as conn: conn.execute('CREATE TABLE unrelated(value TEXT)')
        before = path.read_bytes()
        with self.assertRaises(ValueError): inventory_video_upgrade(path)
        self.assertEqual(path.read_bytes(), before)

    def test_retained_bound_origins_and_orphaned_unbound_attempts_are_distinguished(self):
        bound, _ = self.submitted_origin()
        unbound = self.ordinary_job(prompt='unbound-different-prompt')
        self.video_runtime(on_submit=lambda request: (_ for _ in ()).throw(TimeoutError())).handle(self.message(unbound['id']))
        with closing(db_connect()) as conn:
            conn.execute('DELETE FROM jobs')
        report = self.scan()
        self.assertEqual(report['retained_origins_without_job'], 2)
        self.assertEqual(report['reason_counts']['orphaned_unbound_attempt'], 1)
        self.assertEqual(report['blocker_count'], 2)

    def test_report_does_not_expose_prompts_credentials_endpoints_or_error_details(self):
        job, _ = self.submitted_origin(prompt='private-video-prompt')
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET error_message=?,human_hint=? WHERE id=?',
                         ('Bearer private-error-secret', 'https://private.invalid/v1', job['id']))
        rendered = json.dumps(self.scan())
        for private in ('private-video-prompt', C.AGNES_API_KEY, 'action-video-task', 'private-error-secret', 'private.invalid'):
            self.assertNotIn(private, rendered)

    def test_invalid_detail_limits_are_rejected(self):
        for value in (-1, 10001, True, '10'):
            with self.subTest(value=value), self.assertRaises(ValueError): self.scan(detail_limit=value)

    def test_uncheckpointed_wal_is_visible_to_readonly_scan(self):
        with closing(db_connect()) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("INSERT INTO jobs(id,kind,status,provider,external_task_id,created_at,updated_at) "
                           "VALUES(?,'video','running','agnes_video','wal-private-task','fixture','fixture')", ('a'*32,))
            self.assertTrue(Path(str(C.DB_FILE) + '-wal').exists())
            self.assertEqual(self.scan()['reason_counts'], {'unverified_legacy_task': 1})

    def test_evidence_schema_error_cannot_be_mistaken_for_missing_source(self):
        self.ordinary_job()
        with closing(db_connect()) as conn:
            conn.execute('DROP TABLE video_job_origins')
            conn.execute('CREATE TABLE video_job_origins(unrelated TEXT)')
        with self.assertRaises(sqlite3.OperationalError): self.scan()

    def test_concurrent_commit_cannot_mix_job_and_attempt_snapshots(self):
        from angemedia_gateway.services import video_upgrade_inventory as module
        job = self.ordinary_job(); original = module.is_worker_managed_video_job
        changed = False
        def commit_during_scan(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                create_job_attempt(job_id=job['id'], attempt_number=1, stage='video_submit', worker_kind='local')
            return original(*args, **kwargs)
        with patch.object(module, 'is_worker_managed_video_job', side_effect=commit_during_scan):
            self.assertEqual(self.scan()['reason_counts'], {'pending_first_submission': 1})
        self.assertEqual(self.scan()['reason_counts'], {'submission_outcome_unknown': 1})

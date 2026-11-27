"""应用升级总览复用真实入队、授权和原worker；不初始化目标库或调用真实渠道。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_action_worker_support import ActionWorkerFixture
import test_modelscope_durable_pipeline as pipeline
from angemedia_gateway import config as C
from angemedia_gateway.db.connection import db_connect, readonly_snapshot
from angemedia_gateway.repositories.jobs import create_job, get_job
from angemedia_gateway.repositories.job_attempts import create_job_attempt
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.services.upgrade_inventory import inventory_application_upgrade
from angemedia_gateway.services.video_upgrade_inventory import inventory_video_upgrade, scan_video_upgrade


class UpgradeInventoryTest(ActionWorkerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        update_provider_runtime_config('modelscope', enabled=True, api_key='fixture-upgrade-modelscope',
                                       base_url_override='https://modelscope.example.test')

    def scan(self, **kwargs):
        return inventory_application_upgrade(C.DB_FILE, **kwargs)

    def image(self):
        return self.job(self.propose(request={'prompt':'只读升级测试','model':'qwen'}))

    def image_flow(self, *, fail=False, complete=False):
        job=self.image();runtime,_=pipeline._runtime()
        provider,download,calls=pipeline.ModelScopeDurablePipelineTest.clients(self,[TimeoutError()] if fail else None)
        with pipeline.ModelScopeDurablePipelineTest.patches(self,provider,download):
            for _ in range(3 if complete else 1):runtime.handle(self.message(job['id']))
        return get_job(job['id']),calls

    def agent(self):
        return self.repo.admit(self.session,uuid4().hex,{'message':'private-agent-input'},access=self.alice).run

    def test_empty_current_schema_does_not_authorize_deployment(self):
        report=self.scan()
        self.assertEqual(report['inventory_gate'],'no_recorded_blockers')
        self.assertEqual(report['drain_observation'],'no_recorded_work')
        self.assertFalse(report['deployment_ready'])
        self.assertIn('checkpoint_and_media_snapshot_consistency',report['not_verified'])
        self.assertIn('configured_path_mapping',report['not_verified'])

    def test_image_and_agent_only_are_visible_when_video_cli_is_clear(self):
        self.image();self.agent()
        self.assertEqual(inventory_video_upgrade(C.DB_FILE)['video_jobs'],0)
        report=self.scan();self.assertEqual(report['inventory_gate'],'blocked')
        self.assertEqual(report['job_counts']['image'],1);self.assertEqual(report['job_counts']['video'],0)
        self.assertEqual(report['assistant_counts']['runs'],1)
        self.assertIn('job_active',report['reason_counts']);self.assertIn('assistant_run_active',report['reason_counts'])
        self.assertEqual(report['drain_observation'],'recorded_work_present');self.assertEqual(self.calls,[])

    def test_video_classification_remains_original_but_active_job_blocks_drain(self):
        self.job(self.video_action())
        before=inventory_video_upgrade(C.DB_FILE);report=self.scan()
        self.assertEqual(report['video_task_gate'],before['task_gate'])
        self.assertEqual({k.removeprefix('video.'):v for k,v in report['reason_counts'].items() if k.startswith('video.')},before['reason_counts'])
        self.assertIn('job_active',report['reason_counts']);self.assertFalse(report['deployment_ready'])

    def test_accepted_image_is_classified_without_querying_or_resigning(self):
        job,calls=self.image_flow();before=list(calls)
        with closing(db_connect()) as conn:
            rows=[tuple(r) for r in conn.execute('SELECT * FROM image_task_receipts')]
        report=self.scan();self.assertIn('image_original_task_in_progress',report['reason_counts'])
        self.assertNotIn('image_evidence_invalid',report['reason_counts']);self.assertEqual(calls,before)
        self.assertEqual(get_job(job['id'])['stage'],'image_poll')
        with closing(db_connect()) as conn:self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM image_task_receipts')],rows)

    def test_ambiguous_terminal_image_receipt_still_blocks(self):
        job,_=self.image_flow(fail=True)
        self.assertEqual(job['status'],'failed')
        report=self.scan();self.assertIn('image_submission_unresolved',report['reason_counts'])
        self.assertGreater(report['blocker_count'],0)

    def test_completed_image_and_late_guard_dispatch_are_retained_not_new_generation(self):
        job,calls=self.image_flow(complete=True)
        self.assertEqual(job['status'],'succeeded')
        report=self.scan();self.assertEqual(report['blocker_count'],0,report)
        self.assertIn('image_terminal_record',report['reason_counts'])
        self.assertIn('outbox_delivery_retained',report['reason_counts'])
        self.assertEqual([r['method'] for r in calls],['POST','GET','GET'])

    def test_changed_external_identity_invalidates_image_receipt(self):
        job,_=self.image_flow()
        with closing(db_connect()) as conn:conn.execute('UPDATE jobs SET external_task_id=? WHERE id=?',('private-changed-task',job['id']))
        self.assertIn('image_evidence_invalid',self.scan()['reason_counts'])
        self.assertNotIn('private-changed-task',json.dumps(self.scan()))

    def test_removed_job_retains_resolved_image_receipt_without_claiming_history(self):
        self.image_flow(complete=True)
        with closing(db_connect()) as conn:conn.execute('DELETE FROM jobs')
        report=self.scan();self.assertIn('image_orphaned_receipt_retained',report['reason_counts'])
        self.assertEqual(report['blocker_count'],0)

    def test_orphaned_unknown_submit_remains_a_blocker(self):
        self.image_flow(fail=True)
        with closing(db_connect()) as conn:conn.execute('DELETE FROM jobs')
        self.assertIn('image_orphaned_task_unresolved',self.scan()['reason_counts'])

    def test_malformed_and_future_payloads_never_produce_clean_gate_or_echo_body(self):
        job=self.image();original=json.loads(job['input_json'])
        candidates=['private-invalid-json',json.dumps([]),json.dumps({**original,'schema_version':True}),
                    json.dumps({**original,'async_image':{'version':2}})]
        for raw in candidates:
            with self.subTest(raw=raw[:20]):
                with closing(db_connect()) as conn:conn.execute('UPDATE jobs SET input_json=? WHERE id=?',(raw,job['id']))
                report=self.scan();self.assertGreater(report['blocker_count'],0)
                self.assertIn('image_evidence_invalid',report['reason_counts'])
                self.assertNotIn('private-invalid-json',json.dumps(report))

    def test_terminal_video_with_corrupt_json_is_not_hidden_by_terminal_status(self):
        create_job(kind='video',status='succeeded',stage='finalize',input_json='private-broken-json')
        self.assertIn('job_payload_invalid',self.scan()['reason_counts'])

    def test_submitted_image_with_lost_payload_is_not_misclassified_as_legacy_history(self):
        job=self.job(self.propose());self.image_runtime().handle(self.message(job['id']))
        with closing(db_connect()) as conn:conn.execute('UPDATE jobs SET input_json=NULL WHERE id=?',(job['id'],))
        self.assertIn('image_evidence_invalid',self.scan()['reason_counts'])

    def test_unknown_job_kind_state_stage_and_schema_are_sanitized(self):
        job=self.image()
        with closing(db_connect()) as conn:
            conn.execute('PRAGMA ignore_check_constraints=ON')
            conn.execute("UPDATE jobs SET kind='private-kind',status='private-state',stage='private-stage',payload_schema_version=2 WHERE id=?",(job['id'],))
        report=self.scan();self.assertIn('job_state_unknown',report['reason_counts']);self.assertIn('job_contract_unknown',report['reason_counts'])
        for secret in ('private-kind','private-state','private-stage'):self.assertNotIn(secret,json.dumps(report))

    def test_missing_evidence_table_is_reported_without_creating_it(self):
        with closing(db_connect()) as conn:conn.execute('DROP TABLE image_task_receipts')
        report=self.scan();self.assertIn('image_task_receipts',report['missing_evidence_tables'])
        self.assertIn('schema_table_missing',report['reason_counts'])
        with closing(db_connect()) as conn:self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='image_task_receipts'").fetchone())

    def test_future_migration_name_is_hashed_and_not_treated_as_compatible(self):
        with closing(db_connect()) as conn:conn.execute('INSERT INTO schema_migrations VALUES(?,?)',('private-future-schema','fixture'))
        report=self.scan();self.assertIn('schema_migration_unknown',report['reason_counts']);self.assertNotIn('private-future-schema',json.dumps(report))

    def test_absent_assistant_table_count_is_unknown_instead_of_zero(self):
        with closing(db_connect()) as conn:conn.execute('DROP TABLE assistant_runs')
        report=self.scan();self.assertIsNone(report['assistant_counts']['runs'])
        self.assertIn('assistant_runs',report['missing_evidence_tables']);self.assertGreater(report['blocker_count'],0)

    def test_missing_migration_marker_cannot_hide_behind_existing_tables(self):
        with closing(db_connect()) as conn:conn.execute("DELETE FROM schema_migrations WHERE version='image_task_receipts_v1'")
        self.assertIn('schema_migration_missing',self.scan()['reason_counts'])

    def test_missing_binding_table_or_key_is_a_blocker_without_key_output(self):
        with closing(db_connect()) as conn:conn.execute('DROP TABLE assistant_action_binding_key');conn.execute('CREATE TABLE assistant_action_binding_key(id INTEGER,value TEXT)')
        self.assertIn('binding_material_unavailable',self.scan()['reason_counts'])

    def test_present_but_incompatible_table_fails_instead_of_reporting_zero(self):
        with closing(db_connect()) as conn:conn.execute('DROP TABLE image_task_receipts');conn.execute('CREATE TABLE image_task_receipts(unrelated TEXT)')
        with self.assertRaises(ValueError):self.scan()

    def test_missing_database_directory_and_wrong_schema_are_not_initialized(self):
        missing=self.temp/'not-created'/'missing.sqlite'
        with self.assertRaises(FileNotFoundError):inventory_application_upgrade(missing)
        self.assertFalse(missing.parent.exists())
        wrong=self.temp/'wrong.sqlite'
        with closing(sqlite3.connect(wrong)) as conn:conn.execute('CREATE TABLE unrelated(value TEXT)')
        before=wrong.read_bytes()
        with self.assertRaises(ValueError):inventory_application_upgrade(wrong)
        self.assertEqual(wrong.read_bytes(),before)

    def test_legacy_layout_with_no_queue_stage_is_not_migrated(self):
        old=self.temp/'legacy.sqlite'
        with closing(sqlite3.connect(old)) as conn:
            conn.execute('CREATE TABLE jobs(id TEXT,kind TEXT,status TEXT,provider TEXT,external_task_id TEXT)')
            conn.execute("INSERT INTO jobs VALUES('private-old-id','image','running','modelscope','private-task')")
        before=old.read_bytes()
        with self.assertRaises(ValueError):inventory_application_upgrade(old)
        self.assertEqual(old.read_bytes(),before)

    def test_expired_assistant_lease_is_observed_not_reclaimed(self):
        run=self.agent();lease=self.repo.claim(run['id'])
        with closing(db_connect()) as conn:conn.execute('UPDATE assistant_session_leases SET expires_ms=0')
        report=self.scan();self.assertIn('assistant_lease_held',report['reason_counts'])
        with closing(db_connect()) as conn:self.assertEqual(conn.execute('SELECT owner_token FROM assistant_session_leases').fetchone()[0],lease.owner_token)
        self.assertNotIn(lease.owner_token,json.dumps(report))

    def test_all_active_assistant_states_block_even_without_job(self):
        run=self.agent()
        for status in ('queued','running','paused'):
            with self.subTest(status=status):
                with closing(db_connect()) as conn:conn.execute('UPDATE assistant_runs SET status=? WHERE id=?',(status,run['id']))
                self.assertIn('assistant_run_active',self.scan()['reason_counts'])

    def test_draft_remains_unconfirmed_without_blocking_idle_application(self):
        self.propose();before=self.counts();report=self.scan()
        self.assertEqual(report['blocker_count'],0,report)
        self.assertIn('assistant_draft_retained',report['reason_counts']);self.assertEqual(self.counts(),before)
        self.assertEqual(before,(0,0,0))

    def test_missing_draft_body_is_not_a_valid_retained_proposal(self):
        self.propose()
        with closing(db_connect()) as conn:conn.execute('UPDATE assistant_action_revisions SET body_json=NULL')
        self.assertIn('assistant_draft_invalid',self.scan()['reason_counts'])

    def test_running_attempt_blocks_even_when_job_is_terminal(self):
        job,_=self.image_flow(complete=True)
        create_job_attempt(job_id=job['id'],attempt_number=99,stage='image_generate',worker_kind='fixture')
        self.assertIn('job_attempt_running',self.scan()['reason_counts'])

    def test_publishing_terminal_guard_is_still_possible_activity(self):
        self.image_flow(complete=True)
        with closing(db_connect()) as conn:conn.execute("UPDATE job_dispatches SET status='publishing' WHERE status='pending'")
        self.assertIn('outbox_work_pending',self.scan()['reason_counts'])

    def test_unknown_or_cross_media_outbox_payload_is_not_a_clean_guard(self):
        self.image_flow(complete=True)
        with closing(db_connect()) as conn:
            row=conn.execute('SELECT id,payload_json FROM job_dispatches LIMIT 1').fetchone();original=json.loads(row['payload_json'])
        for payload in ('private-bad-payload',json.dumps([]),json.dumps({**original,'payload_schema_version':2}),
                        json.dumps({**original,'job_kind':'video'})):
            with self.subTest(payload=payload[:20]):
                with closing(db_connect()) as conn:conn.execute("UPDATE job_dispatches SET status='pending',payload_json=? WHERE id=?",(payload,row['id']))
                report=self.scan();self.assertIn('outbox_contract_unknown',report['reason_counts']);self.assertNotIn('private-bad-payload',json.dumps(report))

    def test_unknown_dispatch_topic_is_not_a_known_terminal_guard(self):
        self.image_flow(complete=True)
        with closing(db_connect()) as conn:conn.execute("UPDATE job_dispatches SET topic='private-unknown-topic' WHERE status='pending'")
        report=self.scan();self.assertIn('outbox_contract_unknown',report['reason_counts']);self.assertNotIn('private-unknown-topic',json.dumps(report))

    def test_detail_limit_does_not_truncate_counts_and_blockers_come_first(self):
        for _ in range(15):create_job(kind='image',status='succeeded',stage='finalize')
        self.image();complete=self.scan(detail_limit=1000)
        for limit in (0,1,2):
            with self.subTest(limit=limit):
                report=self.scan(detail_limit=limit)
                self.assertEqual(report['reason_counts'],complete['reason_counts']);self.assertEqual(report['findings_total'],complete['findings_total'])
                self.assertEqual(report['blocker_count'],complete['blocker_count']);self.assertEqual(len(report['details']),limit)
                self.assertTrue(all(r['blocks_inventory_gate'] for r in report['details']))
                self.assertEqual(report['details_omitted'],report['findings_total']-limit)

    def test_invalid_limits_are_rejected(self):
        for value in (-1,10001,True,'5'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.scan(detail_limit=value)

    def test_shared_snapshot_is_readonly_and_video_scanner_requires_it(self):
        with readonly_snapshot(C.DB_FILE) as conn:
            with self.assertRaises(sqlite3.OperationalError):conn.execute('DELETE FROM jobs')
            self.assertEqual(scan_video_upgrade(conn,0)['video_jobs'],0)
        with closing(db_connect()) as conn, self.assertRaises(ValueError):scan_video_upgrade(conn,0)

    def test_concurrent_cross_domain_commit_changes_next_scan_only(self):
        from angemedia_gateway.services import upgrade_inventory as module
        original=module.scan_video_upgrade
        def change(*args):
            self.agent();return original(*args)
        with patch.object(module,'scan_video_upgrade',side_effect=change):
            first=self.scan();self.assertEqual(first['assistant_counts']['runs'],0)
        self.assertEqual(self.scan()['assistant_counts']['runs'],1)

    def test_report_does_not_include_configuration_body_paths_or_provider_ids(self):
        job,_=self.image_flow()
        with closing(db_connect()) as conn:
            conn.execute('UPDATE jobs SET prompt=?,error_message=? WHERE id=?',('private-prompt','Bearer private-error',job['id']))
        rendered=json.dumps(self.scan())
        for private in ('private-prompt','private-error','fixture-upgrade-modelscope','modelscope.example.test',str(self.temp),'image-task-1'):
            self.assertNotIn(private,rendered)

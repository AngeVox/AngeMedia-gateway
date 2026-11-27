"""验收应用路由、审阅账本与原升级预检的一致性。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from assistant_run_support import RunFixture
from angemedia_gateway import config as C
from angemedia_gateway.assistant_run_access import RunAuthenticationExpired
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.db.legacy_job_reviews import LEGACY_JOB_REVIEWS_VERSION
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.admin_auth import delete_admin_session
from angemedia_gateway.repositories.job_events import append_job_event
from angemedia_gateway.repositories.jobs import create_job, get_job
from angemedia_gateway.security import admin_session_context
from angemedia_gateway.server import app
from angemedia_gateway.services import upgrade_inventory
from angemedia_gateway.services.job_control import regeneration_control
from angemedia_gateway.services.legacy_job_evidence import LegacyReviewError
from angemedia_gateway.services.legacy_job_reviews import inspect_legacy_job_review, record_legacy_job_review
from angemedia_gateway.services.video_upgrade_inventory import inventory_video_upgrade


class LegacyJobReviewsTest(RunFixture, unittest.TestCase):
    def job(self, *, kind='image', failure='rate_limit', status=429, source=True):
        provider = 'modelscope' if kind == 'image' else 'agnes_video'
        stage = 'image_generate' if kind == 'image' else 'video_submit'
        code = 'image_provider_failure' if kind == 'image' else 'video_submit_ambiguous'
        operation = 'ModelScope submit' if kind == 'image' else 'agnes_video submit'
        message = f'{operation} failed: HTTP {status} {failure}'
        data = {'schema_version': 1, 'route': {'routes': [{'provider': provider}]}}
        job = create_job(kind=kind, provider=provider, model='fixture-model', status='failed',
                         input_json=json.dumps(data) if kind == 'image' else None,
                         error_code=code, error_message=message, prompt='private-prompt', attempt_count=1)
        if source:
            with closing(db_connect()) as conn:
                conn.execute(
                    'INSERT INTO job_attempts(id,job_id,attempt_number,stage,status,started_at,error_code,error_message) '
                    "VALUES(?,?,1,?,'failed','old',?,?)", (uuid4().hex, job['id'], stage, code, message))
                append_job_event(job['id'], 'worker_attempt_failed', {'attempt': 1, 'error_code': code},
                                 to_status='failed', stage='finalize', conn=conn)
        return job['id']

    def inspect(self, job_id):
        return inspect_legacy_job_review(job_id, access=self.access['alice'])

    def write(self, job_id, *, request_key=None, digest=None, user='alice'):
        if digest is None:
            digest = self.inspect(job_id)['proposal']['evidence_digest']
        return record_legacy_job_review(
            job_id, request_key=request_key or uuid4().hex, evidence_digest=digest, access=self.access[user])

    def scan(self, **kwargs):
        return upgrade_inventory.inventory_application_upgrade(C.DB_FILE, **kwargs)

    def test_confirmed_review_preserves_base_report_and_recovery(self):
        for kind in ('image', 'video'):
            with self.subTest(kind=kind):
                original = self.job(kind=kind)
                job = get_job(original)
                baseline = self.scan()
                self.assertEqual(baseline['blocker_count'], 0)
                reason = 'image_recorded_submission_rejection' if kind == 'image' else 'video.recorded_submission_rejection'
                self.assertIn(reason, baseline['reason_counts'])
                self.assertEqual(self.inspect(original)['proposal']['http_status'], 429)
                receipt = self.write(original)
                self.assertFalse(receipt['replayed'])
                self.assertEqual(get_job(original), job)
                self.assertFalse(regeneration_control(job)['enabled'])
                report = self.scan(include_legacy_reviews=True)
                reviews = report.pop('legacy_reviews')
                self.assertEqual(report, baseline)
                self.assertIn({'job_id': original, 'state': 'current', 'conclusion': 'rejection_reviewed',
                               'http_status': 429}, reviews['details'])
                self.assertNotIn('private-prompt', json.dumps(reviews))
                self.assertNotIn('alice', json.dumps(reviews))

    def test_only_supported_rejection_with_original_chain_is_confirmable(self):
        for status, reason, source in [(503, 'upstream', True), (429, 'auth', True), (403, 'auth', False)]:
            with self.subTest(status=status, source=source):
                job_id = self.job(status=status, failure=reason, source=source)
                self.assertEqual(self.inspect(job_id)['proposal']['conclusion'], 'needs_evidence')
        job_id = self.job(kind='video')
        with closing(db_connect()) as conn:
            conn.execute("UPDATE jobs SET external_task_id='fixture-remote',provider_status='FAILED' WHERE id=?", (job_id,))
        self.write(job_id)
        self.assertEqual(self.inspect(job_id)['review']['conclusion'], 'needs_evidence')

    def test_stale_and_cross_job_evidence_is_rejected_and_can_be_recaptured(self):
        job_id, other_id = self.job(), self.job()
        old = self.inspect(job_id)['proposal']['evidence_digest']
        self.write(job_id, digest=old)
        self.assert_code('review_evidence_stale', self.write, other_id, digest=old)
        with closing(db_connect()) as conn:
            append_job_event(job_id, 'state_updated', {'version': 2}, conn=conn)
        self.assert_code('review_evidence_stale', self.write, job_id, digest=old)
        self.assertEqual(self.inspect(job_id)['review']['state'], 'task_changed')
        self.write(job_id)
        self.assertEqual(self.inspect(job_id)['review']['state'], 'current')

    def test_latest_review_with_changed_evidence_is_reported_without_using_older_one(self):
        job_id = self.job()
        self.write(job_id)
        latest = self.write(job_id)
        with closing(db_connect()) as conn:
            conn.execute('DROP TRIGGER legacy_job_reviews_update')
            conn.execute("UPDATE legacy_job_reviews SET evidence_json='{}' WHERE sequence=?", (latest['sequence'],))
        self.assertEqual(self.inspect(job_id)['review']['state'], 'evidence_changed')
        self.assertEqual(self.scan(include_legacy_reviews=True)['legacy_reviews']['state_counts'], {'evidence_changed': 1})

    def test_concurrent_replays_append_once_and_keys_cannot_change_binding(self):
        job_id, other_id = self.job(), self.job()
        digest = self.inspect(job_id)['proposal']['evidence_digest']
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.write(job_id, request_key=key, digest=digest), range(4)))
        self.assertEqual({result['sequence'] for result in results}, {1})
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        self.assert_code('review_request_conflict', self.write, other_id, request_key=key)
        self.assert_code('review_request_conflict', self.write, job_id, request_key=key, user='bob')

    def test_ledger_keeps_history_on_job_cleanup_and_guards_replacement(self):
        job_id = self.job()
        self.write(job_id)
        with closing(db_connect()) as conn:
            for statement in ('UPDATE legacy_job_reviews SET reviewer=\'other\'',
                              'DELETE FROM legacy_job_reviews',
                              'INSERT OR REPLACE INTO legacy_job_reviews SELECT * FROM legacy_job_reviews'):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement)
            conn.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        observed = self.inspect(job_id)
        self.assertIsNone(observed['proposal'])
        self.assertEqual(observed['review']['state'], 'job_unavailable')
        self.assertEqual(observed['review']['reviewer'], 'alice')

    def test_revoked_access_is_rechecked_in_write_transaction(self):
        job_id = self.job()
        digest = self.inspect(job_id)['proposal']['evidence_digest']
        delete_admin_session(self.cookies['alice'])
        with self.assertRaises(RunAuthenticationExpired):
            self.write(job_id, digest=digest)
        self.assertEqual(self.scan(include_legacy_reviews=True)['legacy_reviews']['reviewed_jobs'], 0)

    def test_routes_check_cookie_context_and_accept_only_bound_digest(self):
        job_id = self.job()
        route = f'/v1/admin/jobs/{job_id}/legacy-review'
        with closing(TestClient(app)) as client:
            self.assertEqual(client.get(route).status_code, 401)
            with patch.object(C, 'GATEWAY_API_KEY', 'fixture-gateway-review-key'):
                self.assertEqual(client.get(route, headers={'X-API-Key': C.GATEWAY_API_KEY}).status_code, 403)
            client.cookies.set('am_admin_session', self.cookies['alice'])
            self.assertEqual(client.get(route).status_code, 409)
            headers = {'X-Studio-Session-Context': admin_session_context(self.cookies['alice'])}
            result = client.get(route, headers=headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers['cache-control'], 'no-store')
            payload = {'evidence_digest': result.json()['proposal']['evidence_digest'], 'request_key': uuid4().hex}
            self.assertEqual(client.post(route, headers=headers, json={**payload, 'reviewer': 'bob'}).status_code, 422)
            self.assertEqual(client.post(route, headers=headers, json={**payload, 'evidence_digest': 'a' * 64}).status_code, 409)
            committed = client.post(route, headers=headers, json=payload)
            self.assertEqual(committed.status_code, 200, committed.text)
            self.assertEqual(client.get(route, headers=headers).json()['review']['reviewer'], 'alice')

    def test_preflight_keeps_review_projection_in_the_same_snapshot(self):
        job_id = self.job()
        digest = self.inspect(job_id)['proposal']['evidence_digest']
        inventory_video = upgrade_inventory.scan_video_upgrade

        def commit_during_scan(conn, detail_limit):
            self.write(job_id, digest=digest)
            return inventory_video(conn, detail_limit)

        with patch.object(upgrade_inventory, 'scan_video_upgrade', side_effect=commit_during_scan):
            view = self.scan(include_legacy_reviews=True)
        self.assertEqual(view['legacy_reviews']['reviewed_jobs'], 0)
        next_view = self.scan(include_legacy_reviews=True, detail_limit=0)
        self.assertEqual(next_view['legacy_reviews']['state_counts'], {'current': 1})
        self.assertEqual(next_view['legacy_reviews']['details'], [])
        self.assertEqual(next_view['legacy_reviews']['details_omitted'], 1)

    def test_previous_schema_reclassifies_only_rejections_before_and_after_review(self):
        recorded = {
            'image': [400, 401, 403, 403, 403, 429],
            'video': [401, 401, 401, 403, 403, 429],
        }
        categories = {400: 'validation', 401: 'auth', 403: 'auth', 429: 'rate_limit'}
        confirmed = {
            self.job(kind=kind, status=status, failure=categories[status])
            for kind, statuses in recorded.items() for status in statuses
        }
        unresolved = {
            self.job(kind=kind, status=503, failure='upstream')
            for kind, count in (('image', 7), ('video', 8)) for _ in range(count)
        }
        with closing(db_connect()) as conn:
            conn.execute('DROP TABLE legacy_job_reviews')
            conn.execute('DELETE FROM schema_migrations WHERE version=?', (LEGACY_JOB_REVIEWS_VERSION,))
            backup = self.temp / 'previous-schema.db'
            with closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
        previous = self.scan()
        self.assertEqual(previous['blocker_count'], 15)
        self.assertEqual({row['job_id'] for row in previous['details'] if row['blocks_inventory_gate']}, unresolved)
        self.assertEqual(previous['reason_counts']['image_recorded_submission_rejection'], 6)
        video = inventory_video_upgrade(C.DB_FILE, detail_limit=0)
        self.assertEqual(video['blocker_count'], 8)
        self.assertEqual(video['reason_counts']['recorded_submission_rejection'], 6)
        self.assertEqual(self.scan(include_legacy_reviews=True)['legacy_reviews']['state'], 'unavailable')
        init_db()
        init_db()
        self.assertEqual(self.scan(), previous)
        for job_id in confirmed | unresolved:
            self.assertFalse(regeneration_control(get_job(job_id))['enabled'])
            self.write(job_id)
        reviewed = self.scan(include_legacy_reviews=True)
        self.assertEqual(reviewed.pop('legacy_reviews')['reviewed_jobs'], 27)
        self.assertEqual(reviewed, previous)
        with closing(sqlite3.connect(backup)) as source, closing(db_connect()) as restored:
            source.backup(restored)
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.scan(), previous)
        self.assertEqual(self.scan(include_legacy_reviews=True)['legacy_reviews']['state'], 'unavailable')

    def test_rejection_evidence_cannot_hide_conflicting_execution_signals(self):
        accepted, later_stage, claimed, damaged = (self.job(kind='video') for _ in range(4))
        with closing(db_connect()) as conn:
            conn.execute("UPDATE jobs SET provider_status='queued' WHERE id=?", (accepted,))
            conn.execute("UPDATE jobs SET claim_token='fixture-owner' WHERE id=?", (claimed,))
            conn.execute("UPDATE jobs SET input_json='[]' WHERE id=?", (damaged,))
            append_job_event(later_stage, 'state_updated', {}, to_status='running', stage='video_poll', conn=conn)
        report = inventory_video_upgrade(C.DB_FILE)
        self.assertEqual(report['blocker_count'], 4)
        self.assertEqual(report['reason_counts'], {'submission_unresolved': 4})

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(LegacyReviewError) as exc:
            function(*args, **kwargs)
        self.assertEqual(exc.exception.code, code)

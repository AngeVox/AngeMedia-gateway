"""动作状态机、账户授权、版本与费用同意；使用正式媒体准备和真实SQLite入队。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from assistant_action_support import ActionFixture
import angemedia_gateway.config as C
from angemedia_gateway.assistant_action_contract import ActionConfirmation
from angemedia_gateway.assistant_run_access import RunAuthenticationExpired
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.repositories.settings import set_config
from angemedia_gateway.services.assistant_action_service import AssistantActionService


class ActionServiceTest(ActionFixture, unittest.TestCase):
    def test_proposal_is_readonly_for_jobs_and_public_review_is_honest(self):
        action = self.propose()
        self.assertEqual(self.counts(), (0, 0, 0))
        review = action['review']
        self.assertTrue(review['allowed'])
        self.assertIsNone(review['cost']['amount'])
        self.assertTrue(review['cost']['may_incur_cost'])
        self.assertFalse(review['routes'][0]['account_access_verified'])
        self.assertFalse(review['routes'][0]['generation_verified'])
        encoded = json.dumps(action)
        for private in ('admission', 'configuration_bindings', 'environment_stamp', self.alice.session_hash, C.SILICONFLOW_API_KEY):
            self.assertNotIn(private, encoded)

    def test_creation_replay_binds_raw_request_and_does_not_reprepare(self):
        key = uuid4().hex
        action = self.propose(key=key)
        with patch.object(self.service.preparer, 'prepare', side_effect=AssertionError('重复提案不应重新准备')):
            replay = self.service.propose(self.session, key, 'image', {'prompt': '蓝色山谷', 'model': 'kolors'}, access=self.alice)
        self.assertFalse(replay['created']); self.assertEqual(replay['action']['id'], action['id'])
        self.assert_code('action_request_conflict', self.propose, key=key, request={'prompt': '红色山谷', 'model': 'kolors'})

    def test_revision_rejects_stale_confirmation_and_replays_exact_revision(self):
        old = self.propose(); key = uuid4().hex
        request = {'prompt': '绿色山谷', 'model': 'kolors'}
        new = self.service.revise(old['id'], 1, key, 'image', request, access=self.alice)['action']
        self.assertEqual(new['version'], 2); self.assertNotEqual(old['binding_digest'], new['binding_digest'])
        self.assert_code('action_version_conflict', self.confirm, old)
        replay = self.service.revise(old['id'], 1, key, 'image', request, access=self.alice)
        self.assertFalse(replay['created']); self.assertEqual(replay['action']['version'], 2)
        self.assert_code('action_request_conflict', self.service.revise, old['id'], 2, key, 'image', {'prompt': 'yellow'}, access=self.alice)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertTrue(self.confirm(new)['created'])

    def test_wrong_binding_and_model_confirmed_true_cannot_authorize(self):
        action = self.propose()
        self.assert_code('invalid_confirmation', self.service.confirm, action['id'], {'confirmed': True}, access=self.alice)
        self.assert_code('action_binding_conflict', self.service.confirm, action['id'], ActionConfirmation(1, '0' * 64), access=self.alice)
        self.assert_code('action_acknowledgements_required', self.service.confirm, action['id'], self.confirmation(action, acknowledgements=[]), access=self.alice)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_owner_and_live_cookie_are_rechecked(self):
        action = self.propose()
        self.assert_code('action_not_found', self.confirm, action, access=self.bob)
        self.assert_code('action_not_found', self.service.inspect, action['id'], access=self.bob)
        with closing(db_connect()) as conn:
            conn.execute('UPDATE admin_sessions SET expires_at=0 WHERE session_hash=?', (self.alice.session_hash,))
        with self.assertRaises(RunAuthenticationExpired): self.confirm(action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_logout_during_preparation_prevents_commit(self):
        action = self.propose(); prepare = self.service.preparer.prepare
        def logout(*args):
            candidate = prepare(*args)
            with closing(db_connect()) as conn:
                conn.execute('DELETE FROM admin_sessions WHERE session_hash=?', (self.alice.session_hash,))
            return candidate
        with patch.object(self.service.preparer, 'prepare', side_effect=logout):
            with self.assertRaises(RunAuthenticationExpired): self.confirm(action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_expiration_requires_explicit_revision_and_revoke_is_final(self):
        action = self.propose(); self.now += 1000
        self.assertEqual(self.service.inspect(action['id'], access=self.alice)['status'], 'expired')
        self.assert_code('action_expired', self.confirm, action)
        fresh = self.service.revise(action['id'], 1, uuid4().hex, 'image', action['review']['request'], access=self.alice)['action']
        self.assertEqual(fresh['version'], 2)
        revoked = self.service.revoke(action['id'], access=self.alice)
        self.assertEqual(revoked['status'], 'revoked'); self.assertIsNone(revoked['review'])
        self.assert_code('action_not_draft', self.confirm, fresh)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_policy_change_cannot_be_overridden_by_cost_acknowledgement(self):
        action = self.propose()
        set_config('ANGE_ASSISTANT_ALLOW_PAID', 'false')
        self.assert_code('action_policy_denied', self.confirm, action)
        denied = self.propose()
        self.assertIn('paid_generation_disabled', denied['review']['blocked_reasons'])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_environment_change_between_prepare_and_write_aborts(self):
        action = self.propose(); prepare = self.service.preparer.prepare
        def change(*args):
            candidate = prepare(*args)
            set_config('ANGE_ASSISTANT_ALLOW_PAID', 'false')
            return candidate
        with patch.object(self.service.preparer, 'prepare', side_effect=change):
            self.assert_code('action_environment_changed', self.confirm, action)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_capacity_revision_limit_and_pagination(self):
        service = AssistantActionService(clock=lambda: self.now, ttl_ms=1000, max_pending=1, max_revisions=1)
        action = self.propose(service=service)
        self.assert_code('action_capacity_reached', self.propose, service=service)
        self.assert_code('action_revision_limit', service.revise, action['id'], 1, uuid4().hex, 'image', {'prompt': '绿', 'model': 'kolors'}, access=self.alice)
        self.assert_code('invalid_action_pagination', service.list, self.session, access=self.alice, limit=True)
        self.assertEqual(service.list(self.session, access=self.alice)['total'], 1)
        self.now += 1000
        self.propose(service=service)
        self.assertEqual(service.list(self.session, access=self.alice, limit=1)['total'], 2)

    def test_confirm_replay_returns_original_job_after_policy_expiry_and_job_cleanup(self):
        action = self.propose(); result = self.confirm(action)
        job_id = result['action']['job']['id']
        self.now += 2000; set_config('ANGE_ASSISTANT_ENABLED', 'false')
        replay = self.confirm(action)
        self.assertFalse(replay['created']); self.assertEqual(replay['action']['job']['id'], job_id)
        self.assert_code('action_already_submitted', self.service.revoke, action['id'], access=self.alice)
        with closing(db_connect()) as conn: conn.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        replay = self.confirm(action)
        self.assertEqual(replay['action']['job'], {'id': job_id, 'status': 'record_unavailable'})
        self.assertEqual(self.counts(), (0, 0, 1))

    def test_revision_cannot_reactivate_expired_action_over_pending_capacity(self):
        service = AssistantActionService(clock=lambda: self.now, ttl_ms=1000, max_pending=1)
        expired = self.propose(service=service)
        self.now += 1000
        self.propose(service=service)
        self.assert_code('action_capacity_reached', service.revise, expired['id'], 1, uuid4().hex, 'image',
            {'prompt': '山谷', 'model': 'kolors'}, access=self.alice)
        self.assertEqual(service.inspect(expired['id'], access=self.alice)['version'], 1)

    def test_same_confirmation_in_four_threads_commits_one_job(self):
        action = self.propose()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.confirm(action), range(4)))
        self.assertEqual(sum(result['created'] for result in results), 1)
        self.assertEqual(len({result['action']['job']['id'] for result in results}), 1)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_equal_content_in_two_distinct_actions_is_two_deliberate_jobs(self):
        first, second = self.propose(), self.propose()
        self.assertNotEqual(self.confirm(first)['action']['job']['id'], self.confirm(second)['action']['job']['id'])
        self.assertEqual(self.counts(), (2, 2, 2))

    def test_receipt_failure_rolls_back_job_event_outbox_and_submission_key(self):
        action = self.propose()
        with closing(db_connect()) as conn:
            conn.execute("CREATE TRIGGER fail_action_receipt BEFORE UPDATE OF job_id ON assistant_actions BEGIN SELECT RAISE(ABORT,'fixture_receipt_failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.confirm(action)
        self.assertEqual(self.counts(), (0, 0, 0))
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM job_events').fetchone()[0], 0)
            conn.execute('DROP TRIGGER fail_action_receipt')
        self.assertEqual(self.service.inspect(action['id'], access=self.alice)['status'], 'draft')
        self.assertTrue(self.confirm(action)['created'])


if __name__ == '__main__': unittest.main()

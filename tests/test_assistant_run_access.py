"""入队/取消/观察的登录与归属事务、队列上限和运行范围合同。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import unittest
from uuid import uuid4

from assistant_run_support import RunFixture
from angemedia_gateway.assistant_run_access import RunAuthenticationExpired, RunCursorInvalid, RunUnavailable
from angemedia_gateway.assistant_runtime_contract import RunBusy
from angemedia_gateway.db.connection import db_connect, db_transaction
from angemedia_gateway.repositories.admin_auth import delete_admin_session
from angemedia_gateway.repositories.assistant_conversations import ConversationNotFound
from angemedia_gateway.repositories.assistant_runtime import AssistantRunRepository
from angemedia_gateway.repositories.assistant_sessions import create_assistant_session


class RunAccessTest(RunFixture, unittest.TestCase):
    def admit(self, **kwargs):
        return self.repo.admit(self.session, uuid4().hex, {"message": "保持蓝色"}, access=self.access["alice"], **kwargs).run["id"]

    def test_ownership_is_checked_for_admission_cancel_and_snapshot(self):
        run_id = self.admit()
        for operation in (lambda: self.repo.admit(self.session, uuid4().hex, {}, access=self.access["bob"]),
                          lambda: self.repo.cancel(run_id, access=self.access["bob"]),
                          lambda: self.repo.snapshot(run_id, access=self.access["bob"])):
            with self.assertRaises(ConversationNotFound):
                operation()
        self.assertEqual(self.repo.get(run_id)["status"], "queued")

    def test_revoked_cookie_is_rechecked_even_for_idempotent_replays(self):
        key = uuid4().hex
        run_id = self.repo.admit(self.session, key, {}, access=self.access["alice"]).run["id"]
        delete_admin_session(self.cookies["alice"])
        for operation in (lambda: self.repo.admit(self.session, key, {}, access=self.access["alice"]),
                          lambda: self.repo.snapshot(run_id, access=self.access["alice"]),
                          lambda: self.repo.cancel(run_id, access=self.access["alice"])):
            with self.assertRaises(RunAuthenticationExpired):
                operation()

    def test_expiry_is_rechecked_in_the_database_transaction(self):
        run_id = self.admit()
        with closing(db_connect()) as conn:
            conn.execute("UPDATE admin_sessions SET expires_at=0 WHERE username='alice'")
        with self.assertRaises(RunAuthenticationExpired):
            self.repo.snapshot(run_id, access=self.access["alice"])

    def test_logout_while_waiting_for_write_lock_prevents_admission(self):
        started = threading.Event()
        def submit():
            started.set()
            return self.admit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with db_transaction(immediate=True) as conn:
                future = pool.submit(submit)
                self.assertTrue(started.wait(2))
                conn.execute("DELETE FROM admin_sessions WHERE username='alice'")
            with self.assertRaises(RunAuthenticationExpired):
                future.result(timeout=5)
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM assistant_runs").fetchone()[0], 0)

    def test_disabled_runtime_does_not_enqueue_but_can_replay_existing_run(self):
        key = uuid4().hex
        with self.assertRaises(RunUnavailable):
            self.repo.admit(self.session, key, {}, access=self.access["alice"], accept_new=False)
        admission = self.repo.admit(self.session, key, {}, access=self.access["alice"])
        replay = self.repo.admit(self.session, key, {}, access=self.access["alice"], accept_new=False)
        self.assertFalse(replay.created)
        self.assertEqual(replay.run["id"], admission.run["id"])

    def test_queue_capacity_is_atomic_and_does_not_block_same_request_retry(self):
        key = uuid4().hex
        first = self.repo.admit(self.session, key, {}, access=self.access["alice"], max_pending=1)
        self.assertFalse(self.repo.admit(self.session, key, {}, access=self.access["alice"], max_pending=1).created)
        with self.assertRaises(RunBusy):
            self.admit(max_pending=1)
        self.repo.cancel(first.run["id"], access=self.access["alice"])
        self.admit(max_pending=1)

    def test_public_snapshot_pages_have_a_consistent_head_and_hide_control_fields(self):
        run_id = self.admit()
        lease = self.repo.claim(run_id)
        self.repo.publish_message(lease, "user", "输入")
        for index in range(5):
            self.repo.append_event(lease, "tool_progress", {"index": index, "owner_token": "private-owner"})
        self.repo.finish(lease, answer="完成", output={"api_key": "fixture-secret"})
        after, sequences = 0, []
        while True:
            page = self.repo.snapshot(run_id, access=self.access["alice"], after=after, limit=2)
            sequences += [event["sequence"] for event in page["events"]]
            after = page["next_after"]
            self.assertNotIn("private-owner", str(page))
            self.assertNotIn("fixture-secret", str(page))
            self.assertNotIn("session_hash", str(page))
            self.assertEqual(page["run"]["answer"], "完成")
            for field in ("input_json", "request_key", "fence", "owner_token", "runtime_managed"):
                self.assertNotIn(field, page["run"])
            if not page["has_more"]:
                break
        self.assertEqual(sequences, list(range(1, after + 1)))
        for cursor in (-1, True, after + 1, 2 ** 64):
            with self.subTest(cursor=cursor), self.assertRaises(RunCursorInvalid):
                self.repo.snapshot(run_id, access=self.access["alice"], after=cursor)

    def test_account_recovery_never_claims_or_expires_shared_managed_records(self):
        create_assistant_session("shared", "历史实验")
        legacy = AssistantRunRepository(clock=lambda: 0)
        run_id = legacy.admit("shared", uuid4().hex, {}, timeout_ms=1).run["id"]
        self.assertEqual(self.repo.ready(), [])
        self.assertEqual(self.repo.expire_due(), 0)
        self.assertEqual(legacy.get(run_id)["status"], "queued")
        self.admit(max_pending=1)


if __name__ == "__main__":
    unittest.main()

"""Transactional outbox dispatcher behavior with a fake broker."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class _FakeBackend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def publish(self, *, topic, message):
        self.calls.append((topic, message))
        if self.error:
            raise self.error
        return f"broker-{message.dispatch_id}"


class JobDispatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="job-dispatcher-test-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        import angemedia_gateway.config as C

        self._orig_db = C.DB_FILE
        self._config = C
        C.DB_FILE = self.db_path
        init_db()
        self.now = datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self._config.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _admit(self, digest: str = "a" * 64):
        from angemedia_gateway.services.job_admission import JobAdmissionService

        return JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash=digest,
            request_hash_version=1,
            payload={"prompt": "safe"},
        )

    def _dispatcher(self, backend, **overrides):
        from angemedia_gateway.services.job_dispatcher import JobDispatcher

        defaults = {
            "queue_backend": backend,
            "batch_size": 20,
            "lease_seconds": 30,
            "max_attempts": 3,
            "retry_base_seconds": 2,
            "now_func": lambda: self.now,
        }
        defaults.update(overrides)
        return JobDispatcher(**defaults)

    def test_claim_publish_and_mark_published_with_events(self) -> None:
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch
        from angemedia_gateway.repositories.job_events import list_job_events

        admitted = self._admit()
        backend = _FakeBackend()
        result = self._dispatcher(backend).dispatch_once()

        self.assertEqual((result.claimed, result.published, result.retried), (1, 1, 0))
        dispatch = get_job_dispatch(admitted.dispatch["id"])
        self.assertEqual(dispatch["status"], "published")
        self.assertEqual(len(backend.calls), 1)
        message = backend.calls[0][1]
        self.assertEqual(message.job_id, admitted.job["id"])
        self.assertEqual(message.dispatch_id, admitted.dispatch["id"])
        event_types = [item["event_type"] for item in list_job_events(admitted.job["id"])]
        self.assertIn("dispatch_claimed", event_types)
        self.assertIn("dispatch_published", event_types)

    def test_publish_failure_releases_for_retry_and_sanitizes_error(self) -> None:
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch
        from angemedia_gateway.repositories.job_events import list_job_events

        admitted = self._admit()
        secret = "Bearer queue-secret-token-123456"
        signed = "https://cdn.example/x?token=signed-secret"
        broker_secret = "redis://:redis-password@redis:6379/0"
        backend = _FakeBackend(RuntimeError(f"redis unavailable {secret} {signed} {broker_secret}"))

        result = self._dispatcher(backend).dispatch_once()

        self.assertEqual((result.published, result.retried), (0, 1))
        dispatch = get_job_dispatch(admitted.dispatch["id"])
        self.assertEqual(dispatch["status"], "pending")
        rendered = repr(dispatch)
        self.assertNotIn("queue-secret-token", rendered)
        self.assertNotIn("signed-secret", rendered)
        self.assertNotIn("redis-password", rendered)
        events = list_job_events(admitted.job["id"])
        self.assertNotIn("queue-secret-token", repr(events))
        self.assertNotIn("signed-secret", repr(events))
        self.assertNotIn("redis-password", repr(events))

    def test_expired_lease_is_reclaimed(self) -> None:
        from angemedia_gateway.repositories.job_dispatches import claim_pending_dispatches

        admitted = self._admit()
        claim_pending_dispatches(
            claim_token="dead-dispatcher",
            claim_expires_at="2026-06-21T09:59:00+00:00",
            now="2026-06-21T09:58:00+00:00",
        )
        backend = _FakeBackend()

        result = self._dispatcher(backend).dispatch_once()

        self.assertEqual(result.published, 1)
        self.assertEqual(backend.calls[0][1].dispatch_id, admitted.dispatch["id"])

    def test_already_published_dispatch_is_not_published_again(self) -> None:
        self._admit()
        backend = _FakeBackend()
        dispatcher = self._dispatcher(backend)
        dispatcher.dispatch_once()
        second = dispatcher.dispatch_once()
        self.assertEqual(second.claimed, 0)
        self.assertEqual(len(backend.calls), 1)

    def test_max_publish_attempts_marks_outbox_failed_without_infinite_retry(self) -> None:
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch

        admitted = self._admit()
        backend = _FakeBackend(RuntimeError("redis unavailable"))
        result = self._dispatcher(backend, max_attempts=1).dispatch_once()
        self.assertEqual(result.failed, 1)
        self.assertEqual(get_job_dispatch(admitted.dispatch["id"])["status"], "failed")
        self.assertEqual(self._dispatcher(backend, max_attempts=1).dispatch_once().claimed, 0)
        self.assertEqual(len(backend.calls), 1)

    def test_unsafe_outbox_payload_never_reaches_broker_message(self) -> None:
        admitted = self._admit()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE job_dispatches SET payload_json=? WHERE id=?",
                (json.dumps({
                    "stage": "image_generate",
                    "api_key": "sk-outbox-secret-123456789",
                    "raw_body": "raw-provider-body",
                    "url": "https://cdn.example/x?token=signed-secret",
                    "data_url": "data:image/png;base64,RAWBASE64",
                }), admitted.dispatch["id"]),
            )
        backend = _FakeBackend()
        self._dispatcher(backend).dispatch_once()
        rendered = repr(backend.calls[0][1].to_dict())
        for forbidden in ("outbox-secret", "raw-provider-body", "signed-secret", "RAWBASE64"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()

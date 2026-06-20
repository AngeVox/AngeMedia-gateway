"""Formal queue foundation contracts: migrations, admission, CAS, and safety."""
from __future__ import annotations

import json
import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class _QueueFoundationTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="queue-foundation-test-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        import angemedia_gateway.config as C

        self._orig_db = C.DB_FILE
        self._config_mod = C
        C.DB_FILE = self.db_path
        init_db()

    def tearDown(self) -> None:
        self._config_mod.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


class QueueMigrationTest(_QueueFoundationTestBase):
    def test_queue_migration_is_recorded_once_and_idempotent(self) -> None:
        from angemedia_gateway.db.migrations import QUEUE_FOUNDATION_VERSION

        init_db()
        init_db()
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
                (QUEUE_FOUNDATION_VERSION,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_jobs_and_queue_tables_have_formal_foundation_schema(self) -> None:
        expected_job_columns = {
            "stage", "payload_schema_version", "priority", "scheduled_at",
            "next_retry_at", "attempt_count", "max_attempts", "claim_token",
            "claim_expires_at", "worker_kind", "provider_status",
            "cancel_requested_at", "version",
        }
        with self._conn() as conn:
            job_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertTrue(expected_job_columns.issubset(job_columns))
        self.assertTrue({"job_events", "job_attempts", "job_dispatches"}.issubset(tables))
        with self._conn() as conn:
            dispatch_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(job_dispatches)").fetchall()
            }
        self.assertTrue(
            {"claim_token", "claim_expires_at", "broker_message_id", "version"}.issubset(
                dispatch_columns
            )
        )

    def test_legacy_job_survives_migration_with_queue_defaults(self) -> None:
        legacy_path = Path(self._tmp_dir) / "legacy.db"
        conn = sqlite3.connect(str(legacy_path), isolation_level=None)
        try:
            conn.executescript(
                """
                CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE jobs(
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    request_hash TEXT,
                    request_hash_version INTEGER
                );
                INSERT INTO jobs(id,kind,status,created_at,updated_at)
                VALUES('legacy-job','image','succeeded','2026-01-01T00:00:00+00:00','2026-01-01T00:00:01+00:00');
                """
            )
            from angemedia_gateway.db.migrations import run_migrations

            run_migrations(conn)
            row = conn.execute(
                "SELECT id,stage,payload_schema_version,attempt_count,max_attempts,version "
                "FROM jobs WHERE id='legacy-job'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(row), ("legacy-job", "finalize", 1, 0, 3, 0))

    def test_init_db_upgrades_pre_queue_database_without_recreating_jobs(self) -> None:
        legacy_path = Path(self._tmp_dir) / "legacy-init.db"
        conn = sqlite3.connect(str(legacy_path))
        try:
            conn.executescript(
                """
                CREATE TABLE jobs(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    provider TEXT, model TEXT, prompt TEXT, input_json TEXT, output_json TEXT,
                    error_code TEXT, error_message TEXT, external_task_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                    completed_at TEXT, duration_ms INTEGER, request_hash TEXT,
                    request_hash_version INTEGER, error_category TEXT, human_hint TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0, gateway_stage TEXT
                );
                INSERT INTO jobs(id,kind,status,created_at,updated_at)
                VALUES('legacy-init-job','video','running','2026-01-01T00:00:00+00:00','2026-01-01T00:00:01+00:00');
                """
            )
            conn.commit()
        finally:
            conn.close()
        self._config_mod.DB_FILE = legacy_path

        init_db()

        with sqlite3.connect(str(legacy_path)) as migrated:
            row = migrated.execute(
                "SELECT id,status,stage,version FROM jobs WHERE id='legacy-init-job'"
            ).fetchone()
            marker = migrated.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version='queue_foundation_v1'"
            ).fetchone()[0]
        self.assertEqual(row, ("legacy-init-job", "running", "admitted", 0))
        self.assertEqual(marker, 1)


class JobAdmissionTest(_QueueFoundationTestBase):
    def _admit(self, request_hash: str):
        from angemedia_gateway.services.job_admission import JobAdmissionService

        return JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash=request_hash,
            request_hash_version=1,
            payload={"prompt": "safe prompt", "asset_id": "asset-123"},
            model="agnes-image",
        )

    def test_admission_writes_job_event_and_dispatch(self) -> None:
        result = self._admit("a" * 64)
        self.assertTrue(result.created)
        with self._conn() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (result.job["id"],)).fetchone()
            event = conn.execute("SELECT * FROM job_events WHERE job_id = ?", (result.job["id"],)).fetchone()
            dispatch = conn.execute("SELECT * FROM job_dispatches WHERE job_id = ?", (result.job["id"],)).fetchone()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["stage"], "image_generate")
        self.assertEqual(event["event_type"], "admitted")
        self.assertEqual(dispatch["status"], "pending")

    def test_dispatch_failure_rolls_back_job_and_event(self) -> None:
        with self._conn() as conn:
            conn.execute(
                "CREATE TRIGGER reject_dispatch BEFORE INSERT ON job_dispatches "
                "BEGIN SELECT RAISE(ABORT, 'dispatch rejected'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self._admit("b" * 64)
        with self._conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0], 0)

    def test_repeated_active_hash_produces_one_job_and_one_dispatch(self) -> None:
        first = self._admit("c" * 64)
        second = self._admit("c" * 64)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.job["id"], first.job["id"])
        with self._conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_dispatches").fetchone()[0], 1)

    def test_database_unique_index_rejects_direct_active_duplicate(self) -> None:
        from angemedia_gateway.repositories.jobs import create_job

        create_job(
            kind="image", status="running", request_hash="3" * 64,
            request_hash_version=1,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            create_job(
                kind="image", status="queued", request_hash="3" * 64,
                request_hash_version=1,
            )

    def test_concurrent_active_hash_is_atomically_deduplicated(self) -> None:
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def admit() -> None:
            try:
                barrier.wait()
                results.append(self._admit("d" * 64))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=admit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for result in results if result.created), 1)
        self.assertEqual(len({result.job["id"] for result in results}), 1)

    def test_outbox_claim_publish_contract_is_compare_and_swap(self) -> None:
        from angemedia_gateway.repositories.job_dispatches import (
            OutboxClaimLost,
            claim_pending_dispatches,
            mark_dispatch_published,
        )

        admitted = self._admit("2" * 64)
        claimed = claim_pending_dispatches(
            claim_token="dispatcher-a", claim_expires_at="2999-01-01T00:00:00+00:00"
        )
        self.assertEqual([item["id"] for item in claimed], [admitted.dispatch["id"]])
        self.assertEqual(
            claim_pending_dispatches(
                claim_token="dispatcher-b", claim_expires_at="2999-01-01T00:00:00+00:00"
            ),
            [],
        )
        with self.assertRaises(OutboxClaimLost):
            mark_dispatch_published(admitted.dispatch["id"], claim_token="dispatcher-b")
        published = mark_dispatch_published(
            admitted.dispatch["id"], claim_token="dispatcher-a", broker_message_id="broker-1"
        )
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["broker_message_id"], "broker-1")


class StrictJobLifecycleTest(_QueueFoundationTestBase):
    def test_valid_transition_increments_version_and_appends_event(self) -> None:
        from angemedia_gateway.services.job_admission import JobAdmissionService
        from angemedia_gateway.services.job_lifecycle import JobLifecycle

        admitted = JobAdmissionService().admit(
            kind="video", stage="video_submit", request_hash="e" * 64,
            request_hash_version=1, payload={"prompt": "video"},
        )
        updated = JobLifecycle().transition(
            admitted.job["id"], expected_version=0, status="running", stage="video_submit"
        )
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["version"], 1)
        with self._conn() as conn:
            event_types = [
                row[0] for row in conn.execute(
                    "SELECT event_type FROM job_events WHERE job_id=? ORDER BY created_at,id",
                    (admitted.job["id"],),
                ).fetchall()
            ]
        self.assertEqual(event_types, ["admitted", "status_changed"])

    def test_terminal_to_running_is_rejected(self) -> None:
        from angemedia_gateway.repositories.jobs import create_job
        from angemedia_gateway.services.job_lifecycle import InvalidJobTransition, JobLifecycle

        job = create_job(kind="image", status="succeeded")
        with self.assertRaises(InvalidJobTransition):
            JobLifecycle().transition(job["id"], expected_version=0, status="running")

    def test_stale_version_is_rejected(self) -> None:
        from angemedia_gateway.repositories.jobs import create_job
        from angemedia_gateway.services.job_lifecycle import JobLifecycle, StaleJobVersion

        job = create_job(kind="image", status="queued")
        lifecycle = JobLifecycle()
        lifecycle.transition(job["id"], expected_version=0, status="running")
        with self.assertRaises(StaleJobVersion):
            lifecycle.transition(job["id"], expected_version=0, status="failed")

    def test_stage_cannot_move_backwards(self) -> None:
        from angemedia_gateway.services.job_admission import JobAdmissionService
        from angemedia_gateway.services.job_lifecycle import InvalidJobTransition, JobLifecycle

        admitted = JobAdmissionService().admit(
            kind="video", stage="video_submit", request_hash="1" * 64,
            request_hash_version=1, payload={"prompt": "video"},
        )
        lifecycle = JobLifecycle()
        polling = lifecycle.transition(
            admitted.job["id"], expected_version=0, status="running", stage="video_poll"
        )
        with self.assertRaises(InvalidJobTransition):
            lifecycle.transition(
                admitted.job["id"], expected_version=polling["version"],
                status="running", stage="video_submit",
            )

    def test_repository_failure_is_not_swallowed(self) -> None:
        from angemedia_gateway.services.job_lifecycle import JobLifecycle

        def fail_update(*args, **kwargs):
            raise RuntimeError("database unavailable")

        lifecycle = JobLifecycle(update_job_func=fail_update)
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            lifecycle.transition("job-id", expected_version=0, status="running")


class QueuePersistenceSafetyTest(_QueueFoundationTestBase):
    def _unsafe_payload(self) -> dict:
        return {
            "prompt": "safe",
            "api_key": "sk-super-secret-123456789",
            "Authorization": "Bearer bearer-secret-token-123456",
            "provider_body": {"raw": "provider-secret-body"},
            "bytes": b"raw-image-secret",
            "data_url": "data:image/png;base64,RAWBASE64SECRET",
            "signed_url": "https://cdn.example/image.png?X-Amz-Signature=signed-secret",
            "local_path": r"C:\\Users\\private\\secret.png",
            "message": (
                "provider failed at https://cdn.example/image.png?token=embedded-signed-secret "
                r"while reading C:\\Users\\private\\embedded-secret.png"
            ),
        }

    def _assert_safe(self, *values) -> None:
        rendered = " ".join(str(value) for value in values)
        for forbidden in (
            "sk-super-secret", "bearer-secret", "provider-secret-body",
            "raw-image-secret", "RAWBASE64SECRET", "signed-secret",
            "embedded-signed-secret", "embedded-secret.png", r"C:\\Users\\private",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_admission_and_outbox_payloads_are_sanitized(self) -> None:
        from angemedia_gateway.services.job_admission import JobAdmissionService

        result = JobAdmissionService().admit(
            kind="image", stage="image_generate", request_hash="f" * 64,
            request_hash_version=1, payload=self._unsafe_payload(),
        )
        with self._conn() as conn:
            row = conn.execute(
                "SELECT j.input_json,d.payload_json,e.payload_json "
                "FROM jobs j JOIN job_dispatches d ON d.job_id=j.id "
                "JOIN job_events e ON e.job_id=j.id WHERE j.id=?",
                (result.job["id"],),
            ).fetchone()
        self._assert_safe(*row)

    def test_event_and_attempt_repositories_sanitize_recursively(self) -> None:
        from angemedia_gateway.repositories.job_attempts import create_job_attempt
        from angemedia_gateway.repositories.job_events import append_job_event
        from angemedia_gateway.repositories.jobs import create_job

        job = create_job(kind="image")
        append_job_event(job["id"], "diagnostic", self._unsafe_payload())
        create_job_attempt(
            job_id=job["id"], attempt_number=1, stage="image_generate",
            worker_kind="image", detail=self._unsafe_payload(),
            error_message="Bearer bearer-secret-token-123456",
        )
        with self._conn() as conn:
            event = conn.execute("SELECT payload_json FROM job_events WHERE job_id=?", (job["id"],)).fetchone()[0]
            attempt = conn.execute(
                "SELECT detail_json,error_message FROM job_attempts WHERE job_id=?", (job["id"],)
            ).fetchone()
        self._assert_safe(event, *attempt)

    def test_job_detail_api_sanitizes_legacy_signed_url_and_private_path(self) -> None:
        os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "queue-foundation-test-password")
        from angemedia_gateway.repositories.jobs import create_job
        from angemedia_gateway.routes.jobs import get_job_endpoint

        job = create_job(
            kind="image",
            input_json=json.dumps(self._unsafe_payload(), default=str),
            output_json='{"url":"https://cdn.example/x?token=signed-secret"}',
        )
        response = asyncio.run(get_job_endpoint(job["id"]))
        self._assert_safe(response)

    def test_request_hash_rejects_non_digest_secret_input(self) -> None:
        from angemedia_gateway.repositories.jobs import create_job

        with self.assertRaises(ValueError):
            create_job(
                kind="image", request_hash="sk-super-secret-123456789",
                request_hash_version=1,
            )

    def test_job_detail_api_hides_internal_claim_token(self) -> None:
        os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "queue-foundation-test-password")
        from angemedia_gateway.repositories.jobs import create_job
        from angemedia_gateway.routes.jobs import get_job_endpoint

        job = create_job(kind="image", claim_token="internal-lease-token")
        response = asyncio.run(get_job_endpoint(job["id"]))
        self.assertNotIn("claim_token", response["data"])
        self.assertNotIn("internal-lease-token", repr(response))

    def test_all_external_job_text_columns_are_sanitized(self) -> None:
        from angemedia_gateway.repositories.jobs import create_job

        job = create_job(
            kind="image",
            provider="Bearer provider-secret-token-123456",
            model="sk-model-secret-123456789",
            external_task_id="av-task-secret-123456789",
            error_code="sk-error-secret-123456789",
            gateway_stage="Bearer stage-secret-token-123456",
            worker_kind="sk-worker-secret-123456789",
        )
        self._assert_safe(job)
        rendered = repr(job)
        for forbidden in (
            "provider-secret-token", "model-secret", "task-secret",
            "error-secret", "stage-secret-token", "worker-secret",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()

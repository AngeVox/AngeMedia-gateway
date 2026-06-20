"""Celery worker entrypoint and provider-free runtime skeleton contracts."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class WorkerRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="worker-runtime-test-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        import angemedia_gateway.config as C

        self._orig_db = C.DB_FILE
        self._config = C
        C.DB_FILE = self.db_path
        init_db()

    def tearDown(self) -> None:
        self._config.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _admit(self):
        from angemedia_gateway.services.job_admission import JobAdmissionService

        return JobAdmissionService().admit(
            kind="image", stage="image_generate", request_hash="d" * 64,
            request_hash_version=1, payload={"prompt": "safe"},
        )

    def _message(self, admitted):
        from angemedia_gateway.queue.messages import JobStageMessage

        return JobStageMessage(
            job_id=admitted.job["id"],
            stage="image_generate",
            attempt=1,
            dispatch_id=admitted.dispatch["id"],
            trace_id=admitted.dispatch["id"],
        )

    def test_unimplemented_stage_records_failed_attempt_without_succeeding_job(self) -> None:
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        admitted = self._admit()
        result = WorkerRuntime().handle(self._message(admitted).to_dict())

        self.assertEqual(result["status"], "not_implemented")
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempt_count"], 1)
        self.assertEqual(job["worker_kind"], "celery")
        attempts = list_job_attempts(job["id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(attempts[0]["error_code"], "worker_stage_not_implemented")
        events = list_job_events(job["id"])
        self.assertIn("worker_stage_not_implemented", [item["event_type"] for item in events])

    def test_duplicate_delivery_is_idempotent(self) -> None:
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        admitted = self._admit()
        runtime = WorkerRuntime()
        runtime.handle(self._message(admitted).to_dict())
        duplicate = runtime.handle(self._message(admitted).to_dict())
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(len(list_job_attempts(admitted.job["id"])), 1)

    def test_invalid_message_is_rejected_without_secret_echo(self) -> None:
        from angemedia_gateway.queue.messages import InvalidQueueMessage
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        secret = "sk-worker-message-secret-123456789"
        with self.assertRaises(InvalidQueueMessage) as raised:
            WorkerRuntime().handle({"raw_provider_body": secret})
        self.assertNotIn(secret, str(raised.exception))

    def test_attempt_above_job_limit_is_rejected_without_recording_attempt(self) -> None:
        from angemedia_gateway.queue.messages import JobStageMessage
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.services.worker_runtime import WorkerJobNotExecutable, WorkerRuntime

        admitted = self._admit()
        message = JobStageMessage(
            job_id=admitted.job["id"], stage="image_generate", attempt=4,
            dispatch_id=admitted.dispatch["id"], trace_id=admitted.dispatch["id"],
        )
        with self.assertRaises(WorkerJobNotExecutable):
            WorkerRuntime().handle(message.to_dict())
        self.assertEqual(list_job_attempts(admitted.job["id"]), [])
        self.assertIn(
            "worker_attempt_limit_rejected",
            [item["event_type"] for item in list_job_events(admitted.job["id"])],
        )

    def test_worker_task_is_a_thin_runtime_wrapper(self) -> None:
        from angemedia_gateway.worker.tasks import execute_job_stage

        runtime = Mock()
        runtime.handle.return_value = {"status": "not_implemented"}
        message = {"job_id": "a" * 32}
        with patch("angemedia_gateway.worker.tasks._get_runtime", return_value=runtime):
            result = execute_job_stage.run(message)
        runtime.handle.assert_called_once_with(message)
        self.assertEqual(result, {"status": "not_implemented"})

    def test_worker_modules_do_not_import_providers_or_adapters(self) -> None:
        paths = [
            ROOT / "scripts" / "angemedia_gateway" / "services" / "worker_runtime.py",
            ROOT / "scripts" / "angemedia_gateway" / "worker" / "tasks.py",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("providers", text)
            self.assertNotIn("adapters", text)
            self.assertNotIn("mark_succeeded", text)


if __name__ == "__main__":
    unittest.main()

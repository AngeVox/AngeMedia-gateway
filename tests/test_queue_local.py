"""SQLite-backed local queue backend contracts."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class LocalQueueSettingsTest(unittest.TestCase):
    def test_local_backend_does_not_require_redis_url(self) -> None:
        from angemedia_gateway.queue.settings import QueueSettings

        settings = QueueSettings(
            enabled=True,
            backend="local",
            broker_url="not-a-redis-url",
        )
        self.assertEqual(settings.backend, "local")
        self.assertEqual(settings.safe_summary()["backend"], "local")

    def test_unknown_enabled_backend_is_rejected(self) -> None:
        from angemedia_gateway.queue.settings import QueueSettings

        with self.assertRaises(RuntimeError):
            QueueSettings(enabled=True, backend="unknown")


class LocalQueueBackendTest(unittest.TestCase):
    def _message(self):
        from angemedia_gateway.queue.messages import JobStageMessage

        return JobStageMessage(
            job_id="a" * 32,
            stage="image_generate",
            attempt=1,
            dispatch_id="b" * 32,
            trace_id="c" * 32,
        )

    def test_publish_executes_runtime_synchronously(self) -> None:
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings, WORKER_TASK_NAME

        runtime = Mock()
        runtime.handle.return_value = {"status": "succeeded"}
        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=runtime,
        )
        message = self._message()

        message_id = backend.publish(topic=WORKER_TASK_NAME, message=message)

        runtime.handle.assert_called_once_with(message.to_dict())
        self.assertEqual(message_id, f"local-{message.dispatch_id}")
        backend.healthcheck()
        self.assertIsNone(backend.revoke(message_id))

    def test_publish_rejects_unknown_topic_and_raw_dict(self) -> None:
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings, WORKER_TASK_NAME

        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=Mock(),
        )
        with self.assertRaises(ValueError):
            backend.publish(topic="unapproved.task", message=self._message())
        with self.assertRaises(TypeError):
            backend.publish(topic=WORKER_TASK_NAME, message={"job_id": "secret"})

    def test_backend_factory_selects_local_without_celery_broker(self) -> None:
        from angemedia_gateway.queue.backend_factory import create_queue_backend
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings

        backend = create_queue_backend(QueueSettings(enabled=True, backend="local"))
        self.assertIsInstance(backend, LocalQueueBackend)


class LocalQueueDispatchIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="local-queue-test-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        import angemedia_gateway.config as C

        self._orig_db = C.DB_FILE
        self._config = C
        C.DB_FILE = self.db_path
        init_db()

    def tearDown(self) -> None:
        self._config.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_dispatcher_delivers_pending_outbox_through_local_runtime(self) -> None:
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch
        from angemedia_gateway.services.job_admission import JobAdmissionService
        from angemedia_gateway.services.job_dispatcher import JobDispatcher

        admitted = JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="a" * 64,
            request_hash_version=1,
            payload={"prompt": "safe"},
        )
        runtime = Mock()
        runtime.handle.return_value = {"status": "succeeded"}
        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=runtime,
        )
        dispatcher = JobDispatcher(
            queue_backend=backend,
            batch_size=1,
            lease_seconds=30,
            max_attempts=3,
            retry_base_seconds=1,
        )

        result = dispatcher.dispatch_once()

        self.assertEqual((result.claimed, result.published, result.failed), (1, 1, 0))
        runtime.handle.assert_called_once()
        dispatch = get_job_dispatch(admitted.dispatch["id"])
        self.assertEqual(dispatch["status"], "published")
        self.assertEqual(dispatch["broker_message_id"], f"local-{admitted.dispatch['id']}")

    def test_unhandled_image_error_is_terminal_without_provider_redelivery(self) -> None:
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.job_admission import JobAdmissionService
        from angemedia_gateway.services.job_dispatcher import JobDispatcher

        admitted = JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="b" * 64,
            request_hash_version=1,
            payload={"prompt": "safe"},
        )
        runtime = Mock()
        runtime.handle.side_effect = RuntimeError("Bearer local-worker-secret-123456")
        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=runtime,
        )
        dispatcher = JobDispatcher(
            queue_backend=backend,
            batch_size=1,
            lease_seconds=30,
            max_attempts=3,
            retry_base_seconds=1,
        )

        first = dispatcher.dispatch_once()
        second = dispatcher.dispatch_once()

        self.assertEqual((first.published, first.retried, first.failed), (1, 0, 0))
        self.assertEqual(second.claimed, 0)
        runtime.handle.assert_called_once()
        dispatch = get_job_dispatch(admitted.dispatch["id"])
        self.assertEqual(dispatch["status"], "published")
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["stage"], "finalize")
        self.assertEqual(job["error_code"], "local_worker_unhandled_error")
        self.assertEqual(job["retryable"], 0)
        rendered = repr(list_job_events(admitted.job["id"])) + repr(job)
        self.assertIn("local_worker_terminal_failure", rendered)
        self.assertNotIn("local-worker-secret", rendered)

    def test_unhandled_video_poll_error_schedules_bounded_local_recovery(self) -> None:
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings
        from angemedia_gateway.repositories.job_dispatches import list_job_dispatches
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.job_admission import JobAdmissionService
        from angemedia_gateway.services.job_dispatcher import JobDispatcher

        admitted = JobAdmissionService().admit(
            kind="video",
            stage="video_poll",
            request_hash="c" * 64,
            request_hash_version=1,
            payload={"prompt": "safe"},
            max_attempts=3,
        )
        runtime = Mock()
        runtime.handle.side_effect = RuntimeError("Bearer recover-secret-123456")
        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=runtime,
        )
        dispatcher = JobDispatcher(
            queue_backend=backend,
            batch_size=1,
            lease_seconds=30,
            max_attempts=3,
            retry_base_seconds=1,
        )

        first = dispatcher.dispatch_once()

        self.assertEqual(first.published, 1)
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["stage"], "video_poll")
        self.assertEqual(job["retryable"], 1)
        dispatches = list_job_dispatches(admitted.job["id"])
        self.assertEqual(len(dispatches), 2)
        self.assertEqual(dispatches[0]["status"], "published")
        self.assertEqual(dispatches[1]["status"], "pending")
        rendered = repr(list_job_events(admitted.job["id"])) + repr(job)
        self.assertIn("local_worker_recovery_scheduled", rendered)
        self.assertNotIn("recover-secret", rendered)

    def test_ambiguous_duplicate_after_restart_is_failed_not_left_running(self) -> None:
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.job_admission import JobAdmissionService
        from angemedia_gateway.services.job_dispatcher import JobDispatcher

        admitted = JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="d" * 64,
            request_hash_version=1,
            payload={"prompt": "safe"},
        )
        runtime = Mock()
        runtime.handle.return_value = {"status": "duplicate"}
        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=runtime,
        )
        dispatcher = JobDispatcher(
            queue_backend=backend,
            batch_size=1,
            lease_seconds=30,
            max_attempts=3,
            retry_base_seconds=1,
        )

        result = dispatcher.dispatch_once()

        self.assertEqual(result.published, 1)
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["stage"], "finalize")
        self.assertEqual(job["error_code"], "local_worker_ambiguous_delivery")
        self.assertEqual(job["retryable"], 0)

    def test_local_process_lock_rejects_second_consumer(self) -> None:
        from angemedia_gateway.queue.local_lock import LocalQueueAlreadyRunning, LocalQueueProcessLock

        lock_path = self.db_path.with_name("local-queue-test.lock")
        with LocalQueueProcessLock(lock_path):
            with self.assertRaises(LocalQueueAlreadyRunning):
                with LocalQueueProcessLock(lock_path):
                    pass

    def test_default_registry_records_local_worker_kind(self) -> None:
        from angemedia_gateway.services.job_stage_registry import default_job_stage_registry

        with patch.dict(os.environ, {"QUEUE_SMOKE_FAKE_PROVIDERS": "true"}, clear=False):
            registry = default_job_stage_registry(worker_kind="local")
        image_handler = registry.get("image_generate")
        video_handler = registry.get("video_submit")
        self.assertIsNotNone(image_handler)
        self.assertIsNotNone(video_handler)
        self.assertEqual(image_handler.__self__.worker_kind, "local")
        self.assertEqual(video_handler.__self__.worker_kind, "local")


class LocalQueueEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="local-queue-e2e-")
        self.db_path = Path(self._tmp_dir) / "test.db"
        self.output_dir = Path(self._tmp_dir) / "generated"
        self.upload_dir = Path(self._tmp_dir) / "uploads"
        import angemedia_gateway.config as C

        self._config = C
        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_public = C.PUBLIC_BASE_URL
        C.DB_FILE = self.db_path
        C.OUTPUT_DIR = self.output_dir
        C.UPLOAD_DIR = self.upload_dir
        C.PUBLIC_BASE_URL = "http://127.0.0.1:9892"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        init_db()

    def tearDown(self) -> None:
        self._config.DB_FILE = self._orig_db
        self._config.OUTPUT_DIR = self._orig_output
        self._config.UPLOAD_DIR = self._orig_upload
        self._config.PUBLIC_BASE_URL = self._orig_public
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _runtime_and_dispatcher(self):
        from angemedia_gateway.queue.local_backend import LocalQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_dispatcher import JobDispatcher
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.queue_smoke import (
            FakeQueueSmokeImageExecutor,
            FakeQueueSmokeVideoExecutor,
            FakeQueueSmokeVideoImporter,
        )
        from angemedia_gateway.services.video_job_worker import VideoJobWorker
        from angemedia_gateway.services.video_polling import VideoPipelinePolicy
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        policy = VideoPipelinePolicy(
            poll_interval_seconds=0.1,
            max_poll_seconds=60,
            max_attempts=10,
            max_backoff_seconds=0.1,
            now_func=lambda: datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        image_worker = ImageJobWorker(
            executor=FakeQueueSmokeImageExecutor(),
            worker_kind="local",
        )
        video_worker = VideoJobWorker(
            executor=FakeQueueSmokeVideoExecutor(),
            asset_importer=FakeQueueSmokeVideoImporter(),
            policy=policy,
            worker_kind="local",
        )
        runtime = WorkerRuntime(
            registry=JobStageRegistry({
                "image_generate": image_worker.handle,
                "video_submit": video_worker.handle_submit,
                "video_poll": video_worker.handle_poll,
                "asset_import": video_worker.handle_asset_import,
            }),
            worker_kind="local",
        )
        backend = LocalQueueBackend(
            settings=QueueSettings(enabled=True, backend="local"),
            runtime=runtime,
        )
        dispatcher = JobDispatcher(
            queue_backend=backend,
            batch_size=1,
            lease_seconds=30,
            max_attempts=3,
            retry_base_seconds=1,
        )
        return policy, dispatcher

    def _asset_count(self, job_id: str) -> int:
        import sqlite3

        with sqlite3.connect(str(self.db_path)) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM assets WHERE job_id=?",
                (job_id,),
            ).fetchone()[0])

    def test_image_job_completes_without_redis_or_celery(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.schemas import ImageRequest
        from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService

        _, dispatcher = self._runtime_and_dispatcher()
        with patch.dict(os.environ, {"QUEUE_SMOKE_FAKE_PROVIDERS": "true"}, clear=False):
            admitted = ImageJobAdmissionService().submit(
                ImageRequest(prompt="local queue image smoke", model="queue-smoke-image")
            )
            result = dispatcher.dispatch_once()

        self.assertEqual(result.published, 1)
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["worker_kind"], "local")
        self.assertEqual(self._asset_count(job["id"]), 1)
        self.assertTrue(any(self.output_dir.glob("queue-smoke-image-*.png")))

    def test_video_pipeline_completes_all_stages_without_redis_or_celery(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.schemas import VideoRequest
        from angemedia_gateway.services.video_job_admission import VideoJobAdmissionService

        policy, dispatcher = self._runtime_and_dispatcher()
        with patch.dict(os.environ, {"QUEUE_SMOKE_FAKE_PROVIDERS": "true"}, clear=False):
            admitted = VideoJobAdmissionService(policy=policy).submit(
                VideoRequest(
                    prompt="local queue video smoke",
                    model="agnes-video-v2.0",
                    wait_for_completion=False,
                )
            )
            batches = [dispatcher.dispatch_once() for _ in range(3)]

        self.assertEqual([batch.published for batch in batches], [1, 1, 1])
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["stage"], "finalize")
        self.assertEqual(job["worker_kind"], "local")
        self.assertTrue(job["external_task_id"])
        self.assertEqual(self._asset_count(job["id"]), 1)
        self.assertTrue(any(self.output_dir.glob("queue-smoke-video-*.mp4")))


if __name__ == "__main__":
    unittest.main()

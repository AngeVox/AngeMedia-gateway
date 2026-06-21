"""Queued video admission and durable worker pipeline contracts."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from angemedia_gateway.state import init_db


class _FakeVideoExecutor:
    def __init__(self, *, submit=None, polls=None, submit_error=None, poll_error=None) -> None:
        self.submit_result = submit
        self.poll_results = list(polls or [])
        self.submit_error = submit_error
        self.poll_error = poll_error
        self.submit_calls = 0
        self.poll_calls = 0

    async def submit(self, request):
        self.submit_calls += 1
        if self.submit_error:
            raise self.submit_error
        return self.submit_result

    async def poll(self, task_id):
        self.poll_calls += 1
        if self.poll_error:
            raise self.poll_error
        return self.poll_results.pop(0)


class _FakeAssetImporter:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def import_completed(self, task_id, poll_result):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class VideoJobQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="video-job-queue-test-")
        self.root = Path(self.tmp_dir)
        import angemedia_gateway.config as C

        self.config = C
        self.original = (C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.PUBLIC_BASE_URL)
        C.DB_FILE = self.root / "test.db"
        C.OUTPUT_DIR = self.root / "generated"
        C.UPLOAD_DIR = self.root / "uploads"
        C.PUBLIC_BASE_URL = "http://testserver"
        C.OUTPUT_DIR.mkdir()
        C.UPLOAD_DIR.mkdir()
        init_db()

    def tearDown(self) -> None:
        C = self.config
        C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.PUBLIC_BASE_URL = self.original
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def admission(self, *, enabled=True):
        from angemedia_gateway.services.video_job_admission import VideoJobAdmissionService

        return VideoJobAdmissionService(provider_enabled_func=lambda _provider: enabled)

    def submit(self, **overrides):
        from angemedia_gateway.schemas import VideoRequest

        payload = {
            "prompt": "queued video cat",
            "model": "agnes-video-v2.0",
            "wait_for_completion": False,
        }
        payload.update(overrides)
        return self.admission().submit(VideoRequest(**payload))

    def next_dispatch(self, job_id: str):
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch

        with sqlite3.connect(str(self.config.DB_FILE)) as conn:
            row = conn.execute(
                "SELECT id FROM job_dispatches WHERE job_id=? AND status='pending' "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return get_job_dispatch(row[0])

    @staticmethod
    def message(dispatch):
        from angemedia_gateway.queue.messages import JobStageMessage

        payload = json.loads(dispatch["payload_json"])
        return JobStageMessage(
            job_id=dispatch["job_id"],
            stage=payload["stage"],
            attempt=payload["attempt"],
            dispatch_id=dispatch["id"],
            trace_id=dispatch["id"],
        )

    def runtime(self, worker):
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        return WorkerRuntime(registry=JobStageRegistry({
            "video_submit": worker.handle_submit,
            "video_poll": worker.handle_poll,
            "asset_import": worker.handle_asset_import,
        }))

    def worker(self, executor, importer=None, **policy):
        from angemedia_gateway.services.video_job_worker import VideoJobWorker
        from angemedia_gateway.services.video_polling import VideoPipelinePolicy

        defaults = {
            "poll_interval_seconds": 2,
            "max_poll_seconds": 120,
            "max_attempts": 20,
            "now_func": lambda: datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc),
        }
        defaults.update(policy)
        return VideoJobWorker(
            executor=executor,
            asset_importer=importer or _FakeAssetImporter(),
            policy=VideoPipelinePolicy(**defaults),
        )

    def test_admission_writes_job_and_submit_dispatch_without_provider(self) -> None:
        admitted = self.submit()
        self.assertTrue(admitted.created)
        self.assertEqual(admitted.job["status"], "queued")
        self.assertEqual(admitted.job["stage"], "video_submit")
        payload = json.loads(admitted.dispatch["payload_json"])
        self.assertEqual(payload["stage"], "video_submit")
        self.assertEqual(payload["attempt"], 1)
        self.assertNotIn("prompt", payload)
        self.assertNotIn("input_json", payload)

    def test_active_duplicate_reuses_job_and_terminal_allows_new(self) -> None:
        from angemedia_gateway.services.job_lifecycle import JobLifecycle

        first = self.submit()
        duplicate = self.submit()
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.job["id"], first.job["id"])
        JobLifecycle().mark_failed(
            first.job["id"], kind="video", error_code="test", error_message="test",
            completed_at="2026-06-21T10:00:00+00:00",
        )
        later = self.submit()
        self.assertTrue(later.created)
        self.assertNotEqual(later.job["id"], first.job["id"])

    def test_admission_rejects_unsafe_inputs_and_disabled_provider(self) -> None:
        from angemedia_gateway.repositories.jobs import list_jobs
        from angemedia_gateway.schemas import VideoRequest
        from angemedia_gateway.services.video_execution import VideoProviderDisabled

        for image in (
            "data:image/png;base64,AAAA", "https://example.com/ref.png",
            "C:/private/ref.png", "/uploads/../private.png",
        ):
            with self.subTest(image=image), self.assertRaises(ValueError):
                self.submit(image=image)
        with self.assertRaises(ValueError):
            self.submit(extra_body={"raw": "body"})
        with self.assertRaises(ValueError):
            self.submit(wait_for_completion=True)
        with self.assertRaises(VideoProviderDisabled):
            self.admission(enabled=False).submit(VideoRequest(prompt="disabled"))
        self.assertEqual(list_jobs(), [])

    def test_submit_calls_provider_once_stores_task_and_schedules_poll(self) -> None:
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoSubmitResult

        admitted = self.submit()
        executor = _FakeVideoExecutor(submit=VideoSubmitResult(
            task_id="video-task-001", provider_status="queued", duration_ms=8,
            started_at="2026-06-21T10:00:00+00:00",
        ))
        runtime = self.runtime(self.worker(executor))
        result = runtime.handle(self.message(admitted.dispatch).to_dict())

        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(executor.submit_calls, 1)
        job = get_job(admitted.job["id"])
        self.assertEqual(job["stage"], "video_poll")
        self.assertEqual(job["external_task_id"], "video-task-001")
        self.assertEqual(job["provider_status"], "queued")
        self.assertEqual(list_job_attempts(job["id"])[0]["status"], "succeeded")
        poll_dispatch = self.next_dispatch(job["id"])
        self.assertEqual(json.loads(poll_dispatch["payload_json"])["stage"], "video_poll")
        persisted = repr((job, poll_dispatch))
        self.assertNotIn("raw_provider", persisted)
        self.assertNotIn("signed", persisted)

        replay = runtime.handle(self.message(admitted.dispatch).to_dict())
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(executor.submit_calls, 1)

    def test_submit_replay_with_external_task_id_never_calls_provider(self) -> None:
        admitted = self.submit()
        with sqlite3.connect(str(self.config.DB_FILE)) as conn:
            conn.execute(
                "UPDATE jobs SET external_task_id='existing-task',provider_status='queued' WHERE id=?",
                (admitted.job["id"],),
            )
        executor = _FakeVideoExecutor()
        result = self.runtime(self.worker(executor)).handle(
            self.message(admitted.dispatch).to_dict()
        )
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(executor.submit_calls, 0)

    def test_submit_dispatch_failure_persists_task_and_resumes_without_resubmit(self) -> None:
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoSubmitResult

        admitted = self.submit(prompt="submit recovery")
        executor = _FakeVideoExecutor(submit=VideoSubmitResult(
            "recovery-task", "queued", 1, "2026-06-21T10:00:00+00:00"
        ))
        runtime = self.runtime(self.worker(executor))
        with patch(
            "angemedia_gateway.services.video_job_worker.create_job_dispatch",
            side_effect=sqlite3.OperationalError("outbox insert failed"),
        ), self.assertRaises(sqlite3.OperationalError):
            runtime.handle(self.message(admitted.dispatch).to_dict())

        job = get_job(admitted.job["id"])
        self.assertEqual(job["stage"], "video_submit")
        self.assertEqual(job["external_task_id"], "recovery-task")
        self.assertEqual(list_job_attempts(job["id"])[0]["status"], "running")

        resumed = runtime.handle(self.message(admitted.dispatch).to_dict())
        self.assertEqual(resumed["status"], "scheduled")
        self.assertEqual(executor.submit_calls, 1)
        self.assertEqual(get_job(job["id"])["stage"], "video_poll")
        self.assertEqual(list_job_attempts(job["id"])[0]["status"], "succeeded")

    def test_submit_failure_is_ambiguous_terminal_and_sanitized(self) -> None:
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.repositories.jobs import get_job

        admitted = self.submit()
        secret = "sk-submit-secret-123456789"
        executor = _FakeVideoExecutor(
            submit_error=RuntimeError(
                f"Authorization Bearer {secret} raw body https://cdn.example/x?token=signed-secret"
            )
        )
        handled = self.runtime(self.worker(executor)).handle(
            self.message(admitted.dispatch).to_dict()
        )
        job = get_job(admitted.job["id"])
        state = repr((job, list_job_attempts(job["id"]), list_job_events(job["id"])))
        self.assertEqual(handled["status"], "failed")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_code"], "video_submit_ambiguous")
        self.assertEqual(job["retryable"], 0)
        self.assertNotIn(secret, state)
        self.assertNotIn("signed-secret", state)

    def test_worker_disabled_provider_never_calls_adapter(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoExecutionService

        class Provider:
            submit_calls = 0

            async def submit_task(self, request):
                self.submit_calls += 1
                return {"task_id": "must-not-run"}

        admitted = self.submit()
        provider = Provider()
        executor = VideoExecutionService(
            provider=provider,
            provider_enabled_func=lambda _provider: False,
        )
        handled = self.runtime(self.worker(executor)).handle(
            self.message(admitted.dispatch).to_dict()
        )
        self.assertEqual(handled["status"], "failed")
        self.assertEqual(provider.submit_calls, 0)
        self.assertEqual(get_job(admitted.job["id"])["error_code"], "video_provider_disabled")

    def _submitted(self, executor):
        admitted = self.submit()
        runtime = self.runtime(self.worker(executor))
        runtime.handle(self.message(admitted.dispatch).to_dict())
        return admitted, runtime, self.next_dispatch(admitted.job["id"])

    def test_poll_running_schedules_backoff_without_finalize(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult

        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("poll-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            polls=[VideoPollResult("poll-task", "running")],
        )
        admitted, runtime, poll_dispatch = self._submitted(executor)
        handled = runtime.handle(self.message(poll_dispatch).to_dict())
        job = get_job(admitted.job["id"])
        self.assertEqual(handled["status"], "scheduled")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["stage"], "video_poll")
        next_poll = self.next_dispatch(job["id"])
        self.assertEqual(json.loads(next_poll["payload_json"])["attempt"], 3)
        self.assertGreater(next_poll["available_at"], poll_dispatch["available_at"])

    def test_poll_completed_schedules_import_without_persisting_remote_url(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult

        signed = "https://cdn.example/video.mp4?token=must-not-persist"
        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("done-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            polls=[VideoPollResult("done-task", "completed", video_url=signed)],
        )
        admitted, runtime, poll_dispatch = self._submitted(executor)
        handled = runtime.handle(self.message(poll_dispatch).to_dict())
        job = get_job(admitted.job["id"])
        import_dispatch = self.next_dispatch(job["id"])
        self.assertEqual(handled["status"], "scheduled")
        self.assertEqual(job["stage"], "asset_import")
        self.assertEqual(json.loads(import_dispatch["payload_json"])["stage"], "asset_import")
        persisted = repr((job, import_dispatch))
        self.assertNotIn(signed, persisted)
        self.assertNotIn("must-not-persist", persisted)

    def test_poll_failed_marks_job_failed_safely(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult

        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("failed-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            polls=[VideoPollResult("failed-task", "failed", error_message="Bearer poll-secret")],
        )
        admitted, runtime, poll_dispatch = self._submitted(executor)
        handled = runtime.handle(self.message(poll_dispatch).to_dict())
        job = get_job(admitted.job["id"])
        self.assertEqual(handled["status"], "failed")
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("poll-secret", repr(job))

    def test_poll_exception_schedules_retry_with_sanitized_error(self) -> None:
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoSubmitResult

        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("retry-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            poll_error=RuntimeError("Bearer poll-retry-secret https://cdn/x?token=signed"),
        )
        admitted, runtime, poll_dispatch = self._submitted(executor)
        handled = runtime.handle(self.message(poll_dispatch).to_dict())
        job = get_job(admitted.job["id"])
        self.assertEqual(handled["status"], "scheduled")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["retryable"], 1)
        self.assertNotIn("poll-retry-secret", repr(job))
        self.assertNotIn("signed", repr(job))

    def test_poll_finalize_db_failure_resumes_same_attempt_safely(self) -> None:
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult

        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("resume-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            polls=[
                VideoPollResult("resume-task", "running"),
                VideoPollResult("resume-task", "running"),
            ],
        )
        admitted, runtime, poll_dispatch = self._submitted(executor)
        with patch(
            "angemedia_gateway.services.video_job_worker.create_job_dispatch",
            side_effect=sqlite3.OperationalError("outbox unavailable"),
        ), self.assertRaises(sqlite3.OperationalError):
            runtime.handle(self.message(poll_dispatch).to_dict())

        after_failure = get_job(admitted.job["id"])
        attempts = list_job_attempts(admitted.job["id"])
        self.assertEqual(after_failure["stage"], "video_poll")
        self.assertEqual(attempts[-1]["status"], "running")

        resumed = runtime.handle(self.message(poll_dispatch).to_dict())
        self.assertEqual(resumed["status"], "scheduled")
        self.assertEqual(executor.poll_calls, 2)
        attempts = list_job_attempts(admitted.job["id"])
        self.assertEqual(attempts[-1]["status"], "succeeded")

    def test_asset_import_finalizes_atomically_and_replay_is_noop(self) -> None:
        from angemedia_gateway.repositories.assets import list_assets
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_asset_import import VideoAssetImportResult
        from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult

        local_file = self.config.OUTPUT_DIR / "queued-video.mp4"
        local_file.write_bytes(b"video")
        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("asset-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            polls=[
                VideoPollResult("asset-task", "completed", video_url="https://cdn.example/video.mp4"),
                VideoPollResult("asset-task", "completed", video_url="https://cdn.example/video.mp4"),
            ],
        )
        importer = _FakeAssetImporter(VideoAssetImportResult(
            result={
                "task_id": "asset-task", "status": "completed",
                "video_url": "http://testserver/generated/queued-video.mp4",
                "local_path": str(local_file), "localized": True,
            },
            duration_ms=25,
        ))
        worker = self.worker(executor, importer)
        runtime = self.runtime(worker)
        admitted = self.submit()
        runtime.handle(self.message(admitted.dispatch).to_dict())
        poll_dispatch = self.next_dispatch(admitted.job["id"])
        runtime.handle(self.message(poll_dispatch).to_dict())
        import_dispatch = self.next_dispatch(admitted.job["id"])
        first = runtime.handle(self.message(import_dispatch).to_dict())
        replay = runtime.handle(self.message(import_dispatch).to_dict())

        job = get_job(admitted.job["id"])
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["stage"], "finalize")
        self.assertEqual(len(list_assets(job_id=job["id"])), 1)
        self.assertEqual(importer.calls, 1)
        attempts = list_job_attempts(job["id"])
        self.assertTrue(all(item["status"] == "succeeded" for item in attempts))
        with sqlite3.connect(str(self.config.DB_FILE)) as conn:
            generation_count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
        self.assertEqual(generation_count, 1)

    def test_asset_finalize_db_failure_rolls_back_and_resumes(self) -> None:
        from angemedia_gateway.repositories.assets import list_assets
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import append_job_event as real_append_event
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.video_asset_import import VideoAssetImportResult
        from angemedia_gateway.services.video_execution import VideoPollResult, VideoSubmitResult

        local_file = self.config.OUTPUT_DIR / "atomic-video.mp4"
        local_file.write_bytes(b"video")
        executor = _FakeVideoExecutor(
            submit=VideoSubmitResult("atomic-task", "queued", 1, "2026-06-21T10:00:00+00:00"),
            polls=[
                VideoPollResult("atomic-task", "completed", video_url="https://cdn.example/video.mp4"),
                VideoPollResult("atomic-task", "completed", video_url="https://cdn.example/video.mp4"),
                VideoPollResult("atomic-task", "completed", video_url="https://cdn.example/video.mp4"),
            ],
        )
        importer = _FakeAssetImporter(VideoAssetImportResult(
            result={
                "task_id": "atomic-task", "status": "completed",
                "video_url": "http://testserver/generated/atomic-video.mp4",
                "local_path": str(local_file), "localized": True,
            },
            duration_ms=20,
        ))
        runtime = self.runtime(self.worker(executor, importer))
        admitted = self.submit(prompt="atomic video")
        runtime.handle(self.message(admitted.dispatch).to_dict())
        poll_dispatch = self.next_dispatch(admitted.job["id"])
        runtime.handle(self.message(poll_dispatch).to_dict())
        import_dispatch = self.next_dispatch(admitted.job["id"])

        def append_or_fail(job_id, event_type, *args, **kwargs):
            if event_type == "worker_video_finalized":
                raise sqlite3.OperationalError("event insert failed")
            return real_append_event(job_id, event_type, *args, **kwargs)

        with patch(
            "angemedia_gateway.services.video_job_worker.append_job_event",
            side_effect=append_or_fail,
        ), self.assertRaises(sqlite3.OperationalError):
            runtime.handle(self.message(import_dispatch).to_dict())

        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["stage"], "asset_import")
        self.assertEqual(list_job_attempts(job["id"])[-1]["status"], "running")
        self.assertEqual(list_assets(job_id=job["id"]), [])
        with sqlite3.connect(str(self.config.DB_FILE)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
        self.assertEqual(count, 0)

        resumed = runtime.handle(self.message(import_dispatch).to_dict())
        self.assertEqual(resumed["status"], "succeeded")
        self.assertEqual(len(list_assets(job_id=job["id"])), 1)
        with sqlite3.connect(str(self.config.DB_FILE)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_terminal_message_does_not_call_executor(self) -> None:
        from angemedia_gateway.services.job_lifecycle import JobLifecycle

        admitted = self.submit()
        JobLifecycle().mark_failed(
            admitted.job["id"], kind="video", error_code="terminal",
            error_message="terminal", completed_at="2026-06-21T10:00:00+00:00",
        )
        executor = _FakeVideoExecutor()
        handled = self.runtime(self.worker(executor)).handle(
            self.message(admitted.dispatch).to_dict()
        )
        self.assertEqual(handled["status"], "terminal")
        self.assertEqual((executor.submit_calls, executor.poll_calls), (0, 0))

    def test_stale_stage_message_is_noop(self) -> None:
        from angemedia_gateway.queue.messages import JobStageMessage
        from angemedia_gateway.services.video_execution import VideoSubmitResult

        admitted = self.submit()
        executor = _FakeVideoExecutor(submit=VideoSubmitResult(
            "stale-task", "queued", 1, "2026-06-21T10:00:00+00:00"
        ))
        runtime = self.runtime(self.worker(executor))
        runtime.handle(self.message(admitted.dispatch).to_dict())
        stale = JobStageMessage(
            job_id=admitted.job["id"], stage="video_submit", attempt=99,
            dispatch_id="c" * 32, trace_id="c" * 32,
        )
        handled = runtime.handle(stale.to_dict())
        self.assertEqual(handled["status"], "stale")
        self.assertEqual(executor.submit_calls, 1)

    def test_asset_import_service_reuses_safe_localizer_and_rejects_unsafe_urls(self) -> None:
        from angemedia_gateway.services.video_asset_import import VideoAssetImportService
        from angemedia_gateway.services.video_execution import VideoPollResult

        localized = {
            "task_id": "safe-task", "status": "completed",
            "video_url": "http://testserver/generated/safe.mp4",
            "local_path": str(self.config.OUTPUT_DIR / "safe.mp4"), "localized": True,
        }
        (self.config.OUTPUT_DIR / "safe.mp4").write_bytes(b"safe")
        localize = AsyncMock(return_value=localized)
        service = VideoAssetImportService(
            localize_video_result_func=localize,
            validate_public_url_func=lambda value: value,
        )
        result = __import__("asyncio").run(service.import_completed(
            "safe-task", VideoPollResult("safe-task", "completed", video_url="https://cdn.example/safe.mp4")
        ))
        self.assertTrue(result.result["localized"])
        localize.assert_awaited_once()

        for url in (
            "https://cdn.example/video.mp4?token=signed",
            "https://user:pass@cdn.example/video.mp4",
            "http://127.0.0.1/private.mp4",
        ):
            validator = (lambda _value: (_ for _ in ()).throw(ValueError("private"))) if "127.0.0.1" in url else (lambda value: value)
            unsafe_localize = AsyncMock()
            unsafe = VideoAssetImportService(
                localize_video_result_func=unsafe_localize,
                validate_public_url_func=validator,
            )
            with self.subTest(url=url), self.assertRaises(ValueError):
                __import__("asyncio").run(unsafe.import_completed(
                    "unsafe-task", VideoPollResult("unsafe-task", "completed", video_url=url)
                ))
            unsafe_localize.assert_not_awaited()

    def test_worker_managed_manual_refresh_is_diagnostic_only(self) -> None:
        from angemedia_gateway.services.video_job_refresh import VideoJobRefreshService

        admitted = self.submit()
        poll = AsyncMock()
        service = VideoJobRefreshService(poll_task_func=poll, min_poll_interval_seconds=0)
        result = __import__("asyncio").run(service.refresh(admitted.job["id"]))
        self.assertEqual(result["refresh_status"], "worker_managed")
        self.assertFalse(result["polled"])
        poll.assert_not_awaited()


class VideoJobAdminApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="video-job-api-test-")
        self.root = Path(self.tmp_dir)
        import os
        import angemedia_gateway.config as C

        self.config = C
        self.original = (C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.GATEWAY_API_KEY)
        self.env = (os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_DEFAULT_PASSWORD"))
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123456"
        C.DB_FILE = self.root / "test.db"
        C.OUTPUT_DIR = self.root / "generated"
        C.UPLOAD_DIR = self.root / "uploads"
        C.GATEWAY_API_KEY = "am-video-api-key"
        C.OUTPUT_DIR.mkdir()
        C.UPLOAD_DIR.mkdir()
        init_db()
        from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user
        from angemedia_gateway.server import app
        from fastapi.testclient import TestClient

        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        import os
        C = self.config
        C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.GATEWAY_API_KEY = self.original
        old_user, old_password = self.env
        if old_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = old_user
        if old_password is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = old_password
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_admin_session_can_queue_video_but_gateway_key_cannot(self) -> None:
        admitted = SimpleNamespace(
            job={"id": "a" * 32, "status": "queued"},
            dispatch={"id": "b" * 32}, created=True,
        )
        service = Mock()
        service.submit.return_value = admitted
        with patch("angemedia_gateway.routes.admin.video_job_admission_service", service):
            denied = self.client.post(
                "/v1/admin/jobs/videos",
                headers={"Authorization": "Bearer am-video-api-key"},
                json={"prompt": "cat"},
            )
            self.assertEqual(denied.status_code, 403)
            login = self.client.post(
                "/v1/admin/login", json={"username": "admin", "password": "admin123456"}
            )
            self.assertEqual(login.status_code, 200, login.text)
            queued = self.client.post("/v1/admin/jobs/videos", json={"prompt": "cat"})
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()["job_id"], "a" * 32)
        service.submit.assert_called_once()


class VideoStudioQueueContractTest(unittest.TestCase):
    def test_generate_video_uses_admin_queue_without_browser_polling(self) -> None:
        source = (ROOT / "app/www/assets/studio/features/generate-video/page.js").read_text(encoding="utf-8")
        self.assertIn("api.post('/admin/jobs/videos'", source)
        self.assertNotIn("api.post('/videos'", source)
        self.assertNotIn("setInterval", source)


if __name__ == "__main__":
    unittest.main()

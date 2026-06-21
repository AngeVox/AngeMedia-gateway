"""Queued image admission and worker stage contracts."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.state import init_db


class _FakeExecutor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def execute(self, request, plan):
        from angemedia_gateway.services.image_execution import ImageExecutionResult

        self.calls += 1
        if self.error is not None:
            raise self.error
        return ImageExecutionResult(
            result=self.result,
            provider="fake",
            model="fake-model",
            request_model=request.model,
            input_mode="explicit_model",
            duration_ms=12,
            started_at="2026-06-21T10:00:00+00:00",
        )


class ImageJobQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="image-job-queue-test-")
        self.root = Path(self._tmp_dir)
        import angemedia_gateway.config as C

        self._config = C
        self._original = (C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.PUBLIC_BASE_URL)
        C.DB_FILE = self.root / "test.db"
        C.OUTPUT_DIR = self.root / "generated"
        C.UPLOAD_DIR = self.root / "uploads"
        C.PUBLIC_BASE_URL = "http://testserver"
        C.OUTPUT_DIR.mkdir()
        C.UPLOAD_DIR.mkdir()
        init_db()

    def tearDown(self) -> None:
        C = self._config
        C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.PUBLIC_BASE_URL = self._original
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @staticmethod
    def _chain(_model=None):
        from angemedia_gateway.providers.base import RouteTarget

        return [RouteTarget("fake", "fake-model")]

    def _admission(self):
        from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService

        return ImageJobAdmissionService(resolve_chain_func=self._chain)

    def _submit(self, **overrides):
        from angemedia_gateway.schemas import ImageRequest

        payload = {"prompt": "queued cat", "model": "fake", "response_format": "url"}
        payload.update(overrides)
        return self._admission().submit(ImageRequest(**payload))

    def _message(self, admitted):
        from angemedia_gateway.queue.messages import JobStageMessage

        return JobStageMessage(
            job_id=admitted.job["id"], stage="image_generate", attempt=1,
            dispatch_id=admitted.dispatch["id"], trace_id=admitted.dispatch["id"],
        )

    def test_admission_atomically_writes_safe_job_and_dispatch_without_provider(self) -> None:
        from angemedia_gateway.repositories.job_dispatches import get_job_dispatch

        admitted = self._submit()
        self.assertTrue(admitted.created)
        self.assertEqual(admitted.job["status"], "queued")
        self.assertEqual(admitted.job["stage"], "image_generate")
        dispatch = get_job_dispatch(admitted.dispatch["id"])
        self.assertEqual(dispatch["status"], "pending")
        self.assertEqual(json.loads(dispatch["payload_json"]), {
            "job_id": admitted.job["id"],
            "job_kind": "image",
            "stage": "image_generate",
            "payload_schema_version": 1,
            "attempt": 1,
        })

    def test_active_duplicate_reuses_job_but_terminal_allows_new_job(self) -> None:
        from angemedia_gateway.services.job_lifecycle import JobLifecycle

        first = self._submit()
        duplicate = self._submit()
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.job["id"], first.job["id"])
        JobLifecycle().mark_failed(
            first.job["id"], kind="image", error_code="test", error_message="test",
            completed_at="2026-06-21T10:01:00+00:00",
        )
        later = self._submit()
        self.assertTrue(later.created)
        self.assertNotEqual(later.job["id"], first.job["id"])

    def test_admission_rejects_unsafe_references_and_b64_output(self) -> None:
        rejected = (
            "data:image/png;base64,AAAA",
            "https://cdn.example/ref.png?token=secret",
            "C:/private/ref.png",
            "/etc/passwd",
            "/uploads/../private.png",
        )
        for image in rejected:
            with self.subTest(image=image), self.assertRaises(ValueError):
                self._submit(image=image)
        with self.assertRaises(ValueError):
            self._submit(response_format="b64_json")

    def test_dispatcher_message_contains_no_prompt_or_job_payload(self) -> None:
        from angemedia_gateway.services.job_dispatcher import JobDispatcher

        class Backend:
            messages = []

            def publish(self, *, topic, message):
                self.messages.append(message.to_dict())
                return "broker-id"

        admitted = self._submit()
        backend = Backend()
        result = JobDispatcher(
            queue_backend=backend,
            batch_size=20,
            lease_seconds=30,
            max_attempts=3,
            retry_base_seconds=2,
        ).dispatch_once()
        self.assertEqual(result.published, 1)
        rendered = repr(backend.messages)
        self.assertNotIn("queued cat", rendered)
        self.assertNotIn("input_json", rendered)
        self.assertNotIn("response_format", rendered)
        self.assertIn(admitted.job["id"], rendered)

    def test_disabled_provider_does_not_create_job(self) -> None:
        from angemedia_gateway.repositories.jobs import list_jobs
        from angemedia_gateway.schemas import ImageRequest
        from angemedia_gateway.services.image_execution import NoImageProviderAvailable
        from angemedia_gateway.services.image_job_admission import ImageJobAdmissionService

        service = ImageJobAdmissionService(resolve_chain_func=lambda _model: [])
        with self.assertRaises(NoImageProviderAvailable):
            service.submit(ImageRequest(prompt="disabled", model="disabled"))
        self.assertEqual(list_jobs(), [])

    def test_worker_executes_registered_stage_and_finalizes_once(self) -> None:
        from angemedia_gateway.repositories.assets import list_assets
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        generated = self._config.OUTPUT_DIR / "queued-cat.png"
        generated.write_bytes(b"\x89PNG\r\n\x1a\nworker")
        provider_result = {
            "data": [{
                "url": "http://testserver/generated/queued-cat.png",
                "local_path": str(generated),
            }]
        }
        admitted = self._submit()
        executor = _FakeExecutor(provider_result)
        worker = ImageJobWorker(executor=executor)
        runtime = WorkerRuntime(registry=JobStageRegistry({"image_generate": worker.handle}))

        first = runtime.handle(self._message(admitted).to_dict())
        replay = runtime.handle(self._message(admitted).to_dict())

        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(executor.calls, 1)
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["stage"], "finalize")
        attempts = list_job_attempts(job["id"])
        self.assertEqual([(item["attempt_number"], item["status"]) for item in attempts], [(1, "succeeded")])
        assets = list_assets(job_id=job["id"])
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["url_path"], "/generated/queued-cat.png")
        with sqlite3.connect(str(self._config.DB_FILE)) as conn:
            generation_count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
        self.assertEqual(generation_count, 1)
        from angemedia_gateway.repositories.job_events import list_job_events
        event_types = [item["event_type"] for item in list_job_events(job["id"])]
        self.assertIn("worker_attempt_succeeded", event_types)
        self.assertIn("status_changed", event_types)

    def test_success_persistence_failure_rolls_back_then_fails_consistently(self) -> None:
        from unittest.mock import patch
        from angemedia_gateway.repositories.assets import list_assets
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        generated = self._config.OUTPUT_DIR / "atomic-failure.png"
        generated.write_bytes(b"\x89PNG\r\n\x1a\nworker")
        result = {"data": [{
            "url": "http://testserver/generated/atomic-failure.png",
            "local_path": str(generated),
        }]}
        admitted = self._submit(prompt="atomic failure")
        runtime = WorkerRuntime(registry=JobStageRegistry({
            "image_generate": ImageJobWorker(executor=_FakeExecutor(result)).handle,
        }))
        with patch(
            "angemedia_gateway.services.image_job_worker.save_generated_asset",
            side_effect=RuntimeError("asset metadata failure sk-secret-123456789"),
        ):
            handled = runtime.handle(self._message(admitted).to_dict())

        self.assertEqual(handled["status"], "failed")
        job = get_job(admitted.job["id"])
        attempts = list_job_attempts(job["id"])
        events = list_job_events(job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertIn("worker_attempt_failed", [item["event_type"] for item in events])
        self.assertEqual(list_assets(job_id=job["id"]), [])
        with sqlite3.connect(str(self._config.DB_FILE)) as conn:
            generation_count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE job_id=?", (job["id"],)
            ).fetchone()[0]
        self.assertEqual(generation_count, 0)
        self.assertNotIn("sk-secret-123456789", repr((job, attempts, events)))

    def test_failure_finalize_event_error_rolls_back_all_failed_state(self) -> None:
        from unittest.mock import patch
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import append_job_event as real_append_event
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        admitted = self._submit(prompt="failure rollback")
        runtime = WorkerRuntime(registry=JobStageRegistry({
            "image_generate": ImageJobWorker(
                executor=_FakeExecutor(error=RuntimeError("provider failed"))
            ).handle,
        }))

        def append_or_fail(job_id, event_type, *args, **kwargs):
            if event_type == "worker_attempt_failed":
                raise sqlite3.OperationalError("event insert failed")
            return real_append_event(job_id, event_type, *args, **kwargs)

        with patch(
            "angemedia_gateway.services.image_job_worker.append_job_event",
            side_effect=append_or_fail,
        ), self.assertRaises(sqlite3.OperationalError):
            runtime.handle(self._message(admitted).to_dict())

        job = get_job(admitted.job["id"])
        attempts = list_job_attempts(job["id"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(attempts[0]["status"], "running")
        self.assertIsNone(attempts[0]["completed_at"])

    def test_terminal_message_is_safe_noop_without_provider_call(self) -> None:
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_lifecycle import JobLifecycle
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        admitted = self._submit()
        JobLifecycle().mark_failed(
            admitted.job["id"], kind="image", error_code="disabled",
            error_message="disabled", completed_at="2026-06-21T10:01:00+00:00",
        )
        executor = _FakeExecutor({"data": []})
        runtime = WorkerRuntime(registry=JobStageRegistry({
            "image_generate": ImageJobWorker(executor=executor).handle,
        }))
        result = runtime.handle(self._message(admitted).to_dict())
        self.assertEqual(result["status"], "terminal")
        self.assertEqual(executor.calls, 0)

    def test_provider_disabled_after_admission_fails_without_provider_call(self) -> None:
        from angemedia_gateway.services.image_execution import ImageExecutionService
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        class Provider:
            calls = 0

            async def generate(self, request, target):
                self.calls += 1
                return {"data": []}

        admitted = self._submit()
        provider = Provider()
        executor = ImageExecutionService(
            providers={"fake": provider},
            provider_enabled_func=lambda _provider: False,
        )
        runtime = WorkerRuntime(registry=JobStageRegistry({
            "image_generate": ImageJobWorker(executor=executor).handle,
        }))
        result = runtime.handle(self._message(admitted).to_dict())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(provider.calls, 0)

    def test_worker_failure_is_sanitized_and_never_marks_success(self) -> None:
        from angemedia_gateway.providers.errors import BackendUnavailable
        from angemedia_gateway.repositories.job_attempts import list_job_attempts
        from angemedia_gateway.repositories.job_events import list_job_events
        from angemedia_gateway.repositories.jobs import get_job
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        secret = "sk-provider-secret-123456789"
        signed = "https://cdn.example/x?token=signed-secret"
        admitted = self._submit()
        executor = _FakeExecutor(error=BackendUnavailable(f"failed {secret} {signed}"))
        runtime = WorkerRuntime(registry=JobStageRegistry({
            "image_generate": ImageJobWorker(executor=executor).handle,
        }))

        result = runtime.handle(self._message(admitted).to_dict())

        self.assertEqual(result["status"], "failed")
        job = get_job(admitted.job["id"])
        self.assertEqual(job["status"], "failed")
        rendered = repr((job, list_job_attempts(job["id"]), list_job_events(job["id"])))
        self.assertNotIn(secret, rendered)
        self.assertNotIn("signed-secret", rendered)

    def test_generation_repository_is_idempotent_by_job_id(self) -> None:
        from angemedia_gateway.repositories.generations import record_generation

        admitted = self._submit()
        kwargs = dict(
            media_type="image", prompt="queued cat", enhanced_prompt=None,
            model="fake-model", status="completed",
            result={"data": [{"url": "/generated/queued-cat.png"}]},
            job_id=admitted.job["id"],
        )
        first = record_generation(**kwargs)
        second = record_generation(**kwargs)
        self.assertEqual(second, first)

    def test_replay_with_partial_generation_and_asset_does_not_duplicate(self) -> None:
        from angemedia_gateway.repositories.assets import list_assets
        from angemedia_gateway.repositories.generations import record_generation
        from angemedia_gateway.services.generation_assets import save_generated_asset
        from angemedia_gateway.services.image_job_worker import ImageJobWorker
        from angemedia_gateway.services.job_stage_registry import JobStageRegistry
        from angemedia_gateway.services.worker_runtime import WorkerRuntime

        generated = self._config.OUTPUT_DIR / "partial-existing.png"
        generated.write_bytes(b"\x89PNG\r\n\x1a\nworker")
        result = {"data": [{
            "url": "http://testserver/generated/partial-existing.png",
            "local_path": str(generated),
        }]}
        admitted = self._submit(prompt="partial existing")
        record_generation(
            media_type="image", prompt="partial existing", enhanced_prompt=None,
            model="fake-model", status="completed", result=result,
            job_id=admitted.job["id"],
        )
        save_generated_asset(
            media_type="image", result=result, prompt="partial existing",
            model="fake-model", provider="fake", duration_ms=1,
            job_id=admitted.job["id"],
        )
        runtime = WorkerRuntime(registry=JobStageRegistry({
            "image_generate": ImageJobWorker(executor=_FakeExecutor(result)).handle,
        }))
        handled = runtime.handle(self._message(admitted).to_dict())
        self.assertEqual(handled["status"], "succeeded")
        self.assertEqual(len(list_assets(job_id=admitted.job["id"])), 1)
        with sqlite3.connect(str(self._config.DB_FILE)) as conn:
            generation_count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE job_id=?", (admitted.job["id"],)
            ).fetchone()[0]
        self.assertEqual(generation_count, 1)

    def test_generation_job_id_migration_preserves_legacy_rows(self) -> None:
        from angemedia_gateway.db.migrations import (
            QUEUE_FOUNDATION_VERSION,
            run_migrations,
        )

        legacy = self.root / "legacy-generations.db"
        with sqlite3.connect(str(legacy), isolation_level=None) as conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                INSERT INTO schema_migrations(version,applied_at)
                VALUES('queue_foundation_v1','2026-01-01T00:00:00+00:00');
                CREATE TABLE generations(
                    id TEXT PRIMARY KEY, media_type TEXT NOT NULL, prompt TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO generations(id,media_type,prompt,status,created_at,updated_at)
                VALUES('legacy-generation','image','cat','completed','2026-01-01','2026-01-01');
                """
            )
            self.assertEqual(QUEUE_FOUNDATION_VERSION, "queue_foundation_v1")
            run_migrations(conn)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(generations)")}
            row = conn.execute("SELECT id,job_id FROM generations").fetchone()
        self.assertIn("job_id", columns)
        self.assertIn("uq_generations_job_id", indexes)
        self.assertEqual(row, ("legacy-generation", None))

    def test_real_schema_reinitialization_keeps_generations_and_unique_index(self) -> None:
        from angemedia_gateway.state import init_db as reinitialize

        with sqlite3.connect(str(self._config.DB_FILE)) as conn:
            conn.execute(
                "INSERT INTO generations(id,media_type,prompt,status,created_at,updated_at) "
                "VALUES('keep-me','image','cat','completed','2026-01-01','2026-01-01')"
            )
            conn.commit()
        reinitialize()
        with sqlite3.connect(str(self._config.DB_FILE)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM generations WHERE id='keep-me'"
            ).fetchone()[0]
            columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(generations)")}
        self.assertEqual(count, 1)
        self.assertIn("job_id", columns)
        self.assertIn("uq_generations_job_id", indexes)


class ImageJobAdminApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="image-job-admin-api-test-")
        self.root = Path(self._tmp_dir)
        import os
        import angemedia_gateway.config as C

        self._config = C
        self._original = (C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.GATEWAY_API_KEY)
        self._env = (os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_DEFAULT_PASSWORD"))
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123456"
        C.DB_FILE = self.root / "test.db"
        C.OUTPUT_DIR = self.root / "generated"
        C.UPLOAD_DIR = self.root / "uploads"
        C.GATEWAY_API_KEY = "am-api-only-test-key"
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

        C = self._config
        C.DB_FILE, C.OUTPUT_DIR, C.UPLOAD_DIR, C.GATEWAY_API_KEY = self._original
        old_user, old_password = self._env
        if old_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = old_user
        if old_password is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = old_password
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_admin_session_can_submit_but_gateway_key_cannot(self) -> None:
        from unittest.mock import Mock, patch

        admitted = SimpleNamespace(
            job={"id": "j" * 32, "status": "queued"},
            dispatch={"id": "d" * 32},
            created=True,
        )
        fake_service = Mock()
        fake_service.submit.return_value = admitted
        with patch("angemedia_gateway.routes.admin.image_job_admission_service", fake_service):
            denied = self.client.post(
                "/v1/admin/jobs/images",
                headers={"Authorization": "Bearer am-api-only-test-key"},
                json={"prompt": "cat", "model": "fake"},
            )
            self.assertEqual(denied.status_code, 403)
            login = self.client.post(
                "/v1/admin/login", json={"username": "admin", "password": "admin123456"}
            )
            self.assertEqual(login.status_code, 200, login.text)
            queued = self.client.post(
                "/v1/admin/jobs/images", json={"prompt": "cat", "model": "fake"}
            )
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()["job_id"], "j" * 32)
        fake_service.submit.assert_called_once()


class ImageStudioQueueContractTest(unittest.TestCase):
    def test_generate_image_uses_admin_queue_without_polling(self) -> None:
        source = (ROOT / "app/www/assets/studio/features/generate-image/page.js").read_text(encoding="utf-8")
        self.assertIn("api.post('/admin/jobs/images'", source)
        self.assertNotIn("api.post('/images/generations'", source)
        self.assertNotIn("setInterval", source)


if __name__ == "__main__":
    unittest.main()

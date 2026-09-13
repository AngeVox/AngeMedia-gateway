"""Managed fnOS Local/Celery queue runtime switching contracts."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.services.job_admission import JobAdmissionService  # noqa: E402
from angemedia_gateway.services.queue_runtime import (  # noqa: E402
    QueueRuntimeError,
    QueueRuntimeService,
    validate_redis_url,
)
from angemedia_gateway.state import init_db  # noqa: E402


class QueueRuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="queue-runtime-"))
        self.etc = self.tmp / "etc"
        self.var = self.tmp / "var"
        self.logs = self.tmp / "logs"
        self.etc.mkdir()
        (self.var / "run").mkdir(parents=True)
        self.logs.mkdir()
        self.env_file = self.etc / "angemedia.env"
        self.env_file.write_text("QUEUE_ENABLED=true\nQUEUE_BACKEND=local\n", encoding="utf-8")
        self.orig_db = C.DB_FILE
        C.DB_FILE = self.tmp / "state.db"
        init_db()
        self.env = patch.dict(os.environ, {
            "TRIM_PKGETC": str(self.etc),
            "TRIM_PKGVAR": str(self.var),
            "ANGEMEDIA_LOG_DIR": str(self.logs),
            "QUEUE_ENABLED": "true",
            "QUEUE_BACKEND": "local",
        }, clear=False)
        self.env.start()
        os.environ.pop("REDIS_URL", None)
        os.environ.pop("CELERY_BROKER_URL", None)
        self.service = QueueRuntimeService()

    def tearDown(self) -> None:
        self.env.stop()
        C.DB_FILE = self.orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_redis_url_policy_allows_admin_local_target_and_rejects_special_targets(self) -> None:
        self.assertEqual(validate_redis_url("redis://127.0.0.1:6379/0"), "redis://127.0.0.1:6379/0")
        with self.assertRaises(QueueRuntimeError):
            validate_redis_url("redis://169.254.169.254:6379/0")
        with self.assertRaises(QueueRuntimeError):
            validate_redis_url("http://127.0.0.1:6379")

    def test_managed_runtime_does_not_adopt_ambient_redis_url(self) -> None:
        os.environ["REDIS_URL"] = "redis://ambient.example:6379/0"
        os.environ["CELERY_BROKER_URL"] = "redis://ambient.example:6379/0"
        self.assertIsNone(self.service._saved_redis_url())

    def test_detect_local_default_returns_only_safe_target_summary(self) -> None:
        with patch.object(self.service, "_redis_ping", return_value=True):
            result = self.service.detect_redis()
        self.assertTrue(result["detected"])
        self.assertEqual(result["recommended_source"], "local_default")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["host"], "127.0.0.1")
        self.assertEqual(candidate["port"], 6379)
        self.assertNotIn("url", candidate)

    def test_switch_to_celery_persists_broker_but_does_not_return_it(self) -> None:
        broker = "redis://:dummy-secret@127.0.0.1:6379/0"
        with (
            patch.object(self.service, "_redis_ping", return_value=True),
            patch.object(self.service, "_restart_topology") as restart,
            patch.object(self.service, "_process_summary", return_value={"dispatcher": True, "worker": True}),
        ):
            result = self.service.switch_backend("celery", broker)
        self.assertEqual(result["backend"], "celery")
        self.assertTrue(result["redis_configured"])
        self.assertNotIn("dummy-secret", repr(result))
        text = self.env_file.read_text(encoding="utf-8")
        self.assertIn("QUEUE_BACKEND=celery", text)
        self.assertIn("dummy-secret", text)
        restart.assert_called_once_with("celery")

    def test_switch_is_blocked_while_job_is_active(self) -> None:
        JobAdmissionService().admit(
            kind="image",
            stage="image_generate",
            request_hash="a" * 64,
            request_hash_version=1,
            payload={"schema_version": 1},
            provider="mock",
            model="mock-model",
        )
        with self.assertRaises(QueueRuntimeError) as caught:
            self.service.switch_backend("local")
        self.assertEqual(caught.exception.status_code, 409)

    def test_failed_switch_restores_original_env_and_backend(self) -> None:
        broker = "redis://127.0.0.1:6379/0"
        calls = []
        def restart(backend: str) -> None:
            calls.append(backend)
            if len(calls) == 1:
                raise RuntimeError("synthetic start failure")
        with (
            patch.object(self.service, "_redis_ping", return_value=True),
            patch.object(self.service, "_restart_topology", side_effect=restart),
        ):
            with self.assertRaises(QueueRuntimeError) as caught:
                self.service.switch_backend("celery", broker)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(os.environ.get("QUEUE_BACKEND"), "local")
        self.assertIn("QUEUE_BACKEND=local", self.env_file.read_text(encoding="utf-8"))
        self.assertEqual(calls, ["celery", "local"])


if __name__ == "__main__":
    unittest.main()

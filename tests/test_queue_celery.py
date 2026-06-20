"""Celery app, message schema, and broker adapter contracts."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class _FakeAsyncResult:
    id = "broker-message-1"


class _FakeCeleryApp:
    def __init__(self) -> None:
        self.calls = []

    def send_task(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return _FakeAsyncResult()


class QueueMessageContractTest(unittest.TestCase):
    def test_message_is_strict_and_contains_only_safe_identifiers(self) -> None:
        from angemedia_gateway.queue.messages import JobStageMessage

        message = JobStageMessage(
            job_id="a" * 32,
            stage="image_generate",
            attempt=1,
            dispatch_id="b" * 32,
            trace_id="c" * 32,
        )
        self.assertEqual(
            set(message.to_dict()),
            {"schema_version", "job_id", "stage", "attempt", "dispatch_id", "trace_id"},
        )

    def test_raw_payload_and_unknown_fields_are_rejected_without_echo(self) -> None:
        from angemedia_gateway.queue.messages import InvalidQueueMessage, parse_job_stage_message

        secret = "sk-queue-secret-123456789"
        with self.assertRaises(InvalidQueueMessage) as raised:
            parse_job_stage_message({
                "job_id": "a" * 32,
                "stage": "image_generate",
                "attempt": 1,
                "dispatch_id": "b" * 32,
                "trace_id": "c" * 32,
                "raw_payload": {"api_key": secret},
            })
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("raw_payload", str(raised.exception))

    def test_boolean_schema_version_is_not_accepted_as_integer_one(self) -> None:
        from angemedia_gateway.queue.messages import InvalidQueueMessage, JobStageMessage

        with self.assertRaises(InvalidQueueMessage):
            JobStageMessage(
                schema_version=True,
                job_id="a" * 32,
                stage="image_generate",
                attempt=1,
                dispatch_id="b" * 32,
                trace_id="c" * 32,
            )


class CeleryAppContractTest(unittest.TestCase):
    def test_celery_app_uses_redis_broker_and_no_result_backend(self) -> None:
        from angemedia_gateway.queue.celery_app import create_celery_app
        from angemedia_gateway.queue.settings import QueueSettings

        settings = QueueSettings(
            enabled=True,
            backend="celery",
            broker_url="redis://redis:6379/0",
            task_queue="angemedia.jobs",
        )
        app = create_celery_app(settings)
        self.assertEqual(app.conf.broker_url, "redis://redis:6379/0")
        self.assertIsNone(app.conf.result_backend)
        self.assertTrue(app.conf.task_ignore_result)
        self.assertEqual(app.conf.task_serializer, "json")
        self.assertEqual(app.conf.accept_content, ["json"])
        self.assertTrue(app.conf.task_acks_late)
        self.assertEqual(app.conf.worker_prefetch_multiplier, 1)

    def test_disabled_mode_must_be_explicit(self) -> None:
        from angemedia_gateway.queue.settings import QueueSettings

        with patch.dict(os.environ, {"QUEUE_ENABLED": "false", "QUEUE_BACKEND": "disabled"}, clear=False):
            settings = QueueSettings.from_env()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.backend, "disabled")

    def test_result_backend_environment_override_is_rejected(self) -> None:
        from angemedia_gateway.queue.celery_app import create_celery_app
        from angemedia_gateway.queue.settings import QueueSettings

        with patch.dict(
            os.environ,
            {"CELERY_RESULT_BACKEND": "redis://:result-secret@redis:6379/9"},
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as raised:
                create_celery_app(QueueSettings())
        self.assertNotIn("result-secret", str(raised.exception))


class CeleryQueueBackendContractTest(unittest.TestCase):
    def test_publish_uses_json_message_and_ignores_results(self) -> None:
        from angemedia_gateway.queue.celery_backend import CeleryQueueBackend
        from angemedia_gateway.queue.messages import JobStageMessage
        from angemedia_gateway.queue.settings import QueueSettings

        app = _FakeCeleryApp()
        backend = CeleryQueueBackend(
            app=app,
            settings=QueueSettings(
                enabled=True,
                backend="celery",
                broker_url="redis://redis:6379/0",
                task_queue="angemedia.jobs",
            ),
        )
        message = JobStageMessage(
            job_id="a" * 32,
            stage="video_submit",
            attempt=1,
            dispatch_id="b" * 32,
            trace_id="c" * 32,
        )
        message_id = backend.publish(topic="angemedia.jobs.execute", message=message)

        self.assertEqual(message_id, "broker-message-1")
        task_name, kwargs = app.calls[0]
        self.assertEqual(task_name, "angemedia.jobs.execute")
        self.assertEqual(kwargs["queue"], "angemedia.jobs")
        self.assertEqual(kwargs["serializer"], "json")
        self.assertTrue(kwargs["ignore_result"])
        self.assertEqual(kwargs["task_id"], "b" * 32)
        self.assertEqual(kwargs["args"], [message.to_dict()])
        self.assertEqual(kwargs["argsrepr"], "(<sanitized-job-stage-message>,)")
        self.assertEqual(kwargs["kwargsrepr"], "{}")

    def test_publish_rejects_dict_and_unapproved_topic(self) -> None:
        from angemedia_gateway.queue.celery_backend import CeleryQueueBackend
        from angemedia_gateway.queue.settings import QueueSettings

        backend = CeleryQueueBackend(
            app=_FakeCeleryApp(),
            settings=QueueSettings(
                enabled=True,
                backend="celery",
                broker_url="redis://redis:6379/0",
                task_queue="angemedia.jobs",
            ),
        )
        with self.assertRaises(TypeError):
            backend.publish(topic="angemedia.jobs.execute", message={"api_key": "secret"})
        with self.assertRaises(ValueError):
            backend.publish(topic="unapproved.task", message=object())

    def test_diagnostics_never_exposes_broker_url(self) -> None:
        from angemedia_gateway.queue.celery_backend import QueueUnavailable
        from angemedia_gateway.queue.diagnostics import queue_diagnostics
        from angemedia_gateway.queue.settings import QueueSettings

        class UnavailableBackend:
            def healthcheck(self):
                raise QueueUnavailable("redis://:redis-password@redis:6379/0")

        settings = QueueSettings(
            enabled=True,
            backend="celery",
            broker_url="redis://:redis-password@redis:6379/0",
        )
        result = queue_diagnostics(UnavailableBackend(), settings)
        self.assertFalse(result["healthy"])
        self.assertNotIn("redis-password", repr(result))


if __name__ == "__main__":
    unittest.main()

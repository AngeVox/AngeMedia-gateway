"""Static deployment and dependency contracts for the formal queue runtime."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class QueueComposeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.compose = yaml.safe_load(self.compose_text)

    def test_compose_has_api_redis_worker_and_dispatcher(self) -> None:
        services = self.compose["services"]
        self.assertTrue({"api", "redis", "worker", "dispatcher"}.issubset(services))
        self.assertIn("healthcheck", services["redis"])
        self.assertIn("appendonly", " ".join(services["redis"]["command"]))

    def test_worker_and_dispatcher_use_dedicated_commands_and_shared_volumes(self) -> None:
        services = self.compose["services"]
        self.assertIn("cli.worker", " ".join(services["worker"]["command"]))
        self.assertIn("cli.dispatcher", " ".join(services["dispatcher"]["command"]))
        self.assertIn("build", services["worker"])
        self.assertIn("build", services["dispatcher"])
        for name in ("api", "worker", "dispatcher"):
            volumes = set(services[name]["volumes"])
            self.assertIn("angemedia-state:/app/state", volumes)
            self.assertIn("angemedia-generated:/app/generated", volumes)
            self.assertIn("angemedia-uploads:/app/uploads", volumes)
        self.assertEqual(services["worker"]["depends_on"]["redis"]["condition"], "service_healthy")
        self.assertEqual(services["dispatcher"]["depends_on"]["redis"]["condition"], "service_healthy")
        self.assertIn("healthcheck", services["dispatcher"])

    def test_queue_environment_is_explicit(self) -> None:
        services = self.compose["services"]
        for name in ("api", "worker", "dispatcher"):
            environment = services[name]["environment"]
            self.assertIn(":-true", environment["QUEUE_ENABLED"])
            self.assertIn(":-celery", environment["QUEUE_BACKEND"])
            self.assertIn("redis://redis:6379/0", environment["CELERY_BROKER_URL"])

    def test_requirements_and_env_example_expose_formal_queue_dependencies(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn("celery", requirements)
        self.assertIn("redis", requirements)
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in (
            "QUEUE_ENABLED", "QUEUE_BACKEND", "REDIS_URL", "CELERY_BROKER_URL",
            "CELERY_TASK_QUEUE", "QUEUE_DISPATCHER_INTERVAL_SECONDS",
            "QUEUE_DISPATCHER_BATCH_SIZE", "QUEUE_DISPATCH_LEASE_SECONDS",
            "WORKER_CONCURRENCY",
        ):
            self.assertIn(f"{key}=", env_example)

    def test_routes_and_provider_adapters_do_not_publish_to_celery(self) -> None:
        paths = [
            *list((ROOT / "scripts" / "angemedia_gateway" / "routes").glob("*.py")),
            *list((ROOT / "scripts" / "angemedia_gateway" / "adapters").glob("*.py")),
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("send_task", text)
            self.assertNotIn("CeleryQueueBackend", text)


if __name__ == "__main__":
    unittest.main()

"""Static deployment and dependency contracts for the formal queue runtime."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
import ast

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

    def test_smoke_compose_override_uses_fake_providers_without_changing_default_compose(self) -> None:
        smoke_path = ROOT / "docker-compose.queue-smoke.yml"
        self.assertTrue(smoke_path.exists(), "queue smoke compose override is missing")
        smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
        services = smoke["services"]
        for name in ("api", "worker", "dispatcher"):
            with self.subTest(service=name):
                environment = services[name]["environment"]
                self.assertEqual(environment["QUEUE_SMOKE_FAKE_PROVIDERS"], "true")
                self.assertEqual(environment["WORKER_CONCURRENCY"], "1")
                self.assertIn("redis://redis:6379/0", environment["CELERY_BROKER_URL"])
        self.assertNotIn("QUEUE_SMOKE_FAKE_PROVIDERS", self.compose_text)

    def test_compose_smoke_script_is_safe_and_cleans_up(self) -> None:
        script_path = ROOT / "scripts" / "ci" / "queue_compose_smoke.py"
        self.assertTrue(script_path.exists(), "queue compose smoke script is missing")
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("--dry-run", source)
        self.assertIn("QUEUE_SMOKE_FAKE_PROVIDERS", source)
        self.assertIn("docker compose", source)
        self.assertIn("down", source)
        self.assertIn("finally", source)
        self.assertIn("/v1/admin/jobs/images", source)
        self.assertIn("/v1/admin/jobs/videos", source)
        self.assertIn("/v1/admin/dashboard/summary", source)
        self.assertIn("/v1/assets", source)
        self.assertIn("assert_no_forbidden_payload", source)
        forbidden_calls = {"handle_submit", "handle_poll", "handle_asset_import", "handle("}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, forbidden_calls)

    def test_smoke_fake_mode_is_explicit_in_worker_registry_and_admission(self) -> None:
        registry = (ROOT / "scripts" / "angemedia_gateway" / "services" / "job_stage_registry.py").read_text(encoding="utf-8")
        image_admission = (ROOT / "scripts" / "angemedia_gateway" / "services" / "image_job_admission.py").read_text(encoding="utf-8")
        video_admission = (ROOT / "scripts" / "angemedia_gateway" / "services" / "video_job_admission.py").read_text(encoding="utf-8")
        smoke = (ROOT / "scripts" / "angemedia_gateway" / "services" / "queue_smoke.py").read_text(encoding="utf-8")
        for source in (registry, image_admission, video_admission):
            self.assertIn("queue_smoke_enabled", source)
        self.assertIn("QUEUE_SMOKE_FAKE_PROVIDERS", smoke)
        self.assertIn("FakeQueueSmokeImageExecutor", registry)
        self.assertIn("FakeQueueSmokeVideoExecutor", registry)


if __name__ == "__main__":
    unittest.main()

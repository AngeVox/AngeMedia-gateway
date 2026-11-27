"""通过真实配置API、HTTP边界与持久化验证连接证据生命周期。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from studio_test_client import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.repositories.provider_connection_evidence import get_connection_evidence
from angemedia_gateway.repositories.provider_runtime_config import update_provider_runtime_config
from angemedia_gateway.services import provider_connection_evidence as evidence
from angemedia_gateway.services.provider_runtime_config import ProviderRuntimeConfigService
from angemedia_gateway.server import app
from angemedia_gateway.state import ensure_default_admin_user, init_db


class ProviderEvidenceLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="provider-evidence-lifecycle-")
        self.addCleanup(tmp.cleanup)
        for setting in (
            patch.object(C, "DB_FILE", Path(tmp.name) / "test.db"),
            patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": "test-password-only"}),
        ):
            setting.start()
            self.addCleanup(setting.stop)
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post("/v1/admin/login", json={"username": "admin", "password": "test-password-only"}).status_code, 200)
        self.configure("siliconflow", enabled=True, api_key="PRIVATE_KEY_ORIGINAL")

    def configure(self, provider: str, **values):
        response = self.client.post(f"/v1/admin/provider-configs/{provider}", json=values)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def current(self, provider: str = "siliconflow") -> dict:
        response = self.client.get("/v1/admin/provider-configs")
        self.assertEqual(response.status_code, 200, response.text)
        return next(item["connection_evidence"] for item in response.json()["data"] if item["provider_id"] == provider)

    def probe(self, provider="siliconflow", on_request=None):
        requests = []

        def respond(request):
            requests.append(request)
            if on_request:
                on_request(request)
            return httpx.Response(200, json={"data": [{"id": "PRIVATE_MODEL_ID"}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("httpx.AsyncClient", return_value=client):
            response = self.client.post(f"/v1/admin/provider-configs/{provider}/test")
        self.assertEqual(response.status_code, 200, response.text)
        for private in ("PRIVATE_KEY_ORIGINAL", "PRIVATE_MODEL_ID", "fingerprint", "PRIVATE_PROXY_PASSWORD"):
            self.assertNotIn(private, response.text)
        return response.json()["data"], requests

    def test_observation_survives_new_service_and_expires_without_claiming_generation(self):
        clock = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        self.assertEqual(self.current()["evidence_state"], "not_tested")
        with patch.object(evidence, "_now", return_value=clock):
            result, requests = self.probe()
            self.assertEqual(result["evidence_state"], "current")
            self.assertEqual(result["checked_at"], clock.isoformat())
            self.assertEqual(result["verification_scope"], "models_endpoint")
            self.assertFalse(result["generation_verified"])
            self.assertEqual(len(requests), 1)
            restored = next(item for item in ProviderRuntimeConfigService().list_configs() if item["provider_id"] == "siliconflow")
            self.assertEqual(restored["connection_evidence"]["checked_at"], result["checked_at"])
        with patch.object(evidence, "_now", return_value=clock + timedelta(seconds=900)):
            expired = self.current()
            self.assertEqual(expired["evidence_state"], "expired")
            self.assertFalse(expired["model_list_validated"])
            self.assertFalse(expired["generation_verified"])

    def test_modelscope_visibility_is_removed_after_expiry_or_scope_change(self):
        epoch = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        self.configure("modelscope", enabled=True, api_key="PRIVATE_KEY_ORIGINAL")
        with patch.object(evidence, "_now", return_value=epoch):
            recorded, _ = self.probe("modelscope")
            self.assertEqual(recorded["project_model_visibility"]["listed"], [])
            self.assertIn("Qwen/Qwen-Image-2512", recorded["project_model_visibility"]["not_listed"])
            self.assertIn("project_model_visibility", self.current("modelscope"))
            catalog = load_provider_catalog()
            changed = replace(catalog, models=tuple(model for model in catalog.models if model.id != "qwen"))
            with patch("angemedia_gateway.services.provider_connection_test.load_provider_catalog", return_value=changed):
                stale = self.current("modelscope")
            self.assertEqual(stale["evidence_state"], "configuration_changed")
            self.assertNotIn("project_model_visibility", stale)
        with patch.object(evidence, "_now", return_value=epoch + timedelta(seconds=900)):
            expired = self.current("modelscope")
            self.assertEqual(expired["evidence_state"], "expired")
            self.assertNotIn("project_model_visibility", expired)
        self.configure("modelscope", api_key="PRIVATE_KEY_NEW")
        changed_key = self.current("modelscope")
        self.assertEqual(changed_key["evidence_state"], "configuration_changed")
        self.assertNotIn("project_model_visibility", changed_key)

    def test_key_restore_base_url_clear_and_switch_require_retest(self):
        self.probe()
        for values in ({"api_key": "PRIVATE_KEY_NEW"}, {"api_key": "PRIVATE_KEY_ORIGINAL"}, {"base_url_override": "https://example.com/another-v1"}, {"enabled": False}):
            self.configure("siliconflow", **values)
            self.assertEqual(self.current()["evidence_state"], "configuration_changed")
        self.configure("siliconflow", enabled=True)
        result, _ = self.probe()
        self.assertEqual(result["evidence_state"], "current")
        self.client.post("/v1/admin/provider-configs/siliconflow/clear-key")
        self.assertEqual(self.current()["evidence_state"], "configuration_changed")

    def test_environment_key_change_is_detected_without_runtime_write(self):
        self.client.post("/v1/admin/provider-configs/siliconflow/clear-key")
        with patch.object(C, "SILICONFLOW_API_KEY", "PRIVATE_ENV_OLD"):
            self.probe()
        with patch.object(C, "SILICONFLOW_API_KEY", "PRIVATE_ENV_NEW"):
            self.assertEqual(self.current()["evidence_state"], "configuration_changed")

    def test_inherited_agnes_key_and_transport_changes_invalidate_the_right_scope(self):
        self.configure("agnes_image", enabled=True, api_key="PRIVATE_AGNES_KEY")
        self.configure("agnes_video", enabled=True)
        self.probe("agnes_video")
        self.probe()
        self.configure("agnes_image", api_key="PRIVATE_AGNES_NEW")
        self.assertEqual(self.current("agnes_video")["evidence_state"], "configuration_changed")
        self.assertEqual(self.current()["evidence_state"], "current")
        self.probe("agnes_video")
        response = self.client.post("/v1/admin/provider-transport/siliconflow", json={"transport_mode": "direct"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.current()["evidence_state"], "configuration_changed")
        self.assertEqual(self.current("agnes_video")["evidence_state"], "current")
        response = self.client.post("/v1/admin/provider-transport", json={"transport_mode": "explicit_proxy", "proxy_url": "http://user:PRIVATE_PROXY_PASSWORD@example.com:8888"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.current("agnes_video")["evidence_state"], "configuration_changed")

    def test_config_changed_during_http_rejects_the_inflight_success(self):
        result, requests = self.probe(on_request=lambda _: update_provider_runtime_config("siliconflow", api_key="PRIVATE_KEY_NEW"))
        self.assertEqual(requests[0].headers["Authorization"], "Bearer PRIVATE_KEY_ORIGINAL")
        self.assertEqual(result["evidence_state"], "configuration_changed")
        self.assertTrue(result["superseded"])
        self.assertFalse(result["model_list_validated"])

    def test_newer_failure_wins_even_when_the_older_success_finishes_last(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            calls = 0

            async def upstream(provider_id, **_):
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set()
                    await release.wait()
                    return {"provider_id": provider_id, "status": "success", "model_list_validated": True}
                return {"provider_id": provider_id, "status": "failed", "model_list_validated": False}

            with patch.object(evidence, "probe_builtin_provider_connection", side_effect=upstream):
                old = asyncio.create_task(evidence.test_and_record_connection("siliconflow"))
                await entered.wait()
                newer = await evidence.test_and_record_connection("siliconflow")
                release.set()
                older = await old
            self.assertEqual(newer["status"], "failed")
            self.assertEqual(older["status"], "failed")
            self.assertTrue(older["superseded"])
            self.assertEqual(evidence.read_connection_evidence("siliconflow")["status"], "failed")

        asyncio.run(scenario())

    def test_cancelled_probe_has_a_bounded_pending_state(self):
        clock = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

        async def scenario():
            entered = asyncio.Event()

            async def upstream(*_, **__):
                entered.set()
                await asyncio.Event().wait()

            with patch.object(evidence, "_now", return_value=clock), patch.object(evidence, "probe_builtin_provider_connection", side_effect=upstream):
                task = asyncio.create_task(evidence.test_and_record_connection("siliconflow"))
                await entered.wait()
                self.assertEqual(evidence.read_connection_evidence("siliconflow")["evidence_state"], "testing")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(scenario())
        with patch.object(evidence, "_now", return_value=clock + timedelta(seconds=31)):
            self.assertEqual(self.current()["evidence_state"], "expired")

    def test_private_fingerprint_material_is_never_in_the_public_projection(self):
        first, _ = self.probe()
        private = get_connection_evidence("siliconflow")
        rendered = json.dumps(first)
        self.assertNotIn(private["fingerprint"], rendered)
        self.assertNotIn(private["fingerprint_key"], rendered)
        self.probe()
        self.assertNotEqual(private["fingerprint_key"], get_connection_evidence("siliconflow")["fingerprint_key"])

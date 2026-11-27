"""验证只读探测不会把接口可达误报成模型可生成。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from studio_test_client import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import angemedia_gateway.config as C
from angemedia_gateway.db.connection import db_connect
from angemedia_gateway.server import app
from angemedia_gateway.state import ensure_default_admin_user, init_db


class ProviderConnectionEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="provider-connection-evidence-")
        self.addCleanup(self.tmp.cleanup)
        self.database = patch.object(C, "DB_FILE", Path(self.tmp.name) / "test.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        self.env = patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": "test-password-only"})
        self.env.start()
        self.addCleanup(self.env.stop)
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        login = self.client.post("/v1/admin/login", json={"username": "admin", "password": "test-password-only"})
        self.assertEqual(login.status_code, 200)
        updated = self.client.post("/v1/admin/provider-configs/siliconflow", json={"enabled": True, "api_key": "TEST_CHANNEL_KEY_DO_NOT_RETURN"})
        self.assertEqual(updated.status_code, 200)

    def probe(self, upstream: httpx.Response | Exception, provider: str = "siliconflow") -> tuple[dict, list[httpx.Request]]:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if isinstance(upstream, Exception):
                raise upstream
            return upstream

        transport = httpx.MockTransport(respond)
        upstream_client = httpx.AsyncClient(transport=transport)
        # 保留真实路由、运行配置和HTTP错误归一化，只替换外部网络边界。
        with patch("httpx.AsyncClient", return_value=upstream_client):
            response = self.client.post(f"/v1/admin/provider-configs/{provider}/test")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("TEST_CHANNEL_KEY_DO_NOT_RETURN", response.text)
        self.assertNotIn("UPSTREAM_PRIVATE_BODY", response.text)
        self.assertNotIn("PRIVATE_MODEL_ID", response.text)
        return response.json()["data"], requests

    def test_siliconflow_list_is_read_only_and_does_not_claim_generation(self) -> None:
        result, requests = self.probe(httpx.Response(200, json={"data": [{"id": "PRIVATE_MODEL_ID"}, {"id": "PRIVATE_MODEL_ID"}]}))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["verification_scope"], "models_endpoint")
        self.assertTrue(result["model_list_validated"])
        self.assertFalse(result["generation_verified"])
        self.assertEqual(result["model_count"], 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(str(requests[0].url), "https://api.siliconflow.cn/v1/models")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer TEST_CHANNEL_KEY_DO_NOT_RETURN")
        with closing(db_connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)

    def test_empty_valid_catalog_is_not_a_generation_check(self) -> None:
        result, _ = self.probe(httpx.Response(200, json={"data": []}))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["model_count"], 0)
        self.assertFalse(result["generation_verified"])

    def test_modelscope_projects_only_exact_builtin_matches_from_official_get(self) -> None:
        configured = self.client.post("/v1/admin/provider-configs/modelscope", json={
            "enabled": True, "api_key": "TEST_CHANNEL_KEY_DO_NOT_RETURN",
        })
        self.assertEqual(configured.status_code, 200)
        result, requests = self.probe(httpx.Response(200, json={"data": [
            {"id": "Qwen/Qwen-Image-2512"}, {"id": "Qwen/Qwen-Image-2512"},
            {"id": "tongyi-mai/Z-Image"}, {"id": "PRIVATE_MODEL_ID"},
        ]}), "modelscope")
        self.assertEqual([(request.method, str(request.url)) for request in requests], [
            ("GET", "https://api-inference.modelscope.cn/v1/models"),
        ])
        self.assertEqual(requests[0].headers["Authorization"], "Bearer TEST_CHANNEL_KEY_DO_NOT_RETURN")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["model_count"], 3)
        self.assertTrue(result["model_list_validated"])
        self.assertFalse(result["generation_verified"])
        self.assertEqual(result["project_model_visibility"], {
            "listed": ["Qwen/Qwen-Image-2512"],
            "not_listed": ["Qwen/Qwen-Image-Edit", "Qwen/Qwen-Image-Edit-2511", "Tongyi-MAI/Z-Image", "Tongyi-MAI/Z-Image-Turbo",
                           "black-forest-labs/FLUX.1-Krea-dev"],
        })
        response = self.client.get("/v1/admin/catalog")
        self.assertEqual(response.status_code, 200)
        model_scope = next(provider for provider in response.json()["providers"] if provider["id"] == "modelscope")
        directory = model_scope["runtime"]["model_directory"]
        self.assertEqual(directory["project_model_visibility"], result["project_model_visibility"])
        self.assertNotIn("TEST_CHANNEL_KEY_DO_NOT_RETURN", response.text)
        self.assertNotIn("PRIVATE_MODEL_ID", response.text)

    def test_modelscope_does_not_produce_visibility_from_redirects_or_malformed_rows(self) -> None:
        self.client.post("/v1/admin/provider-configs/modelscope", json={
            "enabled": True, "api_key": "TEST_CHANNEL_KEY_DO_NOT_RETURN",
        })
        for response in (
            httpx.Response(302, headers={"location": "https://example.invalid/models"}),
            httpx.Response(200, json={"data": [{"id": "Qwen/Qwen-Image-2512"}, {"id": "  "}]}),
        ):
            with self.subTest(status=response.status_code):
                result, requests = self.probe(response, "modelscope")
                self.assertEqual(len(requests), 1)
                self.assertFalse(result["model_list_validated"])
                self.assertNotIn("project_model_visibility", result)
        configured = self.client.post("/v1/admin/provider-configs/modelscope", json={
            "base_url_override": "https://example.invalid/v1",
        })
        self.assertEqual(configured.status_code, 200)
        result, requests = self.probe(httpx.Response(200, json={"data": []}), "modelscope")
        self.assertEqual(requests, [])
        self.assertEqual(result["status"], "unsupported")
        self.assertNotIn("project_model_visibility", result)
        catalog = self.client.get("/v1/admin/catalog").json()
        model_scope = next(provider for provider in catalog["providers"] if provider["id"] == "modelscope")
        self.assertNotIn("model_directory", model_scope["runtime"])

    def test_http_200_with_invalid_catalog_is_not_success(self) -> None:
        for response in (
            httpx.Response(200, text="UPSTREAM_PRIVATE_BODY"),
            httpx.Response(200, json={"error": "UPSTREAM_PRIVATE_BODY"}),
            httpx.Response(200, json={"data": "UPSTREAM_PRIVATE_BODY"}),
        ):
            with self.subTest(response=response):
                result, _ = self.probe(response)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["model_list_validated"])
                self.assertFalse(result["generation_verified"])
                self.assertIsNone(result["model_count"])

    def test_provider_errors_never_reuse_a_success_observation(self) -> None:
        for response in (
            httpx.Response(401, text="UPSTREAM_PRIVATE_BODY"),
            httpx.Response(403, text="UPSTREAM_PRIVATE_BODY"),
            httpx.Response(429, text="UPSTREAM_PRIVATE_BODY"),
            httpx.ReadTimeout("UPSTREAM_PRIVATE_BODY"),
        ):
            with self.subTest(response=response):
                result, requests = self.probe(response)
                self.assertEqual(len(requests), 1)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["model_list_validated"])
                self.assertFalse(result["generation_verified"])
                self.assertIsNone(result["model_count"])

    def test_stopped_or_unconfigured_provider_does_not_request_network(self) -> None:
        for payload, expected in (({"enabled": False}, "disabled"), ({"enabled": True}, "not_configured")):
            with self.subTest(status=expected):
                self.client.post("/v1/admin/provider-configs/siliconflow", json=payload)
                if expected == "not_configured":
                    self.client.post("/v1/admin/provider-configs/siliconflow/clear-key")
                with patch.object(C, "SILICONFLOW_API_KEY", ""):
                    result, requests = self.probe(httpx.Response(200, json={"data": []}))
                self.assertEqual(result["status"], expected)
                self.assertEqual(requests, [])
                self.assertFalse(result["generation_verified"])


if __name__ == "__main__":
    unittest.main()

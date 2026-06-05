from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("IMAGE_PROXY_STATE_DIR", tempfile.mkdtemp(prefix="angemedia-test-"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from fastapi.testclient import TestClient  # noqa: E402

from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.services.admin_service import AssistantModelFetchError, ProviderModelFetchError  # noqa: E402


class AdminApiWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.created_provider_ids: list[str] = []
        self.login_admin()

    def tearDown(self) -> None:
        for provider_id in self.created_provider_ids:
            self.client.delete(f"/v1/admin/providers/{provider_id}")
        self.client.post(
            "/v1/admin/config",
            json={
                "settings": {
                    "GATEWAY_API_KEY": "",
                    "PUBLIC_BASE_URL": "http://testserver",
                    "OPENAI_IMAGE_API_KEY": "",
                    "ANGE_LLM_API_KEY": "",
                    "ANGE_LLM_BASE_URL": "",
                    "ANGE_LLM_MODEL": "",
                }
            },
        )

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def unique_provider_id(self, prefix: str = "phase-12b") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def create_custom_provider(
        self,
        provider_id: str,
        sort_order: int = 100,
        enabled: bool = True,
        default_model: str = "test-image-model",
    ) -> dict:
        self.created_provider_ids.append(provider_id)
        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": f"Provider {provider_id}",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": f"sk-{provider_id}-secret",
                "default_model": default_model,
                "enabled": enabled,
                "sort_order": sort_order,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def provider_list_row(self, provider_id: str) -> dict:
        response = self.client.get("/v1/admin/providers")
        self.assertEqual(response.status_code, 200, response.text)
        rows = {item["id"]: item for item in response.json()["data"]}
        self.assertIn(provider_id, rows)
        return rows[provider_id]

    def provider_status_row(self, provider_id: str) -> dict:
        response = self.client.get("/v1/admin/provider-status")
        self.assertEqual(response.status_code, 200, response.text)
        rows = {item["id"]: item for item in response.json()["custom"]}
        self.assertIn(provider_id, rows)
        return rows[provider_id]

    def assert_provider_test_state(self, provider_id: str, status: str, error: str | None = None) -> None:
        for row in (self.provider_list_row(provider_id), self.provider_status_row(provider_id)):
            self.assertEqual(row["last_test_status"], status)
            if error is not None:
                self.assertEqual(row["last_error"], error)

    def save_llm_config(
        self,
        base_url: str = "https://llm.example.com/v1",
        api_key: str = "sk-llm-secret-123456",
        model: str = "gpt-test-model",
    ) -> None:
        response = self.client.post(
            "/v1/admin/config",
            json={
                "settings": {
                    "ANGE_LLM_API_KEY": api_key,
                    "ANGE_LLM_BASE_URL": base_url,
                    "ANGE_LLM_MODEL": model,
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    class AssistantHttpResponse:
        def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(self.text)

    def patch_assistant_async_client(
        self,
        response: AssistantHttpResponse | None = None,
        post_error: Exception | None = None,
    ) -> tuple[Any, list[Any]]:
        instances: list[Any] = []

        class FakeAsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.args = args
                self.kwargs = kwargs
                self.posts: list[dict[str, Any]] = []
                instances.append(self)

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

            async def post(self, url: str, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> Any:
                self.posts.append({"url": url, "headers": headers or {}, "json": json or {}})
                if post_error is not None:
                    raise post_error
                return response or AdminApiWriteTest.AssistantHttpResponse()

        return patch("angemedia_gateway.services.admin_service.httpx.AsyncClient", new=FakeAsyncClient), instances

    def test_admin_config_rejects_invalid_values(self) -> None:
        invalid_payloads = [
            {"AUTO_DOWNLOAD_GENERATED": "not-a-bool"},
            {"MEDIA_DOWNLOAD_MAX_BYTES": "not-an-int"},
            {"PUBLIC_BASE_URL": "ftp://example.test"},
        ]

        for settings in invalid_payloads:
            with self.subTest(settings=settings):
                response = self.client.post("/v1/admin/config", json={"settings": settings})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("detail", response.json())

    def test_admin_config_save_persists_and_masks_secret(self) -> None:
        secret = "sk-phase-12b-secret-value-123456"
        public_url = "https://example.com/angemedia"

        response = self.client.post(
            "/v1/admin/config",
            json={
                "settings": {
                    "PUBLIC_BASE_URL": public_url,
                    "OPENAI_IMAGE_API_KEY": secret,
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        settings = response.json()["settings"]
        self.assertEqual(settings["PUBLIC_BASE_URL"], public_url)
        self.assertIn("*", settings["OPENAI_IMAGE_API_KEY"])
        self.assertNotEqual(settings["OPENAI_IMAGE_API_KEY"], secret)
        self.assertNotIn(secret, response.text)

        config = self.client.get("/v1/admin/config")
        self.assertEqual(config.status_code, 200, config.text)
        saved_settings = config.json()["settings"]
        self.assertEqual(saved_settings["PUBLIC_BASE_URL"], public_url)
        self.assertIn("*", saved_settings["OPENAI_IMAGE_API_KEY"])
        self.assertNotIn(secret, config.text)

    def test_gateway_key_generation_modes_and_saved_key_auth(self) -> None:
        unsaved = self.client.post("/v1/admin/gateway-key", json={"save": False})
        self.assertEqual(unsaved.status_code, 200, unsaved.text)
        unsaved_data = unsaved.json()
        self.assertFalse(unsaved_data["saved"])
        self.assertRegex(unsaved_data["key"], r"^am-[a-f0-9]{32}$")

        saved = self.client.post("/v1/admin/gateway-key", json={"save": True})
        self.assertEqual(saved.status_code, 200, saved.text)
        saved_data = saved.json()
        self.assertTrue(saved_data["saved"])
        self.assertIn("****", saved_data["key_preview"])
        self.assertNotIn("key", saved_data)

        unauthenticated = TestClient(app)
        locked = unauthenticated.get("/v1/models")
        self.assertEqual(locked.status_code, 401, locked.text)

        known_key = unsaved_data["key"]
        configured = self.client.post(
            "/v1/admin/config",
            json={"settings": {"GATEWAY_API_KEY": known_key}},
        )
        self.assertEqual(configured.status_code, 200, configured.text)

        authorized = TestClient(app).get("/v1/models", headers={"Authorization": f"Bearer {known_key}"})
        self.assertEqual(authorized.status_code, 200, authorized.text)

    def test_provider_save_validation_errors(self) -> None:
        missing_required = self.client.post(
            "/v1/admin/providers",
            json={"id": self.unique_provider_id("missing"), "name": "Missing Required"},
        )
        self.assertEqual(missing_required.status_code, 400, missing_required.text)
        self.assertEqual(missing_required.json()["detail"], "base_url 和 default_model 必填")

        private_url = self.client.post(
            "/v1/admin/providers",
            json={
                "id": self.unique_provider_id("private"),
                "name": "Private URL",
                "base_url": "http://localhost:9890/v1",
                "default_model": "private-model",
            },
        )
        self.assertEqual(private_url.status_code, 400, private_url.text)
        self.assertIn("localhost", private_url.json()["detail"])

    def test_custom_provider_create_masks_key_toggle_and_delete(self) -> None:
        provider_id = self.unique_provider_id()
        self.created_provider_ids.append(provider_id)
        secret = "sk-custom-provider-secret-123456"

        created = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": "Phase 12B Provider",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": secret,
                "default_model": "test-image-model",
                "enabled": True,
                "sort_order": 123,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        created_data = created.json()["data"]
        self.assertEqual(created_data["id"], provider_id)
        self.assertNotEqual(created_data["api_key"], secret)
        self.assertIn("*", created_data["api_key"])

        providers = self.client.get("/v1/admin/providers")
        self.assertEqual(providers.status_code, 200, providers.text)
        indexed = {item["id"]: item for item in providers.json()["data"]}
        self.assertIn(provider_id, indexed)
        self.assertNotEqual(indexed[provider_id]["api_key"], secret)
        self.assertIn("*", indexed[provider_id]["api_key"])
        self.assertNotIn(secret, providers.text)

        disabled = self.client.post(f"/v1/admin/providers/{provider_id}/enabled", json={"enabled": False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["data"]["enabled"])

        enabled = self.client.post(f"/v1/admin/providers/{provider_id}/enabled", json={"enabled": True})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["data"]["enabled"])

        deleted = self.client.delete(f"/v1/admin/providers/{provider_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["ok"])
        self.created_provider_ids.remove(provider_id)

    def test_provider_delete_missing_and_builtin_sort_errors(self) -> None:
        missing = self.client.delete(f"/v1/admin/providers/{self.unique_provider_id('missing')}")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"], "自定义渠道不存在")

        built_in_sort = self.client.post("/v1/admin/providers/siliconflow/sort", json={"sort_order": 99})
        self.assertEqual(built_in_sort.status_code, 400, built_in_sort.text)
        self.assertEqual(built_in_sort.json()["detail"], "内置渠道排序固定；默认链路顺序由网关维护")

    def test_custom_provider_sort_success_updates_list_order(self) -> None:
        first_id = self.unique_provider_id("sort-first")
        second_id = self.unique_provider_id("sort-second")
        self.create_custom_provider(first_id, sort_order=200)
        self.create_custom_provider(second_id, sort_order=300)

        sorted_response = self.client.post(f"/v1/admin/providers/{first_id}/sort", json={"sort_order": 400})
        self.assertEqual(sorted_response.status_code, 200, sorted_response.text)
        sorted_data = sorted_response.json()["data"]
        self.assertEqual(sorted_data["id"], first_id)
        self.assertEqual(sorted_data["sort_order"], 400)

        providers = self.client.get("/v1/admin/providers")
        self.assertEqual(providers.status_code, 200, providers.text)
        provider_rows = providers.json()["data"]
        indexed = {item["id"]: item for item in provider_rows}
        self.assertEqual(indexed[first_id]["sort_order"], 400)

        pair_order = [item["id"] for item in provider_rows if item["id"] in {first_id, second_id}]
        self.assertEqual(pair_order, [second_id, first_id])

    def test_provider_sort_and_enable_missing_errors_keep_messages(self) -> None:
        missing_id = self.unique_provider_id("missing-provider")

        invalid_sort = self.client.post(f"/v1/admin/providers/{missing_id}/sort", json={"sort_order": "abc"})
        self.assertEqual(invalid_sort.status_code, 400, invalid_sort.text)
        self.assertEqual(invalid_sort.json()["detail"], "排序值必须是整数")

        missing_enabled = self.client.post(f"/v1/admin/providers/{missing_id}/enabled", json={"enabled": False})
        self.assertEqual(missing_enabled.status_code, 404, missing_enabled.text)
        self.assertEqual(missing_enabled.json()["detail"], "自定义渠道不存在")

    def test_builtin_provider_toggle_response_and_custom_provider_isolation(self) -> None:
        status = self.client.get("/v1/admin/provider-status")
        self.assertEqual(status.status_code, 200, status.text)
        siliconflow = next(item for item in status.json()["built_in"] if item["id"] == "siliconflow")
        original_enabled = bool(siliconflow["enabled"])

        custom_id = self.unique_provider_id("builtin-isolation")
        self.create_custom_provider(custom_id, sort_order=321, enabled=True)
        before = self.client.get("/v1/admin/providers")
        self.assertEqual(before.status_code, 200, before.text)
        before_custom = {item["id"]: item for item in before.json()["data"]}[custom_id]

        try:
            disabled = self.client.post("/v1/admin/providers/siliconflow/enabled", json={"enabled": False})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            disabled_body = disabled.json()
            self.assertTrue(disabled_body["ok"])
            disabled_data = disabled_body["data"]
            self.assertEqual(disabled_data["id"], "siliconflow")
            self.assertEqual(disabled_data["type"], "built_in")
            self.assertEqual(disabled_data["source"], "built_in")
            self.assertFalse(disabled_data["enabled"])
            self.assertIn("ready", disabled_data)
            self.assertIn("configured", disabled_data)

            after_disable = self.client.get("/v1/admin/providers")
            self.assertEqual(after_disable.status_code, 200, after_disable.text)
            disabled_custom = {item["id"]: item for item in after_disable.json()["data"]}[custom_id]
            self.assertEqual(disabled_custom["enabled"], before_custom["enabled"])
            self.assertEqual(disabled_custom["sort_order"], before_custom["sort_order"])
            self.assertEqual(disabled_custom["default_model"], before_custom["default_model"])

            enabled = self.client.post("/v1/admin/providers/siliconflow/enabled", json={"enabled": True})
            self.assertEqual(enabled.status_code, 200, enabled.text)
            enabled_body = enabled.json()
            self.assertTrue(enabled_body["ok"])
            enabled_data = enabled_body["data"]
            self.assertEqual(enabled_data["id"], "siliconflow")
            self.assertEqual(enabled_data["type"], "built_in")
            self.assertEqual(enabled_data["source"], "built_in")
            self.assertTrue(enabled_data["enabled"])

            after_enable = self.client.get("/v1/admin/providers")
            self.assertEqual(after_enable.status_code, 200, after_enable.text)
            enabled_custom = {item["id"]: item for item in after_enable.json()["data"]}[custom_id]
            self.assertEqual(enabled_custom["enabled"], before_custom["enabled"])
            self.assertEqual(enabled_custom["sort_order"], before_custom["sort_order"])
            self.assertEqual(enabled_custom["default_model"], before_custom["default_model"])
        finally:
            self.client.post("/v1/admin/providers/siliconflow/enabled", json={"enabled": original_enabled})

    def test_provider_test_models_success_updates_status(self) -> None:
        provider_id = self.unique_provider_id("test-ok")
        secret = f"sk-{provider_id}-secret"
        self.create_custom_provider(provider_id, default_model="target-model")

        fetch_models = AsyncMock(return_value=(["target-model", "other-model"], 37))
        with patch("angemedia_gateway.services.admin_service.fetch_openai_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["models"], ["target-model", "other-model"])
        self.assertEqual(body["elapsed_ms"], 37)
        self.assertEqual(body["data"]["last_test_status"], "ok")
        self.assertEqual(body["data"]["last_response_ms"], 37)
        self.assertNotIn(secret, response.text)
        fetch_models.assert_awaited_once_with("https://example.com/v1", secret)
        self.assert_provider_test_state(provider_id, "ok", "")

    def test_provider_test_models_missing_default_updates_model_not_listed(self) -> None:
        provider_id = self.unique_provider_id("test-missing")
        self.create_custom_provider(provider_id, default_model="target-model")

        fetch_models = AsyncMock(return_value=(["other-model"], 42))
        with patch("angemedia_gateway.services.admin_service.fetch_openai_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["models"], ["other-model"])
        self.assertEqual(body["elapsed_ms"], 42)
        self.assertEqual(body["data"]["last_test_status"], "model_not_listed")
        self.assertEqual(body["data"]["last_error"], "默认模型不在 /models 返回列表中")
        self.assert_provider_test_state(provider_id, "model_not_listed", "默认模型不在 /models 返回列表中")

    def test_provider_test_models_http_failure_writes_failed_before_502(self) -> None:
        provider_id = self.unique_provider_id("test-http-fail")
        self.create_custom_provider(provider_id, default_model="target-model")
        detail = "模型列表拉取失败：HTTP 500 {\"error\":\"bad\"}"

        fetch_models = AsyncMock(side_effect=ProviderModelFetchError(detail))
        with patch("angemedia_gateway.services.admin_service.fetch_openai_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], detail)
        self.assert_provider_test_state(provider_id, "failed", detail)

    def test_provider_test_models_plain_exception_returns_failed_payload(self) -> None:
        provider_id = self.unique_provider_id("test-exception")
        self.create_custom_provider(provider_id, default_model="target-model")

        fetch_models = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("angemedia_gateway.services.admin_service.fetch_openai_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["message"], "连接测试失败：boom")
        self.assertEqual(body["data"]["last_test_status"], "failed")
        self.assert_provider_test_state(provider_id, "failed", "boom")

    def test_builtin_provider_test_response_without_external_fetch_or_custom_status_write(self) -> None:
        status = self.client.get("/v1/admin/provider-status")
        self.assertEqual(status.status_code, 200, status.text)
        built_in_rows = {item["id"]: item for item in status.json()["built_in"]}
        original_siliconflow_enabled = bool(built_in_rows["siliconflow"]["enabled"])
        original_openai_image_enabled = bool(built_in_rows["openai_image"]["enabled"])

        custom_id = self.unique_provider_id("builtin-test")
        self.create_custom_provider(custom_id)
        before = self.provider_list_row(custom_id)

        fetch_models = AsyncMock(return_value=(["should-not-be-used"], 1))
        try:
            self.client.post("/v1/admin/config", json={"settings": {"OPENAI_IMAGE_API_KEY": ""}})
            self.client.post("/v1/admin/providers/siliconflow/enabled", json={"enabled": True})
            self.client.post("/v1/admin/providers/openai_image/enabled", json={"enabled": True})

            with patch("angemedia_gateway.services.admin_service.fetch_openai_model_ids", new=fetch_models):
                ready = self.client.post("/v1/admin/providers/siliconflow/test")
                missing = self.client.post("/v1/admin/providers/openai_image/test")

            self.assertEqual(ready.status_code, 200, ready.text)
            ready_body = ready.json()
            self.assertTrue(ready_body["ok"])
            self.assertEqual(ready_body["data"]["id"], "siliconflow")
            self.assertEqual(ready_body["message"], "渠道已启用且关键配置存在")

            self.assertEqual(missing.status_code, 200, missing.text)
            missing_body = missing.json()
            self.assertFalse(missing_body["ok"])
            self.assertEqual(missing_body["data"]["id"], "openai_image")
            self.assertEqual(missing_body["message"], "渠道未启用或缺少关键配置")
            fetch_models.assert_not_awaited()

            after = self.provider_list_row(custom_id)
            self.assertEqual(after["last_test_status"], before["last_test_status"])
            self.assertEqual(after["last_response_ms"], before["last_response_ms"])
            self.assertEqual(after["last_error"], before["last_error"])
        finally:
            self.client.post("/v1/admin/providers/siliconflow/enabled", json={"enabled": original_siliconflow_enabled})
            self.client.post("/v1/admin/providers/openai_image/enabled", json={"enabled": original_openai_image_enabled})

    def test_assistant_models_success_returns_current_fields(self) -> None:
        self.save_llm_config(base_url="https://llm.example.com/v1/", api_key="sk-llm-secret-123456")
        fetch_models = AsyncMock(return_value=(["gpt-test-model", "gpt-alt-model"], 31))

        with patch("angemedia_gateway.services.admin_service.fetch_assistant_model_ids", new=fetch_models):
            response = self.client.get("/v1/admin/assistant/models")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {"data": ["gpt-test-model", "gpt-alt-model"], "elapsed_ms": 31, "base_url": "https://llm.example.com/v1"},
        )
        fetch_models.assert_awaited_once_with("https://llm.example.com/v1", "sk-llm-secret-123456")

    def test_assistant_models_missing_base_url_keeps_error_message(self) -> None:
        self.save_llm_config(base_url="", model="gpt-test-model")
        fetch_models = AsyncMock(return_value=(["should-not-be-used"], 1))

        with patch("angemedia_gateway.services.admin_service.fetch_assistant_model_ids", new=fetch_models):
            response = self.client.get("/v1/admin/assistant/models")

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "请先配置 LLM 接口地址")
        fetch_models.assert_not_awaited()

    def test_assistant_models_http_failure_keeps_502_message(self) -> None:
        self.save_llm_config(base_url="https://llm.example.com/v1")
        detail = "模型列表拉取失败：HTTP 503 {\"error\":\"bad\"}"
        fetch_models = AsyncMock(side_effect=AssistantModelFetchError(detail))

        with patch("angemedia_gateway.services.admin_service.fetch_assistant_model_ids", new=fetch_models):
            response = self.client.get("/v1/admin/assistant/models")

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], detail)
        self.assertTrue(response.json()["detail"].startswith("模型列表拉取失败：HTTP "))

    def test_assistant_test_success_returns_preview_fields(self) -> None:
        self.save_llm_config(base_url="https://llm.example.com/v1/", api_key="sk-llm-secret-123456", model="gpt-test-model")
        content = "连接正常。" + ("x" * 240)
        fake_response = self.AssistantHttpResponse(
            status_code=200,
            payload={"choices": [{"message": {"content": content}}]},
            text='{"ok":true}',
        )
        client_patch, instances = self.patch_assistant_async_client(fake_response)

        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={"model": "gpt-override-model"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["model"], "gpt-override-model")
        self.assertIsInstance(body["elapsed_ms"], int)
        self.assertEqual(body["preview"], content[:200])
        self.assertNotIn("sk-llm-secret-123456", response.text)

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].kwargs["timeout"], 30)
        post = instances[0].posts[0]
        self.assertEqual(post["url"], "https://llm.example.com/v1/chat/completions")
        self.assertEqual(post["headers"]["Authorization"], "Bearer sk-llm-secret-123456")
        self.assertEqual(post["headers"]["Content-Type"], "application/json")
        self.assertEqual(post["json"]["model"], "gpt-override-model")
        self.assertEqual(post["json"]["temperature"], 0.1)
        self.assertEqual(post["json"]["max_tokens"], 48)

    def test_assistant_test_missing_base_url_or_model_keeps_error_message(self) -> None:
        cases = [
            {"settings": {"base_url": "", "model": "gpt-test-model"}, "payload": {}},
            {"settings": {"base_url": "https://llm.example.com/v1", "model": ""}, "payload": {}},
        ]

        for case in cases:
            with self.subTest(case=case):
                self.save_llm_config(base_url=case["settings"]["base_url"], model=case["settings"]["model"])
                client_patch, instances = self.patch_assistant_async_client()
                with client_patch:
                    response = self.client.post("/v1/admin/assistant/test", json=case["payload"])

                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "请先配置 LLM 接口地址和模型")
                self.assertEqual(instances, [])

    def test_assistant_test_http_failure_keeps_502_message(self) -> None:
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        fake_response = self.AssistantHttpResponse(status_code=503, payload={}, text='{"error":"bad"}')
        client_patch, _ = self.patch_assistant_async_client(fake_response)

        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], 'LLM 测试失败：HTTP 503 {"error":"bad"}')
        self.assertTrue(response.json()["detail"].startswith("LLM 测试失败：HTTP "))

    def test_assistant_test_plain_exception_keeps_502_message(self) -> None:
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        client_patch, _ = self.patch_assistant_async_client(post_error=RuntimeError("boom"))

        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "LLM 测试失败：boom")
        self.assertTrue(response.json()["detail"].startswith("LLM 测试失败："))

    def test_mock_provider_appears_in_provider_status(self) -> None:
        """验证 Mock Provider 出现在 provider-status 的 built-in 列表中。"""
        response = self.client.get("/v1/admin/provider-status")
        self.assertEqual(response.status_code, 200, response.text)

        built_in = response.json()["built_in"]
        mock_rows = [row for row in built_in if row["id"] == "mock"]
        self.assertEqual(len(mock_rows), 1, "Mock Provider 应出现在 built-in 列表中")

        mock_row = mock_rows[0]
        self.assertEqual(mock_row["id"], "mock")
        self.assertTrue(mock_row["configured"], "Mock Provider 应始终 configured=True")
        self.assertTrue(mock_row["enabled"], "Mock Provider 应默认 enabled=True")
        self.assertTrue(mock_row["ready"], "Mock Provider 应默认 ready=True")
        self.assertEqual(mock_row["default_model"], "mock-model")
        self.assertEqual(mock_row["provider_type"], "built_in_image")
        self.assertEqual(mock_row["category"], "图片")
        self.assertIn("mock", mock_row["aliases"])

    def test_mock_provider_not_polluting_custom_providers(self) -> None:
        """验证 Mock Provider 不污染 custom providers 列表。"""
        response = self.client.get("/v1/admin/providers")
        self.assertEqual(response.status_code, 200, response.text)

        custom_ids = [item["id"] for item in response.json()["data"]]
        self.assertNotIn("mock", custom_ids, "Mock Provider 不应出现在 custom providers 列表中")


if __name__ == "__main__":
    unittest.main()

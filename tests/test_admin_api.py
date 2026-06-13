from __future__ import annotations

import json
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

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.providers.errors import BackendUnavailable  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.services.admin_service import AssistantModelFetchError, ProviderModelFetchError  # noqa: E402
from angemedia_gateway.state import ensure_default_admin_user, init_db, verify_admin_login  # noqa: E402


SAFE_PROVIDER_SUMMARY_FIELDS = {
    "id",
    "name",
    "provider_type",
    "enabled",
    "api_key_configured",
    "default_model",
    "sort_order",
    "last_test_status",
    "last_response_ms",
    "last_test_at",
    "created_at",
    "updated_at",
}

FORBIDDEN_PROVIDER_SUMMARY_FIELDS = {
    "api_key",
    "_api_key",
    "key_hash",
    "secret",
    "token",
    "password",
    "base_url",
    "status_url",
    "quota_url",
    "last_error",
    "raw",
    "raw_body",
    "raw_response",
    "raw_error",
    "exception",
    "stack",
}


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
        list_row = self.provider_list_row(provider_id)
        self.assertEqual(list_row["last_test_status"], status)
        status_row = self.provider_status_row(provider_id)
        self.assertEqual(status_row["last_test_status"], status)
        if error is not None:
            self.assertEqual(status_row["last_error"], error)

    def assert_provider_safe_summary(self, item: dict, *, api_key_configured: bool) -> None:
        self.assertIsInstance(item, dict)
        self.assertLessEqual(set(item), SAFE_PROVIDER_SUMMARY_FIELDS)
        self.assertTrue(FORBIDDEN_PROVIDER_SUMMARY_FIELDS.isdisjoint(item))
        self.assertIn("api_key_configured", item)
        self.assertIs(type(item["api_key_configured"]), bool)
        self.assertIs(item["api_key_configured"], api_key_configured)

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

    def patch_provider_status_async_client(
        self,
        response: AssistantHttpResponse | None = None,
    ) -> tuple[Any, list[Any]]:
        instances: list[Any] = []

        class FakeAsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.args = args
                self.kwargs = kwargs
                self.gets: list[dict[str, Any]] = []
                instances.append(self)

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

            async def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
                self.gets.append({"url": url, "headers": headers or {}})
                return response or AdminApiWriteTest.AssistantHttpResponse(status_code=200, text='{"ok":true}')

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

    def test_catalog_requires_admin_auth(self) -> None:
        response = TestClient(app).get("/v1/admin/catalog")
        self.assertIn(response.status_code, (401, 403), response.text)

    def test_catalog_api_returns_safe_provider_and_model_capabilities(self) -> None:
        response = self.client.get("/v1/admin/catalog")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        providers = {item["id"]: item for item in body["providers"]}
        models = {item["id"]: item for item in body["models"]}

        self.assertEqual(body["object"], "provider_catalog")
        self.assertIn("agnes_video", providers)
        self.assertEqual(providers["agnes_video"]["display_name"], "Agnes Video")
        self.assertEqual(providers["agnes_video"]["media_type"], "video")
        self.assertEqual(providers["agnes_video"]["status"], "release")
        self.assertEqual(providers["agnes_video"]["config_enabled_key"], "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED")
        self.assertTrue(providers["agnes_video"]["selectable"])

        video_model = models["agnes-video-v2-0"]
        self.assertEqual(video_model["provider_id"], "agnes_video")
        self.assertEqual(video_model["media_type"], "video")
        self.assertEqual(video_model["status"], "release")
        self.assertTrue(video_model["selectable"])
        self.assertTrue(video_model["capabilities"]["text_to_video"])
        self.assertTrue(video_model["capabilities"]["image_to_video"])
        self.assertEqual(video_model["params"]["width"], "integer")
        self.assertEqual(video_model["ref_inputs"]["image"], "optional")
        self.assertIn("1152x768", video_model["size_presets"])
        self.assertIn("release_path", video_model["tags"])

        pollinations = providers["pollinations"]
        pollinations_model = models["pollinations"]
        self.assertEqual(pollinations["status"], "experimental")
        self.assertFalse(pollinations["enabled_default"])
        self.assertIsNone(pollinations["default_chain_order"])
        self.assertEqual(pollinations_model["status"], "experimental")
        self.assertIsNone(pollinations_model["default_chain_order"])

        for forbidden in ("credential_keys", "api_key", "AGNES_API_KEY", "token", "password", "secret"):
            self.assertNotIn(forbidden, response.text)

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
        self.assertNotIn("api_key", created_data)
        self.assert_provider_safe_summary(created_data, api_key_configured=True)

        providers = self.client.get("/v1/admin/providers")
        self.assertEqual(providers.status_code, 200, providers.text)
        indexed = {item["id"]: item for item in providers.json()["data"]}
        self.assertIn(provider_id, indexed)
        self.assertNotIn("api_key", indexed[provider_id])
        self.assert_provider_safe_summary(indexed[provider_id], api_key_configured=True)
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
            self.assertFalse(disabled_data["enabled"])
            self.assert_provider_safe_summary(disabled_data, api_key_configured=True)

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
            self.assertTrue(enabled_data["enabled"])
            self.assert_provider_safe_summary(enabled_data, api_key_configured=True)

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
        with patch("angemedia_gateway.services.provider_admin_service.fetch_openai_compatible_model_ids", new=fetch_models):
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
        with patch("angemedia_gateway.services.provider_admin_service.fetch_openai_compatible_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["models"], ["other-model"])
        self.assertEqual(body["elapsed_ms"], 42)
        self.assertEqual(body["status"], "model_not_found")
        self.assertEqual(body["data"]["last_test_status"], "model_not_found")
        self.assert_provider_test_state(
            provider_id,
            "model_not_found",
            "Provider is reachable, but the default model was not listed.",
        )

    def test_provider_test_models_http_failure_writes_failed_before_502(self) -> None:
        provider_id = self.unique_provider_id("test-http-fail")
        self.create_custom_provider(provider_id, default_model="target-model")
        marker = "UPSTREAM_BODY_SHOULD_NOT_LEAK"

        fetch_models = AsyncMock(side_effect=BackendUnavailable(marker, status_code=500))
        with patch("angemedia_gateway.services.provider_admin_service.fetch_openai_compatible_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertNotIn(marker, response.text)
        self.assert_provider_test_state(provider_id, "upstream_unavailable", "Provider upstream is unavailable.")

    def test_provider_test_models_plain_exception_returns_failed_payload(self) -> None:
        provider_id = self.unique_provider_id("test-exception")
        self.create_custom_provider(provider_id, default_model="target-model")

        fetch_models = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("angemedia_gateway.services.provider_admin_service.fetch_openai_compatible_model_ids", new=fetch_models):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertEqual(body["message"], "Provider upstream is unavailable.")
        self.assertEqual(body["data"]["last_test_status"], "upstream_unavailable")
        self.assert_provider_test_state(provider_id, "upstream_unavailable", "Provider upstream is unavailable.")

    def test_builtin_provider_test_response_without_external_fetch_or_custom_status_write(self) -> None:
        status = self.client.get("/v1/admin/provider-status")
        self.assertEqual(status.status_code, 200, status.text)
        built_in_rows = {item["id"]: item for item in status.json()["built_in"]}
        original_siliconflow_enabled = bool(built_in_rows["siliconflow"]["enabled"])
        original_openai_image_enabled = bool(built_in_rows["openai_image"]["enabled"])

        custom_id = self.unique_provider_id("builtin-test")
        self.create_custom_provider(custom_id)
        before = self.provider_list_row(custom_id)
        before_status = self.provider_status_row(custom_id)

        fetch_models = AsyncMock(return_value=(["should-not-be-used"], 1))
        try:
            self.client.post("/v1/admin/config", json={"settings": {"OPENAI_IMAGE_API_KEY": ""}})
            self.client.post("/v1/admin/providers/siliconflow/enabled", json={"enabled": True})
            self.client.post("/v1/admin/providers/openai_image/enabled", json={"enabled": True})

            with patch("angemedia_gateway.services.provider_admin_service.fetch_openai_compatible_model_ids", new=fetch_models):
                ready = self.client.post("/v1/admin/providers/siliconflow/test")
                missing = self.client.post("/v1/admin/providers/openai_image/test")

            self.assertEqual(ready.status_code, 409, ready.text)
            ready_detail = ready.json()["detail"]
            self.assertEqual(ready_detail["code"], "test_not_supported")
            self.assertEqual(ready_detail["status"], "test_not_supported")

            self.assertEqual(missing.status_code, 409, missing.text)
            missing_detail = missing.json()["detail"]
            self.assertEqual(missing_detail["code"], "test_not_supported")
            self.assertEqual(missing_detail["status"], "test_not_supported")
            fetch_models.assert_not_awaited()

            after = self.provider_list_row(custom_id)
            after_status = self.provider_status_row(custom_id)
            self.assertEqual(after["last_test_status"], before["last_test_status"])
            self.assertEqual(after["last_response_ms"], before["last_response_ms"])
            self.assertEqual(after_status["last_error"], before_status["last_error"])
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
        self.assertEqual(response.json()["detail"], 'LLM 测试失败：HTTP 503')
        self.assertTrue(response.json()["detail"].startswith("LLM 测试失败：HTTP "))

    def test_assistant_test_plain_exception_keeps_502_message(self) -> None:
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        client_patch, _ = self.patch_assistant_async_client(post_error=RuntimeError("boom"))

        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "LLM 测试失败")

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

    def test_provider_status_and_quota_requests_do_not_send_provider_api_key(self) -> None:
        """status_url / quota_url 外呼不应携带 provider API key。"""
        provider_id = self.unique_provider_id("status-auth")
        provider_secret = f"sk-{provider_id}-secret-token-123456"
        self.created_provider_ids.append(provider_id)
        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": "Status Auth Provider",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": provider_secret,
                "default_model": "safe-model",
                "status_url": "https://example.com/status",
                "quota_url": "https://example.com/quota",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        client_patch, instances = self.patch_provider_status_async_client()
        with client_patch:
            status_response = self.client.get("/v1/admin/provider-status")

        self.assertEqual(status_response.status_code, 200, status_response.text)
        self.assertNotIn(provider_secret, status_response.text)
        calls = [call for instance in instances for call in instance.gets]
        requested_urls = {call["url"] for call in calls}
        self.assertIn("https://example.com/status", requested_urls)
        self.assertIn("https://example.com/quota", requested_urls)
        for call in calls:
            rendered_headers = json.dumps(call["headers"], sort_keys=True)
            self.assertNotIn("Authorization", call["headers"])
            self.assertNotIn(provider_secret, rendered_headers)
            self.assertNotIn(f"Bearer {provider_secret}", rendered_headers)

    def test_mock_provider_not_polluting_custom_providers(self) -> None:
        """验证 Mock Provider 不污染 custom providers 列表。"""
        response = self.client.get("/v1/admin/providers")
        self.assertEqual(response.status_code, 200, response.text)

        custom_ids = [item["id"] for item in response.json()["data"]]
        self.assertNotIn("mock", custom_ids, "Mock Provider 不应出现在 custom providers 列表中")

    def test_list_custom_providers_sort_and_secret_summary(self) -> None:
        """GET /providers 排序正确，并只返回 api_key_configured 派生状态。"""
        first_id = self.unique_provider_id("list-first")
        second_id = self.unique_provider_id("list-second")
        self.create_custom_provider(first_id, sort_order=300)
        self.create_custom_provider(second_id, sort_order=100)

        resp = self.client.get("/v1/admin/providers")
        self.assertEqual(resp.status_code, 200, resp.text)
        rows = resp.json()["data"]
        self.assertGreaterEqual(len(rows), 2)
        # sort_order ASC, created_at DESC → second (100) 在 first (300) 前
        ids = [r["id"] for r in rows]
        self.assertLess(ids.index(second_id), ids.index(first_id))
        # api_key 不回显，masked key 也不出现在响应 shape 中。
        for r in rows:
            self.assertNotIn("api_key", r)
            self.assertIn("api_key_configured", r)
            self.assertIs(type(r["api_key_configured"]), bool)

    def test_provider_safe_summary_list_contract_excludes_sensitive_fields(self) -> None:
        """Product RC: GET /providers 应返回 Studio-safe summary，而不是 DB row。"""
        from angemedia_gateway.state import update_custom_provider_test

        provider_id = self.unique_provider_id("safe-list")
        self.created_provider_ids.append(provider_id)
        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": "Safe Summary Provider",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": f"sk-{provider_id}-secret",
                "default_model": "safe-model",
                "enabled": True,
                "status_url": "https://example.com/status",
                "quota_url": "https://example.com/quota",
                "sort_order": 321,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        update_custom_provider_test(provider_id, "failed", 456, "raw upstream provider error")

        providers = self.client.get("/v1/admin/providers")
        self.assertEqual(providers.status_code, 200, providers.text)
        rows = {item["id"]: item for item in providers.json()["data"]}
        self.assertIn(provider_id, rows)
        self.assert_provider_safe_summary(rows[provider_id], api_key_configured=True)

    def test_provider_safe_summary_list_contract_reports_key_configured_as_bool(self) -> None:
        """Product RC: api_key_configured 只能是布尔派生，不返回 masked key。"""
        with_key_id = self.unique_provider_id("safe-key")
        without_key_id = self.unique_provider_id("safe-nokey")
        self.created_provider_ids.extend([with_key_id, without_key_id])

        with_key = self.client.post(
            "/v1/admin/providers",
            json={
                "id": with_key_id,
                "name": "With Key",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": f"sk-{with_key_id}-secret",
                "default_model": "safe-model",
            },
        )
        self.assertEqual(with_key.status_code, 200, with_key.text)

        without_key = self.client.post(
            "/v1/admin/providers",
            json={
                "id": without_key_id,
                "name": "Without Key",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "default_model": "safe-model",
            },
        )
        self.assertEqual(without_key.status_code, 200, without_key.text)

        providers = self.client.get("/v1/admin/providers")
        self.assertEqual(providers.status_code, 200, providers.text)
        rows = {item["id"]: item for item in providers.json()["data"]}
        self.assert_provider_safe_summary(rows[with_key_id], api_key_configured=True)
        self.assert_provider_safe_summary(rows[without_key_id], api_key_configured=False)

    def test_provider_safe_summary_create_response_contract(self) -> None:
        """Product RC: 创建成功响应不回显 raw/masked secret 或 URL 字段。"""
        provider_id = self.unique_provider_id("safe-create")
        self.created_provider_ids.append(provider_id)

        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": "Safe Create",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": f"sk-{provider_id}-secret",
                "default_model": "safe-model",
                "status_url": "https://example.com/status",
                "quota_url": "https://example.com/quota",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_provider_safe_summary(response.json()["data"], api_key_configured=True)

    def test_provider_safe_summary_enable_and_sort_response_contract(self) -> None:
        """Product RC: enable/sort 写操作也只返回 Studio-safe summary。"""
        provider_id = self.unique_provider_id("safe-write")
        self.create_custom_provider(provider_id, sort_order=100, enabled=True)

        disabled = self.client.post(f"/v1/admin/providers/{provider_id}/enabled", json={"enabled": False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assert_provider_safe_summary(disabled.json()["data"], api_key_configured=True)

        sorted_response = self.client.post(f"/v1/admin/providers/{provider_id}/sort", json={"sort_order": 222})
        self.assertEqual(sorted_response.status_code, 200, sorted_response.text)
        self.assert_provider_safe_summary(sorted_response.json()["data"], api_key_configured=True)

    # ── Provider Delete Safety Coverage ────────────────────

    def test_delete_provider_with_gateway_api_key_returns_403(self) -> None:
        """API 模式 API Key 不能调用 DELETE /v1/admin/providers，必须 403。"""
        key_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "delete-test-key"})
        self.assertEqual(key_resp.status_code, 200, key_resp.text)
        api_key = key_resp.json()["data"]["key"]

        provider_id = self.unique_provider_id("gw-delete")
        self.create_custom_provider(provider_id)

        # Use a separate client WITHOUT admin session to test pure API key auth
        api_client = TestClient(app)
        response = api_client.delete(
            f"/v1/admin/providers/{provider_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("网关访问密钥不能访问管理后台", response.text)

        # provider must still exist after forbidden attempt
        self.provider_list_row(provider_id)

    def test_delete_builtin_provider_siliconflow_returns_404(self) -> None:
        """删除 builtin provider siliconflow 必须返回 404，不能删除内置渠道。"""
        response = self.client.delete("/v1/admin/providers/siliconflow")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "自定义渠道不存在")

        # builtin provider must still be listed
        status = self.client.get("/v1/admin/provider-status")
        self.assertEqual(status.status_code, 200, status.text)
        builtin_ids = [row["id"] for row in status.json()["built_in"]]
        self.assertIn("siliconflow", builtin_ids)

    def test_custom_provider_create_delete_second_delete_returns_404(self) -> None:
        """create → delete 成功 → second delete 必须返回 404。"""
        provider_id = self.unique_provider_id("dbl-del")
        self.created_provider_ids.append(provider_id)
        self.create_custom_provider(provider_id)

        first = self.client.delete(f"/v1/admin/providers/{provider_id}")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["ok"])
        self.created_provider_ids.remove(provider_id)

        second = self.client.delete(f"/v1/admin/providers/{provider_id}")
        self.assertEqual(second.status_code, 404, second.text)
        self.assertEqual(second.json()["detail"], "自定义渠道不存在")

    def test_delete_provider_response_is_safe_minimal_dict(self) -> None:
        """delete 响应必须严格只含 {"ok": true}，不泄露任何 secret 或 config。"""
        provider_id = self.unique_provider_id("safe-del")
        self.created_provider_ids.append(provider_id)
        self.create_custom_provider(provider_id, default_model="del-model")

        response = self.client.delete(f"/v1/admin/providers/{provider_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body.keys()), {"ok"})
        self.assertTrue(body["ok"])
        response_text = response.text
        for forbidden in ("api_key", "key_hash", "secret", "token", "base_url", "sk-"):
            self.assertNotIn(forbidden, response_text, f"delete response must not contain '{forbidden}'")
        self.created_provider_ids.remove(provider_id)

    def test_delete_provider_returns_400_for_invalid_id_format(self) -> None:
        """provider_id 含非法字符时必须返回 400，不能进入 SQL。"""
        for bad_id in ["a.b", "a~b", "a*b", "a+b"]:
            with self.subTest(bad_id=bad_id):
                response = self.client.delete(f"/v1/admin/providers/{bad_id}")
                self.assertEqual(response.status_code, 400, f"bad_id={bad_id!r}: {response.text}")
                self.assertIn("渠道 ID", response.text)

    # ── Safe Envelope Security Tests ───────────────────────

    def test_provider_status_quota_no_raw_body_leak(self) -> None:
        """status/quota 响应不得包含上游 raw body。"""
        marker = "RAW_STATUS_BODY_SHOULD_NOT_LEAK"
        provider_id = self.unique_provider_id("safe-status")
        self.created_provider_ids.append(provider_id)
        resp = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": f"Provider {provider_id}",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": f"sk-{provider_id}-secret",
                "default_model": "safe-model",
                "enabled": True,
                "sort_order": 100,
                "status_url": "https://example.com/status",
                "quota_url": "https://example.com/quota",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        fake_resp = self.AssistantHttpResponse(status_code=200, text=marker)
        client_patch, instances = self.patch_provider_status_async_client(fake_resp)
        with client_patch:
            response = self.client.get("/v1/admin/provider-status")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(marker, response.text)
        # Verify mock was actually called for status_url and quota_url
        all_gets = [g for inst in instances for g in inst.gets]
        requested_urls = {g["url"] for g in all_gets}
        self.assertIn("https://example.com/status", requested_urls, "status_url must be fetched")
        self.assertIn("https://example.com/quota", requested_urls, "quota_url must be fetched")
        # Verify safe envelope in response
        custom_rows = {item["id"]: item for item in response.json().get("custom", [])}
        self.assertIn(provider_id, custom_rows)
        row = custom_rows[provider_id]
        for key in ("status", "quota"):
            sub = row.get(key)
            self.assertIsNotNone(sub, f"{key} sub-object must exist")
            self.assertNotIn("body", sub, f"{key} must not contain 'body'")
            self.assertNotIn("status_code", sub, f"{key} must not contain 'status_code'")
            self.assertIn("http_status", sub, f"{key} must contain 'http_status'")
            self.assertIn("ok", sub, f"{key} must contain 'ok'")
            self.assertIn("error", sub, f"{key} must contain 'error'")
            self.assertEqual(sub["http_status"], 200)
            self.assertTrue(sub["ok"])
            self.assertIsNone(sub["error"])

    def test_provider_status_exception_returns_safe_error(self) -> None:
        """status/quota 连接异常时只返回固定安全短语。"""
        marker = "RAW_STATUS_EXCEPTION_SHOULD_NOT_LEAK"

        class ExplodingClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def get(self, url, headers=None):
                raise ConnectionError(marker)

        self.create_custom_provider(
            self.unique_provider_id("safe-exc"),
            default_model="safe-model",
        )
        with patch("angemedia_gateway.services.admin_service.httpx.AsyncClient", new=ExplodingClient):
            response = self.client.get("/v1/admin/provider-status")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(marker, response.text)
        for item in response.json().get("custom", []):
            for key in ("status", "quota"):
                sub = item.get(key)
                if sub is not None:
                    self.assertFalse(sub["ok"])
                    self.assertIsNone(sub["http_status"])
                    self.assertEqual(sub["error"], "连接失败")

    def test_provider_test_http_failure_no_raw_body(self) -> None:
        """fetch_openai_model_ids 内部不会将上游 raw body 拼入错误消息。"""
        marker = "UPSTREAM_MODELS_BODY_SHOULD_NOT_LEAK"
        fake_resp = self.AssistantHttpResponse(status_code=500, text=marker)
        client_patch, _ = self.patch_provider_status_async_client(fake_resp)
        with client_patch:
            with self.assertRaises(ProviderModelFetchError) as ctx:
                import asyncio
                from angemedia_gateway.services.admin_service import fetch_openai_model_ids
                asyncio.run(fetch_openai_model_ids("https://example.com/v1", "sk-test"))
        error_text = str(ctx.exception)
        self.assertNotIn(marker, error_text, "ProviderModelFetchError must not contain upstream body")
        self.assertIn("HTTP 500", error_text)

    def test_provider_test_exception_no_raw_exc(self) -> None:
        """Provider test 未知异常时 response 不含原始异常消息。"""
        marker = "RAW_PROVIDER_EXCEPTION_SHOULD_NOT_LEAK"
        provider_id = self.unique_provider_id("safe-exc")
        self.create_custom_provider(provider_id, default_model="target-model")
        with patch(
            "angemedia_gateway.services.provider_admin_service.fetch_openai_compatible_model_ids",
            new=AsyncMock(side_effect=RuntimeError(marker)),
        ):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertNotIn(marker, response.text)
        self.assertEqual(body["status"], "upstream_unavailable")
        self.assertEqual(body["message"], "Provider upstream is unavailable.")

    def test_assistant_models_http_failure_no_raw_body(self) -> None:
        """fetch_assistant_model_ids 内部不会将上游 raw body 拼入错误消息。"""
        marker = "ASSISTANT_MODELS_BODY_SHOULD_NOT_LEAK"
        fake_resp = self.AssistantHttpResponse(status_code=500, text=marker)
        client_patch, _ = self.patch_provider_status_async_client(fake_resp)
        with client_patch:
            with self.assertRaises(AssistantModelFetchError) as ctx:
                import asyncio
                from angemedia_gateway.services.admin_service import fetch_assistant_model_ids
                asyncio.run(fetch_assistant_model_ids("https://llm.example.com/v1", "sk-test"))
        error_text = str(ctx.exception)
        self.assertNotIn(marker, error_text, "AssistantModelFetchError must not contain upstream body")
        self.assertIn("HTTP 500", error_text)

    def test_assistant_test_http_failure_no_raw_body(self) -> None:
        """Assistant test HTTP 失败时 502 detail 不含上游 raw body。"""
        marker = "ASSISTANT_TEST_BODY_SHOULD_NOT_LEAK"
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        fake_resp = self.AssistantHttpResponse(status_code=500, payload={}, text=marker)
        client_patch, _ = self.patch_assistant_async_client(fake_resp)
        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn(marker, response.text)
        self.assertIn("HTTP 500", response.text)

    def test_assistant_test_exception_no_raw_exc(self) -> None:
        """Assistant test 未知异常时 502 detail 不含原始异常消息。"""
        marker = "ASSISTANT_EXCEPTION_SHOULD_NOT_LEAK"
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        client_patch, _ = self.patch_assistant_async_client(post_error=RuntimeError(marker))
        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn(marker, response.text)
        self.assertEqual(response.json()["detail"], "LLM 测试失败")

    def test_assistant_test_success_preview_redacted(self) -> None:
        """Assistant test 成功时 preview 先脱敏再截断，secret 不泄露。"""
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        # Case 1: secret well within 200 chars
        secret_marker = "sk-test-preview-secret-value"
        llm_content = f"连接正常。测试密钥: {secret_marker}。其余内容足够长以验证截断。"
        fake_resp = self.AssistantHttpResponse(
            status_code=200,
            payload={"choices": [{"message": {"content": llm_content}}]},
        )
        client_patch, _ = self.patch_assistant_async_client(fake_resp)
        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("preview", body)
        self.assertLessEqual(len(body["preview"]), 200)
        self.assertNotIn(secret_marker, body["preview"], "preview must not contain raw secret")

    def test_assistant_test_preview_redact_before_truncate(self) -> None:
        """Secret 跨越 200 字符边界时，先脱敏再截断不泄露 secret 片段。"""
        self.save_llm_config(base_url="https://llm.example.com/v1", model="gpt-test-model")
        # Build content where secret starts at char 195, crossing the 200 boundary
        prefix = "x" * 195
        secret_marker = "sk-cross-boundary-secret-value-12345"
        llm_content = prefix + secret_marker + " trailing text after secret"
        fake_resp = self.AssistantHttpResponse(
            status_code=200,
            payload={"choices": [{"message": {"content": llm_content}}]},
        )
        client_patch, _ = self.patch_assistant_async_client(fake_resp)
        with client_patch:
            response = self.client.post("/v1/admin/assistant/test", json={})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        preview = body["preview"]
        self.assertLessEqual(len(preview), 200, "preview must not exceed 200 chars")
        self.assertNotIn(secret_marker, preview, "preview must not contain full secret")
        self.assertNotIn("sk-cross-boundary", preview, "preview must not contain secret prefix fragment")
        self.assertNotIn("sk-", preview, "preview must not contain secret key prefix")

    def test_provider_status_no_secret_in_response(self) -> None:
        """provider-status 响应不包含 api_key / key_hash / secret / token。"""
        provider_id = self.unique_provider_id("safe-leak")
        self.create_custom_provider(provider_id)
        secret = f"sk-{provider_id}-secret"
        response = self.client.get("/v1/admin/provider-status")
        self.assertEqual(response.status_code, 200, response.text)
        for forbidden in ("api_key", "key_hash", secret):
            self.assertNotIn(forbidden, response.text, f"response must not contain '{forbidden}'")


class DefaultAdminPasswordPolicyTest(unittest.TestCase):
    def test_admin_default_password_env_remains_compatible(self) -> None:
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-default-password-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": "compatible-admin-secret"}):
                    init_db()
                    ensure_default_admin_user()
                    self.assertTrue(verify_admin_login("admin", "compatible-admin-secret"))
                    self.assertFalse(verify_admin_login("admin", "admin123456"))
            finally:
                C.DB_FILE = original_db

    def test_admin_default_password_unset_raises_runtime_error(self) -> None:
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-random-password-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin"}, clear=False):
                    os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
                    init_db()
                    with self.assertRaises(RuntimeError) as ctx:
                        ensure_default_admin_user()
                    self.assertIn("ADMIN_DEFAULT_PASSWORD", str(ctx.exception))
            finally:
                C.DB_FILE = original_db

    def test_admin_default_password_unset_creates_no_admin_user(self) -> None:
        """缺失 ADMIN_DEFAULT_PASSWORD 时不得创建 admin 用户。"""
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-no-user-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin"}, clear=False):
                    os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
                    init_db()
                    with self.assertRaises(RuntimeError):
                        ensure_default_admin_user()
                    self.assertFalse(verify_admin_login("admin", "admin123456"))
                    self.assertFalse(verify_admin_login("admin", ""))
                    self.assertFalse(verify_admin_login("admin", "any-password"))
            finally:
                C.DB_FILE = original_db

    def test_admin_default_password_unset_no_password_in_logs(self) -> None:
        """缺失 ADMIN_DEFAULT_PASSWORD 时不得调用 log.warning 输出密码。"""
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-no-log-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin"}, clear=False):
                    os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
                    init_db()
                    with patch("angemedia_gateway.repositories.admin_auth.log.warning") as mock_warn, \
                         patch("angemedia_gateway.repositories.admin_auth.log.info") as mock_info, \
                         patch("angemedia_gateway.repositories.admin_auth.log.debug") as mock_debug, \
                         patch("angemedia_gateway.repositories.admin_auth.log.error") as mock_error:
                        with self.assertRaises(RuntimeError):
                            ensure_default_admin_user()
                        mock_warn.assert_not_called()
                        mock_info.assert_not_called()
                        mock_debug.assert_not_called()
                        mock_error.assert_not_called()
            finally:
                C.DB_FILE = original_db

    def test_admin_default_password_empty_raises_runtime_error(self) -> None:
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-empty-password-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": ""}, clear=False):
                    init_db()
                    with self.assertRaises(RuntimeError) as ctx:
                        ensure_default_admin_user()
                    self.assertIn("ADMIN_DEFAULT_PASSWORD", str(ctx.exception))
            finally:
                C.DB_FILE = original_db

    def test_admin_default_password_empty_creates_no_admin_user(self) -> None:
        """空 ADMIN_DEFAULT_PASSWORD 时不得创建 admin 用户。"""
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-empty-no-user-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": ""}, clear=False):
                    init_db()
                    with self.assertRaises(RuntimeError):
                        ensure_default_admin_user()
                    self.assertFalse(verify_admin_login("admin", "admin123456"))
                    self.assertFalse(verify_admin_login("admin", ""))
                    self.assertFalse(verify_admin_login("admin", "any-password"))
            finally:
                C.DB_FILE = original_db

    def test_admin_default_password_set_creates_user_with_correct_password(self) -> None:
        """ADMIN_DEFAULT_PASSWORD 设置时正常创建 admin，可用该密码登录。"""
        original_db = C.DB_FILE
        with tempfile.TemporaryDirectory(prefix="admin-set-pass-test-") as tmp_dir:
            C.DB_FILE = Path(tmp_dir) / "test.db"
            try:
                with patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": "my-secure-init-pass"}):
                    init_db()
                    ensure_default_admin_user()
                    self.assertTrue(verify_admin_login("admin", "my-secure-init-pass"))
                    self.assertFalse(verify_admin_login("admin", "wrong-password"))
                    self.assertFalse(verify_admin_login("admin", "admin123456"))
            finally:
                C.DB_FILE = original_db


if __name__ == "__main__":
    unittest.main()

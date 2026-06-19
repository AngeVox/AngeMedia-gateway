from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("IMAGE_PROXY_STATE_DIR", tempfile.mkdtemp(prefix="angemedia-provider-runtime-test-"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider  # noqa: E402
from angemedia_gateway.db.connection import db_connect  # noqa: E402
from angemedia_gateway.db.schema import init_db  # noqa: E402
from angemedia_gateway.providers.base import RouteTarget  # noqa: E402
from angemedia_gateway.providers.image import ByteDanceImageProvider  # noqa: E402
from angemedia_gateway.providers.runtime_config import resolve_provider_runtime_config  # noqa: E402
from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user  # noqa: E402
from angemedia_gateway.repositories.gateway_keys import create_gateway_api_key, revoke_gateway_api_key  # noqa: E402
from angemedia_gateway.request_hash_builders import (  # noqa: E402
    build_image_request_hash_payload,
    build_video_request_hash_payload,
)
from angemedia_gateway.routing import resolve_chain  # noqa: E402
from angemedia_gateway.schemas import ImageRequest, VideoRequest  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.services.image_generation import NoImageProviderAvailable, create_image  # noqa: E402
from angemedia_gateway.services.video_generation import VideoProviderDisabled, create_video  # noqa: E402


class CapturingAsyncClient:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.response

    async def get(self, url: str, **kwargs):
        self.get_calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class BuiltinProviderRuntimeConfigTest(unittest.TestCase):
    provider_id = "bytedance"
    runtime_provider_ids = ("bytedance", "siliconflow", "modelscope", "openai_image", "agnes_video")

    def setUp(self) -> None:
        init_db()
        ensure_default_admin_user()
        self._original_key = C.BYTEDANCE_API_KEY
        self._original_base_url = C.BYTEDANCE_BASE_URL
        C.BYTEDANCE_API_KEY = ""
        C.BYTEDANCE_BASE_URL = "https://ark.example.test/api/v3"
        self._delete_runtime_row()
        self.client = TestClient(app)
        login = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def tearDown(self) -> None:
        self._delete_runtime_row()
        C.BYTEDANCE_API_KEY = self._original_key
        C.BYTEDANCE_BASE_URL = self._original_base_url

    def _delete_runtime_row(self) -> None:
        with db_connect() as conn:
            conn.executemany(
                "DELETE FROM provider_runtime_configs WHERE provider_id = ?",
                [(provider_id,) for provider_id in self.runtime_provider_ids],
            )

    def test_admin_list_is_safe_and_gateway_key_is_rejected(self) -> None:
        response = self.client.get("/v1/admin/provider-configs")
        self.assertEqual(response.status_code, 200, response.text)
        rows = {item["provider_id"]: item for item in response.json()["data"]}
        self.assertIn(self.provider_id, rows)
        self.assertEqual(rows[self.provider_id]["source"], "builtin")
        self.assertEqual(rows[self.provider_id]["media_types"], ["image"])
        self.assertNotIn("api_key", rows[self.provider_id])
        self.assertNotIn("Authorization", response.text)

        anonymous = TestClient(app)
        self.assertEqual(anonymous.get("/v1/admin/provider-configs").status_code, 401)

        gateway_key = create_gateway_api_key(name="provider-runtime-admin-boundary")
        try:
            api_client = TestClient(app)
            headers = {"Authorization": f"Bearer {gateway_key['key']}"}
            blocked_get = api_client.get("/v1/admin/provider-configs", headers=headers)
            blocked_write = api_client.post(
                f"/v1/admin/provider-configs/{self.provider_id}",
                json={"enabled": False},
                headers=headers,
            )
            self.assertEqual(blocked_get.status_code, 403, blocked_get.text)
            self.assertEqual(blocked_write.status_code, 403, blocked_write.text)
        finally:
            revoke_gateway_api_key(gateway_key["id"])

    def test_connection_test_requires_session_and_rejects_unknown_provider(self) -> None:
        anonymous = TestClient(app).post(f"/v1/admin/provider-configs/{self.provider_id}/test")
        self.assertEqual(anonymous.status_code, 401, anonymous.text)

        gateway_key = create_gateway_api_key(name="provider-connection-test-boundary")
        try:
            gateway_response = TestClient(app).post(
                f"/v1/admin/provider-configs/{self.provider_id}/test",
                headers={"Authorization": f"Bearer {gateway_key['key']}"},
            )
            self.assertEqual(gateway_response.status_code, 403, gateway_response.text)
            self.assertNotIn(gateway_key["key"], gateway_response.text)
        finally:
            revoke_gateway_api_key(gateway_key["id"])

        unknown = self.client.post("/v1/admin/provider-configs/does-not-exist/test")
        self.assertEqual(unknown.status_code, 404, unknown.text)

    def test_connection_test_short_circuits_disabled_no_key_unsupported_and_invalid_url(self) -> None:
        with patch("httpx.AsyncClient") as async_client:
            with patch.object(C, "OPENAI_IMAGE_API_KEY", "ENV_OPENAI_KEY_DO_NOT_USE"), patch.object(
                C, "OPENAI_IMAGE_BASE_URL", "https://example.com/default-v1"
            ):
                disabled_update = self.client.post(
                    "/v1/admin/provider-configs/openai_image",
                    json={"enabled": False},
                )
                self.assertEqual(disabled_update.status_code, 200, disabled_update.text)
                disabled = self.client.post("/v1/admin/provider-configs/openai_image/test")
                self.assertEqual(disabled.status_code, 200, disabled.text)
                self.assertEqual(disabled.json()["data"]["status"], "disabled")

            enabled_update = self.client.post(
                "/v1/admin/provider-configs/openai_image",
                json={"enabled": True},
            )
            self.assertEqual(enabled_update.status_code, 200, enabled_update.text)
            with patch.object(C, "OPENAI_IMAGE_API_KEY", ""), patch.object(
                C, "OPENAI_IMAGE_BASE_URL", "https://example.com/default-v1"
            ):
                not_configured = self.client.post("/v1/admin/provider-configs/openai_image/test")
                self.assertEqual(not_configured.status_code, 200, not_configured.text)
                self.assertEqual(not_configured.json()["data"]["status"], "not_configured")

            unsupported_update = self.client.post(
                f"/v1/admin/provider-configs/{self.provider_id}",
                json={"enabled": True, "api_key": "UNSUPPORTED_KEY_DO_NOT_USE"},
            )
            self.assertEqual(unsupported_update.status_code, 200, unsupported_update.text)
            unsupported = self.client.post(f"/v1/admin/provider-configs/{self.provider_id}/test")
            self.assertEqual(unsupported.status_code, 200, unsupported.text)
            self.assertEqual(unsupported.json()["data"]["status"], "unsupported")

            with patch.object(C, "OPENAI_IMAGE_API_KEY", "ENV_OPENAI_KEY_DO_NOT_USE"), patch.object(
                C, "OPENAI_IMAGE_BASE_URL", "not-a-url"
            ):
                invalid_url = self.client.post("/v1/admin/provider-configs/openai_image/test")
                self.assertEqual(invalid_url.status_code, 200, invalid_url.text)
                self.assertEqual(invalid_url.json()["data"]["status"], "failed")
                self.assertEqual(invalid_url.json()["data"]["message"], "Provider base URL is invalid.")

            async_client.assert_not_called()

    def test_connection_test_uses_runtime_key_and_base_url_without_exposing_them(self) -> None:
        env_key = "ENV_OPENAI_KEY_DO_NOT_USE"
        runtime_key = "RUNTIME_OPENAI_KEY_DO_NOT_USE"
        runtime_base = "https://example.com/runtime-openai-v1"
        with patch.object(C, "OPENAI_IMAGE_API_KEY", env_key), patch.object(
            C, "OPENAI_IMAGE_BASE_URL", "https://example.com/default-openai-v1"
        ):
            updated = self.client.post(
                "/v1/admin/provider-configs/openai_image",
                json={"enabled": True, "api_key": runtime_key, "base_url_override": runtime_base},
            )
            self.assertEqual(updated.status_code, 200, updated.text)

            fake_client = CapturingAsyncClient(httpx.Response(200, json={"data": [{"id": "safe-model"}]}))
            with patch("httpx.AsyncClient", return_value=fake_client):
                response = self.client.post("/v1/admin/provider-configs/openai_image/test")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["provider_id"], "openai_image")
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["http_status"], 200)
        self.assertEqual(data["details"]["endpoint_kind"], "models")
        self.assertEqual(data["details"]["api_key_source"], "runtime")
        self.assertEqual(data["details"]["base_url_source"], "runtime")
        self.assertEqual(fake_client.get_calls[0][0], f"{runtime_base}/models")
        self.assertEqual(
            fake_client.get_calls[0][1]["headers"]["Authorization"],
            f"Bearer {runtime_key}",
        )
        rendered = response.text
        for forbidden in (runtime_key, env_key, runtime_base, "Authorization"):
            self.assertNotIn(forbidden, rendered)

    def test_connection_test_failures_are_safe_and_do_not_return_provider_body(self) -> None:
        runtime_key = "RUNTIME_FAILURE_KEY_DO_NOT_USE"
        runtime_base = "https://example.com/runtime-failure-v1"
        updated = self.client.post(
            "/v1/admin/provider-configs/openai_image",
            json={"enabled": True, "api_key": runtime_key, "base_url_override": runtime_base},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        cases = [
            (httpx.Response(401, text="AUTH RAW BODY Authorization Bearer LEAK_DO_NOT_USE"), 401),
            (httpx.Response(403, text="FORBIDDEN RAW BODY RUNTIME_FAILURE_KEY_DO_NOT_USE"), 403),
            (httpx.ReadTimeout("TIMEOUT RAW BODY RUNTIME_FAILURE_KEY_DO_NOT_USE"), None),
            (httpx.ConnectError("NETWORK RAW BODY RUNTIME_FAILURE_KEY_DO_NOT_USE"), None),
        ]
        for upstream, expected_http_status in cases:
            with self.subTest(upstream=type(upstream).__name__, http_status=expected_http_status):
                fake_client = CapturingAsyncClient(upstream)
                with patch("httpx.AsyncClient", return_value=fake_client):
                    response = self.client.post("/v1/admin/provider-configs/openai_image/test")
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()["data"]
                self.assertEqual(data["status"], "failed")
                self.assertEqual(data["http_status"], expected_http_status)
                self.assertEqual(len(fake_client.get_calls), 1)
                for forbidden in (
                    runtime_key,
                    runtime_base,
                    "Authorization",
                    "RAW BODY",
                    "LEAK_DO_NOT_USE",
                ):
                    self.assertNotIn(forbidden, response.text)

    def test_runtime_precedence_clear_disable_and_base_url_resolution(self) -> None:
        env_secret = "sk-env-fallback-secret-1234"
        runtime_secret = "sk-runtime-provider-secret-9876"
        C.BYTEDANCE_API_KEY = env_secret

        env_row = {item["provider_id"]: item for item in self.client.get("/v1/admin/provider-configs").json()["data"]}[self.provider_id]
        self.assertTrue(env_row["api_key_configured"])
        self.assertNotIn(env_secret, json.dumps(env_row))

        updated = self.client.post(
            f"/v1/admin/provider-configs/{self.provider_id}",
            json={
                "enabled": False,
                "api_key": runtime_secret,
                "base_url_override": "https://example.com/v1",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertNotIn(runtime_secret, updated.text)
        data = updated.json()["data"]
        self.assertFalse(data["enabled"])
        self.assertTrue(data["api_key_configured"])
        self.assertEqual(data["base_url_override"], "https://example.com/v1")

        resolved = resolve_provider_runtime_config(self.provider_id)
        self.assertEqual(resolved.api_key, runtime_secret)
        self.assertEqual(resolved.base_url, "https://example.com/v1")
        self.assertEqual(resolve_chain("seedream"), [])

        request_hash_payload = build_image_request_hash_payload(
            ImageRequest(prompt="safe request hash", model="seedream", size="1024x1024"),
            provider_mode="builtin",
            resolved_chain=[RouteTarget(self.provider_id, "seedream-3-0-t2i-250415")],
        )
        serialized_hash = json.dumps(request_hash_payload.payload)
        self.assertNotIn(runtime_secret, serialized_hash)
        self.assertNotIn("https://example.com/v1", serialized_hash)
        self.assertNotIn("api_key", serialized_hash)
        self.assertNotIn("base_url", serialized_hash)

        C.BYTEDANCE_API_KEY = ""
        cleared = self.client.post(f"/v1/admin/provider-configs/{self.provider_id}/clear-key")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertFalse(cleared.json()["data"]["api_key_configured"])
        self.assertNotIn(runtime_secret, cleared.text)

    def test_image_adapter_uses_runtime_key_and_base_then_clear_falls_back_to_env(self) -> None:
        env_key = "ENV_KEY_DO_NOT_USE"
        runtime_key = "RUNTIME_KEY_DO_NOT_USE"
        runtime_base = "https://example.com/runtime-v1"
        C.BYTEDANCE_API_KEY = env_key

        updated = self.client.post(
            f"/v1/admin/provider-configs/{self.provider_id}",
            json={"enabled": True, "api_key": runtime_key, "base_url_override": runtime_base},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertNotIn(runtime_key, updated.text)

        first_client = CapturingAsyncClient(
            httpx.Response(200, json={"data": [{"url": "https://example.test/runtime.png"}]})
        )
        with patch("httpx.AsyncClient", return_value=first_client):
            asyncio.run(
                ByteDanceImageProvider().generate(
                    ImageRequest(prompt="runtime e2e", model="seedream", size="1024x1024"),
                    RouteTarget(self.provider_id, "seedream-3-0-t2i-250415"),
                )
            )

        self.assertEqual(first_client.post_calls[0][0], f"{runtime_base}/images/generations")
        self.assertEqual(
            first_client.post_calls[0][1]["headers"]["Authorization"],
            f"Bearer {runtime_key}",
        )

        cleared = self.client.post(f"/v1/admin/provider-configs/{self.provider_id}/clear-key")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertTrue(cleared.json()["data"]["api_key_configured"])
        self.assertNotIn(runtime_key, cleared.text)
        self.assertNotIn(env_key, cleared.text)

        second_client = CapturingAsyncClient(
            httpx.Response(200, json={"data": [{"url": "https://example.test/env.png"}]})
        )
        with patch("httpx.AsyncClient", return_value=second_client):
            asyncio.run(
                ByteDanceImageProvider().generate(
                    ImageRequest(prompt="env fallback", model="seedream", size="1024x1024"),
                    RouteTarget(self.provider_id, "seedream-3-0-t2i-250415"),
                )
            )

        self.assertEqual(second_client.post_calls[0][0], f"{runtime_base}/images/generations")
        self.assertEqual(
            second_client.post_calls[0][1]["headers"]["Authorization"],
            f"Bearer {env_key}",
        )

    def test_disabled_image_provider_never_reaches_generation_adapter(self) -> None:
        class NeverCalledImageProvider:
            called = False

            async def generate(self, req, target):
                self.called = True
                raise AssertionError("disabled image provider was called")

        explicit_provider = NeverCalledImageProvider()
        disabled = self.client.post(
            f"/v1/admin/provider-configs/{self.provider_id}",
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        with self.assertRaisesRegex(NoImageProviderAvailable, "所选模型已停用"):
            asyncio.run(
                create_image(
                    ImageRequest(prompt="disabled explicit", model="seedream", size="1024x1024"),
                    providers={self.provider_id: explicit_provider},
                )
            )
        self.assertFalse(explicit_provider.called)

        for provider_id in ("siliconflow", "modelscope"):
            response = self.client.post(
                f"/v1/admin/provider-configs/{provider_id}",
                json={"enabled": False},
            )
            self.assertEqual(response.status_code, 200, response.text)
        default_provider = NeverCalledImageProvider()
        with self.assertRaisesRegex(NoImageProviderAvailable, "默认链路全部停用"):
            asyncio.run(
                create_image(
                    ImageRequest(prompt="disabled default chain", size="1024x1024"),
                    providers={"siliconflow": default_provider, "modelscope": default_provider},
                )
            )
        self.assertFalse(default_provider.called)

    def test_agnes_video_runtime_credentials_and_disabled_generation_gate(self) -> None:
        runtime_key = "RUNTIME_VIDEO_KEY_DO_NOT_USE"
        runtime_base = "https://example.com/agnes-runtime-v1"
        updated = self.client.post(
            "/v1/admin/provider-configs/agnes_video",
            json={"enabled": True, "api_key": runtime_key, "base_url_override": runtime_base},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertNotIn(runtime_key, updated.text)

        provider = AgnesVideoProvider(
            api_key="ENV_VIDEO_KEY_DO_NOT_USE",
            base_url="https://env-video.example.test/v1",
            runtime_config_resolver=resolve_provider_runtime_config,
        )
        provider._request_json = AsyncMock(return_value={"task_id": "runtime-video-task", "status": "queued"})
        request = VideoRequest(prompt="runtime video e2e")
        result = asyncio.run(provider.submit_task(request))
        self.assertEqual(result["task_id"], "runtime-video-task")
        call = provider._request_json.await_args
        self.assertEqual(call.args[:2], ("POST", f"{runtime_base}/videos"))
        self.assertEqual(call.kwargs["headers"]["Authorization"], f"Bearer {runtime_key}")

        video_hash = build_video_request_hash_payload(request, provider="agnes_video")
        serialized_hash = json.dumps(video_hash.payload)
        self.assertNotIn(runtime_key, serialized_hash)
        self.assertNotIn(runtime_base, serialized_hash)
        self.assertNotIn("api_key", serialized_hash)
        self.assertNotIn("base_url", serialized_hash)

        disabled = self.client.post(
            "/v1/admin/provider-configs/agnes_video",
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)

        class NeverCalledVideoProvider:
            called = False

            async def submit_task(self, req):
                self.called = True
                raise AssertionError("disabled video provider was called")

        blocked_provider = NeverCalledVideoProvider()
        with self.assertRaisesRegex(VideoProviderDisabled, "视频渠道已停用"):
            asyncio.run(create_video(request, agnes_video_provider=blocked_provider))
        self.assertFalse(blocked_provider.called)

    def test_unknown_and_non_builtin_providers_are_not_editable(self) -> None:
        unknown = self.client.post("/v1/admin/provider-configs/does-not-exist", json={"enabled": False})
        self.assertEqual(unknown.status_code, 404, unknown.text)

        catalog_only = self.client.post("/v1/admin/provider-configs/mock", json={"enabled": False})
        self.assertEqual(catalog_only.status_code, 409, catalog_only.text)


class ProviderRuntimeConfigSourceContractTest(unittest.TestCase):
    def test_all_builtin_adapters_consume_the_shared_resolver(self) -> None:
        adapter_paths = [
            "scripts/angemedia_gateway/providers/image/siliconflow.py",
            "scripts/angemedia_gateway/providers/image/modelscope.py",
            "scripts/angemedia_gateway/providers/image/pollinations.py",
            "scripts/angemedia_gateway/providers/image/openai_compatible.py",
            "scripts/angemedia_gateway/providers/image/agnes.py",
            "scripts/angemedia_gateway/providers/image/bytedance.py",
        ]
        for relative_path in adapter_paths:
            with self.subTest(path=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("resolve_provider_runtime_config", source)
                self.assertIn("runtime.base_url", source)
                self.assertIn("runtime.api_key", source)
                for credential_name in (
                    "SILICONFLOW_API_KEY",
                    "MODELSCOPE_API_KEY",
                    "POLLINATIONS_API_KEY",
                    "OPENAI_IMAGE_API_KEY",
                    "AGNES_API_KEY",
                    "BYTEDANCE_API_KEY",
                    "OPENAI_IMAGE_BASE_URL",
                    "AGNES_BASE_URL",
                    "BYTEDANCE_BASE_URL",
                ):
                    self.assertNotIn(f"C.{credential_name}", source)
                    self.assertNotIn(f"config.{credential_name}", source)
        video_source = (ROOT / "scripts/angemedia_gateway/adapters/agnes_video.py").read_text(encoding="utf-8")
        self.assertIn("runtime_config_resolver", video_source)
        self.assertNotIn("AGNES_API_KEY", video_source)
        self.assertNotIn("AGNES_BASE_URL", video_source)

    def test_providers_ui_keeps_custom_and_readonly_sections_with_write_only_key_input(self) -> None:
        providers_root = ROOT / "app/www/assets/studio/features/providers"
        page_source = (providers_root / "page.js").read_text(encoding="utf-8")
        builtin_source = (providers_root / "builtin-config.js").read_text(encoding="utf-8")
        api_source = (providers_root / "provider-api.js").read_text(encoding="utf-8")
        css_source = (ROOT / "app/www/assets/studio/styles/pages.css").read_text(encoding="utf-8")
        i18n_source = (ROOT / "app/www/assets/studio/i18n.js").read_text(encoding="utf-8")

        self.assertIn("renderBuiltinConfigPanel", page_source)
        self.assertIn("createProviderForm", page_source)
        self.assertIn("providerCard", page_source)
        self.assertIn("providers.catalogProviders", page_source)
        self.assertIn("providers.reservedProviders", page_source)
        self.assertIn("/admin/provider-configs", api_source)
        self.assertIn("type: 'password'", builtin_source)
        self.assertIn("value: ''", builtin_source)
        self.assertNotIn("value: provider.api_key_preview", builtin_source)
        self.assertIn("provider.api_key_configured", builtin_source)
        self.assertIn("provider.api_key_preview", builtin_source)
        self.assertIn("value: provider.base_url_override || ''", builtin_source)
        self.assertIn("/clear-key", builtin_source)
        self.assertIn("/test", builtin_source)
        self.assertIn("testConnection.disabled = true", builtin_source)
        self.assertIn("testConnection.textContent = t('providers.builtinTesting')", builtin_source)
        for status in ("success", "failed", "unsupported", "not_configured", "disabled"):
            self.assertIn(f"{status}:", builtin_source)
        self.assertIn("checked: provider.enabled === true", builtin_source)
        self.assertIn("@media (max-width: 400px)", css_source)
        self.assertIn(".builtin-config-actions .btn", css_source)
        self.assertIn(".builtin-config-actions .provider-test-button", css_source)
        self.assertIn("grid-column: 1 / -1", css_source)
        self.assertIn("providers.builtinTestConnection", i18n_source)

    def test_storage_auth_contract_is_unchanged(self) -> None:
        source = (ROOT / "scripts/angemedia_gateway/routes/storage.py").read_text(encoding="utf-8")
        self.assertIn('dependencies=[Depends(require_auth)]', source)
        self.assertIn('@router.api_route("/generated/{filename:path}", methods=["GET", "HEAD"]', source)
        self.assertIn('@router.api_route("/uploads/{filename:path}", methods=["GET", "HEAD"]', source)


if __name__ == "__main__":
    unittest.main()

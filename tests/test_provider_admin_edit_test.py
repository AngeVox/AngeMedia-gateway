from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("IMAGE_PROXY_STATE_DIR", tempfile.mkdtemp(prefix="angemedia-provider-admin-test-"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from fastapi.testclient import TestClient  # noqa: E402

from angemedia_gateway.repositories.settings import get_custom_provider  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import ensure_default_admin_user, init_db  # noqa: E402


FORBIDDEN_RESPONSE_KEYS = {
    "api_key",
    "_api_key",
    "authorization",
    "Authorization",
    "password",
    "token",
    "secret",
    "raw",
    "raw_body",
    "raw_response",
    "raw_error",
    "exception",
    "stack",
}


class RecordingAsyncClient:
    response: httpx.Response | None = None
    error: Exception | None = None
    instances: list["RecordingAsyncClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.gets: list[dict[str, Any]] = []
        self.posts: list[dict[str, Any]] = []
        RecordingAsyncClient.instances.append(self)

    async def __aenter__(self) -> "RecordingAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.gets.append({"url": url, **kwargs})
        if RecordingAsyncClient.error is not None:
            raise RecordingAsyncClient.error
        return RecordingAsyncClient.response or httpx.Response(200, json={"data": []})

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.posts.append({"url": url, **kwargs})
        raise AssertionError("Provider test must not call image generation endpoints")


def _reset_recording_client(response: httpx.Response | None = None, error: Exception | None = None) -> None:
    RecordingAsyncClient.response = response
    RecordingAsyncClient.error = error
    RecordingAsyncClient.instances = []


class ProviderAdminEditTestContract(unittest.TestCase):
    def setUp(self) -> None:
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        self.created_provider_ids: list[str] = []
        self.login_admin()

    def tearDown(self) -> None:
        for provider_id in self.created_provider_ids:
            self.client.delete(f"/v1/admin/providers/{provider_id}")

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def unique_provider_id(self, prefix: str = "edit-test") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def create_custom_provider(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        default_model: str = "target-model",
        notes: str = "initial notes",
    ) -> None:
        self.created_provider_ids.append(provider_id)
        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": f"Provider {provider_id}",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": api_key or f"sk-{provider_id}-old-secret",
                "default_model": default_model,
                "enabled": True,
                "notes": notes,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def gateway_key_client(self) -> tuple[TestClient, str]:
        response = self.client.post("/v1/admin/gateway-keys", json={"name": self.unique_provider_id("gw")})
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["data"]["key"]
        return TestClient(app), key

    def assert_no_sensitive_response(self, payload: Any, *markers: str) -> None:
        text = str(payload)
        for marker in markers:
            if marker:
                self.assertNotIn(marker, text)
        for forbidden_text in ("Authorization", "Bearer", "raw body"):
            self.assertNotIn(forbidden_text, text)

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, FORBIDDEN_RESPONSE_KEYS)
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)

    def test_provider_detail_requires_admin_session_and_denies_gateway_key(self) -> None:
        provider_id = self.unique_provider_id()
        self.create_custom_provider(provider_id)

        anonymous = TestClient(app).get(f"/v1/admin/providers/{provider_id}")
        self.assertEqual(anonymous.status_code, 401)

        api_client, key = self.gateway_key_client()
        api_key_response = api_client.get(
            f"/v1/admin/providers/{provider_id}",
            headers={"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(api_key_response.status_code, 403)
        self.assert_no_sensitive_response(api_key_response.json(), key)

    def test_custom_provider_detail_returns_editable_safe_detail(self) -> None:
        provider_id = self.unique_provider_id("detail")
        secret = f"sk-{provider_id}-secret"
        self.create_custom_provider(provider_id, api_key=secret, notes="editable note")

        response = self.client.get(f"/v1/admin/providers/{provider_id}")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["id"], provider_id)
        self.assertEqual(data["source"], "custom")
        self.assertTrue(data["editable"])
        self.assertEqual(data["base_url"], "https://example.com/v1")
        self.assertEqual(data["default_model"], "target-model")
        self.assertTrue(data["enabled"])
        self.assertEqual(data["notes"], "editable note")
        self.assertTrue(data["api_key_configured"])
        self.assert_no_sensitive_response(data, secret)

    def test_builtin_provider_detail_is_read_only_or_unknown_is_404(self) -> None:
        builtin = self.client.get("/v1/admin/providers/siliconflow")
        self.assertEqual(builtin.status_code, 200, builtin.text)
        data = builtin.json()["data"]
        self.assertEqual(data["id"], "siliconflow")
        self.assertEqual(data["source"], "builtin")
        self.assertFalse(data["editable"])
        self.assertTrue(data["read_only"])
        self.assert_no_sensitive_response(data)

        unknown = self.client.get(f"/v1/admin/providers/{self.unique_provider_id('missing')}")
        self.assertEqual(unknown.status_code, 404)

    def test_provider_edit_requires_admin_session_and_denies_gateway_key(self) -> None:
        provider_id = self.unique_provider_id()
        self.create_custom_provider(provider_id)
        payload = {"name": "Edited", "base_url": "https://example.com/v1", "default_model": "edited-model"}

        anonymous = TestClient(app).patch(f"/v1/admin/providers/{provider_id}", json=payload)
        self.assertEqual(anonymous.status_code, 401)

        api_client, key = self.gateway_key_client()
        api_key_response = api_client.patch(
            f"/v1/admin/providers/{provider_id}",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(api_key_response.status_code, 403)
        self.assert_no_sensitive_response(api_key_response.json(), key)

    def test_custom_provider_edit_updates_allowed_fields_and_returns_safe_summary(self) -> None:
        provider_id = self.unique_provider_id("edit")
        old_secret = f"sk-{provider_id}-old-secret"
        self.create_custom_provider(provider_id, api_key=old_secret)

        response = self.client.patch(
            f"/v1/admin/providers/{provider_id}",
            json={
                "name": "Edited Provider",
                "base_url": "https://api.example.com/v1",
                "default_model": "edited-model",
                "enabled": False,
                "notes": "edited note",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["id"], provider_id)
        self.assertEqual(data["name"], "Edited Provider")
        self.assertEqual(data["default_model"], "edited-model")
        self.assertFalse(data["enabled"])
        self.assertTrue(data["api_key_configured"])
        self.assertNotIn("base_url", data)
        self.assert_no_sensitive_response(data, old_secret)

        stored = get_custom_provider(provider_id, include_secret=True)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["base_url"], "https://api.example.com/v1")
        self.assertEqual(stored["api_key"], old_secret)
        self.assertEqual(stored["notes"], "edited note")

    def test_custom_provider_edit_empty_or_missing_api_key_keeps_existing_secret(self) -> None:
        provider_id = self.unique_provider_id("keep-key")
        old_secret = f"sk-{provider_id}-old-secret"
        self.create_custom_provider(provider_id, api_key=old_secret)

        for payload in (
            {"name": "No Key Field", "base_url": "https://example.com/v1", "default_model": "model-a"},
            {"name": "Empty Key", "base_url": "https://example.com/v1", "default_model": "model-b", "api_key": ""},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(f"/v1/admin/providers/{provider_id}", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assert_no_sensitive_response(response.json(), old_secret)
                stored = get_custom_provider(provider_id, include_secret=True)
                self.assertIsNotNone(stored)
                self.assertEqual(stored["api_key"], old_secret)

    def test_custom_provider_edit_non_empty_api_key_replaces_secret_without_echo(self) -> None:
        provider_id = self.unique_provider_id("replace-key")
        old_secret = f"sk-{provider_id}-old-secret"
        new_secret = f"sk-{provider_id}-new-secret"
        self.create_custom_provider(provider_id, api_key=old_secret)

        response = self.client.patch(
            f"/v1/admin/providers/{provider_id}",
            json={
                "name": "Replace Key",
                "base_url": "https://example.com/v1",
                "default_model": "target-model",
                "api_key": new_secret,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assert_no_sensitive_response(response.json(), old_secret, new_secret)
        stored = get_custom_provider(provider_id, include_secret=True)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["api_key"], new_secret)

    def test_provider_edit_denies_builtin_catalog_reserved_and_disallowed_fields(self) -> None:
        provider_id = self.unique_provider_id("disallowed")
        self.create_custom_provider(provider_id)

        for denied_id in ("siliconflow", "pollinations"):
            with self.subTest(denied_id=denied_id):
                response = self.client.patch(
                    f"/v1/admin/providers/{denied_id}",
                    json={"name": "Denied", "base_url": "https://example.com/v1", "default_model": "x"},
                )
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"]["code"], "provider_read_only")
                self.assert_no_sensitive_response(response.json())

        response = self.client.patch(
            f"/v1/admin/providers/{provider_id}",
            json={
                "name": "Bad Fields",
                "base_url": "https://example.com/v1",
                "default_model": "x",
                "status_url": "https://example.com/status",
                "quota_url": "https://example.com/quota",
                "sort_order": 1,
                "last_error": "raw body should not be accepted",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_provider_edit_fields")
        self.assert_no_sensitive_response(response.json())

    def test_provider_edit_invalid_base_url_returns_safe_error(self) -> None:
        provider_id = self.unique_provider_id("bad-url")
        self.create_custom_provider(provider_id)

        response = self.client.patch(
            f"/v1/admin/providers/{provider_id}",
            json={"base_url": "example.com/v1/images/generations", "default_model": "x"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_base_url")
        self.assert_no_sensitive_response(response.json())


class ProviderAdminTestContract(unittest.TestCase):
    def setUp(self) -> None:
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        self.created_provider_ids: list[str] = []
        self.login_admin()

    def tearDown(self) -> None:
        for provider_id in self.created_provider_ids:
            self.client.delete(f"/v1/admin/providers/{provider_id}")

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def unique_provider_id(self, prefix: str = "provider-test") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def create_custom_provider(self, provider_id: str, *, api_key: str | None = None) -> str:
        secret = api_key or f"sk-{provider_id}-secret"
        self.created_provider_ids.append(provider_id)
        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": f"Provider {provider_id}",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": secret,
                "default_model": "target-model",
                "enabled": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return secret

    def gateway_key_client(self) -> tuple[TestClient, str]:
        response = self.client.post("/v1/admin/gateway-keys", json={"name": self.unique_provider_id("gw")})
        self.assertEqual(response.status_code, 200, response.text)
        key = response.json()["data"]["key"]
        return TestClient(app), key

    def assert_no_sensitive_response(self, payload: Any, *markers: str) -> None:
        ProviderAdminEditTestContract.assert_no_sensitive_response(self, payload, *markers)

    def test_provider_test_requires_admin_session_and_denies_gateway_key(self) -> None:
        provider_id = self.unique_provider_id()
        self.create_custom_provider(provider_id)

        anonymous = TestClient(app).post(f"/v1/admin/providers/{provider_id}/test")
        self.assertEqual(anonymous.status_code, 401)

        api_client, key = self.gateway_key_client()
        api_key_response = api_client.post(
            f"/v1/admin/providers/{provider_id}/test",
            headers={"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(api_key_response.status_code, 403)
        self.assert_no_sensitive_response(api_key_response.json(), key)

    def test_provider_test_only_supports_custom_openai_image(self) -> None:
        for provider_id in ("siliconflow", "pollinations", "agnes_video"):
            with self.subTest(provider_id=provider_id):
                response = self.client.post(f"/v1/admin/providers/{provider_id}/test")
                self.assertEqual(response.status_code, 409)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "test_not_supported")
                self.assertEqual(detail["status"], "test_not_supported")
                self.assert_no_sensitive_response(detail)

    def test_custom_provider_test_uses_models_endpoint_provider_http_helper_and_no_generation(self) -> None:
        provider_id = self.unique_provider_id("models")
        secret = self.create_custom_provider(provider_id)
        _reset_recording_client(httpx.Response(200, json={"data": [{"id": "target-model"}, {"id": "other-model"}]}))

        with patch("httpx.AsyncClient", new=RecordingAsyncClient):
            response = self.client.post(f"/v1/admin/providers/{provider_id}/test")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["provider_id"], provider_id)
        self.assertEqual(body["provider_type"], "openai_image")
        self.assertEqual(body["model"], "target-model")
        self.assertTrue(body["model_found"])
        self.assertIsInstance(body["elapsed_ms"], int)
        self.assertIn("message", body)
        self.assert_no_sensitive_response(body, secret)

        all_gets = [call for instance in RecordingAsyncClient.instances for call in instance.gets]
        all_posts = [call for instance in RecordingAsyncClient.instances for call in instance.posts]
        self.assertEqual([call["url"] for call in all_gets], ["https://example.com/v1/models"])
        self.assertEqual(all_posts, [], "provider test must not call /images/generations")
        for instance in RecordingAsyncClient.instances:
            self.assertIs(instance.kwargs.get("trust_env"), False)

    def test_custom_provider_test_failure_categories_are_safe(self) -> None:
        cases = [
            ("auth_failed", httpx.Response(401, text="AUTH BODY sk-leak Authorization: Bearer token")),
            ("rate_limited", httpx.Response(429, text="RATE BODY sk-leak token password")),
            ("upstream_unavailable", httpx.Response(500, text="UPSTREAM BODY sk-leak token password")),
            ("invalid_response", httpx.Response(200, text="not-json sk-leak token password")),
        ]
        for expected_status, response_obj in cases:
            with self.subTest(expected_status=expected_status):
                provider_id = self.unique_provider_id(expected_status.replace("_", "-"))
                secret = self.create_custom_provider(provider_id)
                _reset_recording_client(response_obj)
                with patch("httpx.AsyncClient", new=RecordingAsyncClient):
                    response = self.client.post(f"/v1/admin/providers/{provider_id}/test")
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertFalse(body["ok"])
                self.assertEqual(body["status"], expected_status)
                self.assertIn("message", body)
                self.assert_no_sensitive_response(body, secret, "sk-leak", "AUTH BODY", "RATE BODY", "UPSTREAM BODY", "not-json")

    def test_custom_provider_test_transport_errors_are_safe(self) -> None:
        cases = [
            ("timeout", httpx.ReadTimeout("timeout raw body sk-leak Authorization: Bearer token")),
            ("network_error", httpx.ConnectError("network raw body sk-leak token password")),
        ]
        for expected_status, error in cases:
            with self.subTest(expected_status=expected_status):
                provider_id = self.unique_provider_id(expected_status.replace("_", "-"))
                secret = self.create_custom_provider(provider_id)
                _reset_recording_client(error=error)
                with patch("httpx.AsyncClient", new=RecordingAsyncClient):
                    response = self.client.post(f"/v1/admin/providers/{provider_id}/test")
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertFalse(body["ok"])
                self.assertEqual(body["status"], expected_status)
                self.assertIn("message", body)
                self.assert_no_sensitive_response(body, secret, "sk-leak", "raw body")


if __name__ == "__main__":
    unittest.main()

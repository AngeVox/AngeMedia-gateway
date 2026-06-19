from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("IMAGE_PROXY_STATE_DIR", tempfile.mkdtemp(prefix="angemedia-provider-runtime-test-"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.db.connection import db_connect  # noqa: E402
from angemedia_gateway.db.schema import init_db  # noqa: E402
from angemedia_gateway.providers.base import RouteTarget  # noqa: E402
from angemedia_gateway.providers.runtime_config import resolve_provider_runtime_config  # noqa: E402
from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user  # noqa: E402
from angemedia_gateway.repositories.gateway_keys import create_gateway_api_key, revoke_gateway_api_key  # noqa: E402
from angemedia_gateway.request_hash_builders import build_image_request_hash_payload  # noqa: E402
from angemedia_gateway.routing import resolve_chain  # noqa: E402
from angemedia_gateway.schemas import ImageRequest  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402


class BuiltinProviderRuntimeConfigTest(unittest.TestCase):
    provider_id = "bytedance"

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
            conn.execute("DELETE FROM provider_runtime_configs WHERE provider_id = ?", (self.provider_id,))

    def test_admin_list_is_safe_and_gateway_key_is_rejected(self) -> None:
        response = self.client.get("/v1/admin/provider-configs")
        self.assertEqual(response.status_code, 200, response.text)
        rows = {item["provider_id"]: item for item in response.json()["data"]}
        self.assertIn(self.provider_id, rows)
        self.assertEqual(rows[self.provider_id]["source"], "builtin")
        self.assertEqual(rows[self.provider_id]["media_types"], ["image"])
        self.assertNotIn("api_key", rows[self.provider_id])
        self.assertNotIn("Authorization", response.text)

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
        self.assertNotIn(runtime_secret, json.dumps(request_hash_payload.payload))

        C.BYTEDANCE_API_KEY = ""
        cleared = self.client.post(f"/v1/admin/provider-configs/{self.provider_id}/clear-key")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertFalse(cleared.json()["data"]["api_key_configured"])
        self.assertNotIn(runtime_secret, cleared.text)

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
        video_source = (ROOT / "scripts/angemedia_gateway/adapters/agnes_video.py").read_text(encoding="utf-8")
        self.assertIn("runtime_config_resolver", video_source)

    def test_providers_ui_keeps_custom_and_readonly_sections_with_write_only_key_input(self) -> None:
        providers_root = ROOT / "app/www/assets/studio/features/providers"
        page_source = (providers_root / "page.js").read_text(encoding="utf-8")
        builtin_source = (providers_root / "builtin-config.js").read_text(encoding="utf-8")
        api_source = (providers_root / "provider-api.js").read_text(encoding="utf-8")
        css_source = (ROOT / "app/www/assets/studio/styles/pages.css").read_text(encoding="utf-8")

        self.assertIn("renderBuiltinConfigPanel", page_source)
        self.assertIn("createProviderForm", page_source)
        self.assertIn("providerCard", page_source)
        self.assertIn("providers.catalogProviders", page_source)
        self.assertIn("providers.reservedProviders", page_source)
        self.assertIn("/admin/provider-configs", api_source)
        self.assertIn("type: 'password'", builtin_source)
        self.assertIn("value: ''", builtin_source)
        self.assertNotIn("value: provider.api_key_preview", builtin_source)
        self.assertIn("/clear-key", builtin_source)
        self.assertIn("checked: provider.enabled === true", builtin_source)
        self.assertIn("@media (max-width: 400px)", css_source)
        self.assertIn(".builtin-config-actions .btn", css_source)

    def test_storage_auth_contract_is_unchanged(self) -> None:
        source = (ROOT / "scripts/angemedia_gateway/routes/storage.py").read_text(encoding="utf-8")
        self.assertIn('dependencies=[Depends(require_auth)]', source)
        self.assertIn('@router.api_route("/generated/{filename:path}", methods=["GET", "HEAD"]', source)
        self.assertIn('@router.api_route("/uploads/{filename:path}", methods=["GET", "HEAD"]', source)


if __name__ == "__main__":
    unittest.main()

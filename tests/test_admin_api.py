from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


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

    def create_custom_provider(self, provider_id: str, sort_order: int = 100, enabled: bool = True) -> dict:
        self.created_provider_ids.append(provider_id)
        response = self.client.post(
            "/v1/admin/providers",
            json={
                "id": provider_id,
                "name": f"Provider {provider_id}",
                "provider_type": "openai_image",
                "base_url": "https://example.com/v1",
                "api_key": f"sk-{provider_id}-secret",
                "default_model": "test-image-model",
                "enabled": enabled,
                "sort_order": sort_order,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

import angemedia_gateway.config as C
from angemedia_gateway.db.schema import init_db
from angemedia_gateway.repositories.admin_auth import ensure_default_admin_user
from angemedia_gateway.server import app


class ProviderTransportAdminApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="angemedia-transport-api-")
        self._original_db = C.DB_FILE
        C.DB_FILE = Path(self._tmp.name) / "angemedia.db"
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        login = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def tearDown(self) -> None:
        self.client.close()
        C.DB_FILE = self._original_db
        self._tmp.cleanup()

    def test_transport_endpoints_require_admin_session(self) -> None:
        anonymous = TestClient(app)
        try:
            self.assertEqual(anonymous.get("/v1/admin/provider-transport").status_code, 401)
            self.assertEqual(
                anonymous.post(
                    "/v1/admin/provider-transport/openai_image",
                    json={"transport_mode": "direct"},
                ).status_code,
                401,
            )
        finally:
            anonymous.close()

    def test_global_proxy_save_and_read_never_echo_secret_url(self) -> None:
        proxy = "http://alice:transport-secret@127.0.0.1:7890"
        saved = self.client.post(
            "/v1/admin/provider-transport",
            json={"transport_mode": "explicit_proxy", "proxy_url": proxy},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        data = saved.json()["data"]
        self.assertEqual(data["scope"], "global")
        self.assertEqual(data["effective_mode"], "explicit_proxy")
        self.assertTrue(data["proxy_configured"])
        self.assertNotIn("proxy_url", data)
        self.assertNotIn(proxy, saved.text)
        self.assertNotIn("transport-secret", saved.text)

        read = self.client.get("/v1/admin/provider-transport")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertNotIn(proxy, read.text)
        self.assertNotIn("transport-secret", read.text)

    def test_provider_direct_override_and_clear_restore_global(self) -> None:
        global_saved = self.client.post(
            "/v1/admin/provider-transport",
            json={"transport_mode": "explicit_proxy", "proxy_url": "http://127.0.0.1:7890"},
        )
        self.assertEqual(global_saved.status_code, 200, global_saved.text)

        direct = self.client.post(
            "/v1/admin/provider-transport/openai_image",
            json={"transport_mode": "direct"},
        )
        self.assertEqual(direct.status_code, 200, direct.text)
        self.assertEqual(direct.json()["data"]["effective_mode"], "direct")
        self.assertEqual(direct.json()["data"]["effective_source"], "provider")

        cleared = self.client.post(
            "/v1/admin/provider-transport/openai_image",
            json={"transport_mode": None},
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)
        data = cleared.json()["data"]
        self.assertIsNone(data["transport_mode"])
        self.assertEqual(data["effective_mode"], "explicit_proxy")
        self.assertEqual(data["effective_source"], "global")

    def test_unknown_provider_transport_is_rejected(self) -> None:
        missing = "missing-provider"
        read = self.client.get(f"/v1/admin/provider-transport/{missing}")
        self.assertEqual(read.status_code, 404, read.text)
        saved = self.client.post(
            f"/v1/admin/provider-transport/{missing}",
            json={"transport_mode": "direct"},
        )
        self.assertEqual(saved.status_code, 404, saved.text)

    def test_custom_provider_delete_clears_transport_before_same_id_reuse(self) -> None:
        provider_id = "custom-transport-lifecycle"
        payload = {
            "id": provider_id,
            "name": "Lifecycle Provider",
            "provider_type": "openai_image",
            "base_url": "http://192.168.1.20:3000/v1",
            "default_model": "test-model",
            "enabled": True,
        }
        created = self.client.post("/v1/admin/providers", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        configured = self.client.post(
            f"/v1/admin/provider-transport/{provider_id}",
            json={
                "transport_mode": "explicit_proxy",
                "proxy_url": "http://127.0.0.1:7890",
            },
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        self.assertEqual(configured.json()["data"]["effective_mode"], "explicit_proxy")

        deleted = self.client.delete(f"/v1/admin/providers/{provider_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json(), {"ok": True})

        recreated = self.client.post("/v1/admin/providers", json=payload)
        self.assertEqual(recreated.status_code, 200, recreated.text)
        transport = self.client.get(f"/v1/admin/provider-transport/{provider_id}")
        self.assertEqual(transport.status_code, 200, transport.text)
        data = transport.json()["data"]
        self.assertIsNone(data["transport_mode"])
        self.assertFalse(data["proxy_configured"])
        self.assertEqual(data["effective_mode"], "direct")
        self.assertEqual(data["effective_source"], "default")

    def test_invalid_proxy_and_unknown_fields_are_rejected_safely(self) -> None:
        invalid_proxy = self.client.post(
            "/v1/admin/provider-transport/openai_image",
            json={"transport_mode": "explicit_proxy", "proxy_url": "socks5://127.0.0.1:1080"},
        )
        self.assertEqual(invalid_proxy.status_code, 400, invalid_proxy.text)
        self.assertNotIn("127.0.0.1:1080", invalid_proxy.text)

        extra = self.client.post(
            "/v1/admin/provider-transport/openai_image",
            json={"transport_mode": "direct", "trust_env": True},
        )
        self.assertEqual(extra.status_code, 422, extra.text)


if __name__ == "__main__":
    unittest.main()

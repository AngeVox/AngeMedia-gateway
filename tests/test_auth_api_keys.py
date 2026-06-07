"""Gateway API Key 接入普通 API 和 Admin 边界测试。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import (  # noqa: E402
    create_gateway_api_key,
    ensure_default_admin_user,
    init_db,
    revoke_gateway_api_key,
    update_gateway_api_key,
)


class GatewayApiKeyAuthTest(unittest.TestCase):
    """普通 /v1/* 与 /v1/admin/* 的 Gateway API Key 鉴权边界。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="auth-api-key-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_base_url = C.PUBLIC_BASE_URL
        self._orig_gateway_key = C.GATEWAY_API_KEY
        self._orig_admin_user = os.environ.get("ADMIN_USERNAME")
        self._orig_admin_pass = os.environ.get("ADMIN_DEFAULT_PASSWORD")

        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.PUBLIC_BASE_URL = "http://testserver"
        C.GATEWAY_API_KEY = ""
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123456"
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.PUBLIC_BASE_URL = self._orig_base_url
        C.GATEWAY_API_KEY = self._orig_gateway_key
        if self._orig_admin_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = self._orig_admin_user
        if self._orig_admin_pass is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = self._orig_admin_pass
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> TestClient:
        client = TestClient(app)
        response = client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def create_db_key(self, *, enabled: bool = True, revoked: bool = False) -> dict:
        item = create_gateway_api_key(name="auth-test")
        if not enabled:
            updated = update_gateway_api_key(item["id"], enabled=False)
            self.assertIsNotNone(updated)
        if revoked:
            self.assertTrue(revoke_gateway_api_key(item["id"]))
        return item

    def assert_no_secret_leak(self, response_text: str, secret: str) -> None:
        self.assertNotIn(secret, response_text)
        self.assertNotIn("key_hash", response_text)

    # ── 普通 /v1/* 鉴权 ────────────────────────────────

    def test_models_open_when_no_legacy_key_and_no_db_records(self) -> None:
        """无 key 配置 + 无 DB key → require_auth 拒绝（fail-closed）。"""
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401, response.text)

    def test_models_require_auth_when_db_key_record_exists(self) -> None:
        self.create_db_key()
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401, response.text)

    def test_db_key_bearer_can_access_models(self) -> None:
        key = self.create_db_key()["key"]
        response = self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_db_key_x_api_key_can_access_models(self) -> None:
        key = self.create_db_key()["key"]
        response = self.client.get("/v1/models", headers={"X-API-Key": key})
        self.assertEqual(response.status_code, 200, response.text)

    def test_disabled_db_key_cannot_access_models(self) -> None:
        key = self.create_db_key(enabled=False)["key"]
        response = self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status_code, 401, response.text)

    def test_revoked_db_key_cannot_access_models(self) -> None:
        key = self.create_db_key(revoked=True)["key"]
        response = self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status_code, 401, response.text)

    def test_wrong_key_cannot_access_models_when_auth_enabled(self) -> None:
        self.create_db_key()
        response = self.client.get("/v1/models", headers={"Authorization": "Bearer am-wrong-key"})
        self.assertEqual(response.status_code, 401, response.text)

    def test_legacy_bearer_key_can_access_models(self) -> None:
        C.GATEWAY_API_KEY = "am-legacy-auth-test"
        response = self.client.get("/v1/models", headers={"Authorization": "Bearer am-legacy-auth-test"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_legacy_x_api_key_can_access_models(self) -> None:
        C.GATEWAY_API_KEY = "am-legacy-auth-test"
        response = self.client.get("/v1/models", headers={"X-API-Key": "am-legacy-auth-test"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_conflicting_bearer_and_x_api_key_return_401(self) -> None:
        key = self.create_db_key()["key"]
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {key}", "X-API-Key": "am-different"},
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_matching_bearer_and_x_api_key_can_access_models(self) -> None:
        key = self.create_db_key()["key"]
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {key}", "X-API-Key": key},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_revoked_db_key_record_keeps_models_auth_enabled(self) -> None:
        self.create_db_key(revoked=True)
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401, response.text)

    # ── Admin API 权限边界 ─────────────────────────────

    def test_admin_session_can_access_gateway_keys(self) -> None:
        client = self.login_admin()
        response = client.get("/v1/admin/gateway-keys")
        self.assertEqual(response.status_code, 200, response.text)

    def test_db_gateway_key_cannot_access_admin_gateway_keys(self) -> None:
        key = self.create_db_key()["key"]
        response = self.client.get("/v1/admin/gateway-keys", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_no_secret_leak(response.text, key)

    def test_legacy_gateway_key_cannot_access_admin_gateway_keys(self) -> None:
        C.GATEWAY_API_KEY = "am-legacy-admin-denied"
        response = self.client.get(
            "/v1/admin/gateway-keys",
            headers={"Authorization": "Bearer am-legacy-admin-denied"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assert_no_secret_leak(response.text, "am-legacy-admin-denied")

    def test_admin_gateway_keys_without_session_or_key_returns_401(self) -> None:
        response = self.client.get("/v1/admin/gateway-keys")
        self.assertEqual(response.status_code, 401, response.text)

    def test_admin_session_wins_when_gateway_key_header_is_present(self) -> None:
        key = self.create_db_key()["key"]
        client = self.login_admin()
        response = client.get("/v1/admin/gateway-keys", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("key_hash", response.text)

    def test_admin_session_status_does_not_accept_db_gateway_key(self) -> None:
        key = self.create_db_key()["key"]
        response = self.client.get("/v1/admin/session", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"authenticated": False})
        self.assert_no_secret_leak(response.text, key)

    def test_admin_session_status_does_not_accept_legacy_gateway_key(self) -> None:
        C.GATEWAY_API_KEY = "am-legacy-session-denied"
        response = self.client.get(
            "/v1/admin/session",
            headers={"Authorization": "Bearer am-legacy-session-denied"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"authenticated": False})
        self.assert_no_secret_leak(response.text, "am-legacy-session-denied")


class GatewayApiKeyLastUsedTest(unittest.TestCase):
    """DB-backed API Key 的 last_used_at / last_used_ip 更新测试。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="auth-lastused-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_base_url = C.PUBLIC_BASE_URL
        self._orig_gateway_key = C.GATEWAY_API_KEY
        self._orig_admin_user = os.environ.get("ADMIN_USERNAME")
        self._orig_admin_pass = os.environ.get("ADMIN_DEFAULT_PASSWORD")

        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.PUBLIC_BASE_URL = "http://testserver"
        C.GATEWAY_API_KEY = ""
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123456"
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.PUBLIC_BASE_URL = self._orig_base_url
        C.GATEWAY_API_KEY = self._orig_gateway_key
        if self._orig_admin_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = self._orig_admin_user
        if self._orig_admin_pass is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = self._orig_admin_pass
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _get_key_record(self, key_id: str) -> dict | None:
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT last_used_at, last_used_ip FROM gateway_api_keys WHERE id = ?",
                (key_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def create_db_key(self, *, enabled: bool = True, revoked: bool = False) -> dict:
        item = create_gateway_api_key(name="lastused-test")
        if not enabled:
            update_gateway_api_key(item["id"], enabled=False)
        if revoked:
            revoke_gateway_api_key(item["id"])
        return item

    def login_admin(self) -> TestClient:
        client = TestClient(app)
        response = client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return client

    # ── 1. Bearer 访问后 last_used_at 非空 ─────────────

    def test_bearer_access_updates_last_used_at(self) -> None:
        """DB-backed key 使用 Bearer 成功访问后 last_used_at 非空。"""
        key_item = self.create_db_key()
        key = key_item["key"]
        key_id = key_item["id"]
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])
        resp = self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(resp.status_code, 200, resp.text)
        record = self._get_key_record(key_id)
        self.assertIsNotNone(record["last_used_at"])

    # ── 2. last_used_ip 被记录 ─────────────────────────

    def test_bearer_access_records_last_used_ip(self) -> None:
        """DB-backed key 成功访问后 last_used_ip 被记录。"""
        key_item = self.create_db_key()
        key = key_item["key"]
        key_id = key_item["id"]
        self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        record = self._get_key_record(key_id)
        self.assertIsNotNone(record["last_used_ip"])
        self.assertNotEqual(record["last_used_ip"], "")

    # ── 3. X-API-Key 访问后 last_used_at 非空 ──────────

    def test_x_api_key_access_updates_last_used_at(self) -> None:
        """DB-backed key 使用 X-API-Key 成功访问后 last_used_at 非空。"""
        key_item = self.create_db_key()
        key = key_item["key"]
        key_id = key_item["id"]
        self.client.get("/v1/models", headers={"X-API-Key": key})
        record = self._get_key_record(key_id)
        self.assertIsNotNone(record["last_used_at"])

    # ── 4. 错误 key 不更新 last_used ───────────────────

    def test_wrong_key_does_not_update_last_used(self) -> None:
        """错误 key 访问后，真实 key 的 last_used_at 仍为空。"""
        key_item = self.create_db_key()
        key_id = key_item["id"]
        # 用错误 key 访问
        self.client.get("/v1/models", headers={"Authorization": "Bearer am-wrong-key"})
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])

    # ── 5. disabled key 不更新 last_used ───────────────

    def test_disabled_key_does_not_update_last_used(self) -> None:
        """disabled key 访问后 last_used_at 仍为空。"""
        key_item = self.create_db_key(enabled=False)
        key = key_item["key"]
        key_id = key_item["id"]
        self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])

    # ── 6. revoked key 不更新 last_used ────────────────

    def test_revoked_key_does_not_update_last_used(self) -> None:
        """revoked key 访问后 last_used_at 仍为空。"""
        key_item = self.create_db_key(revoked=True)
        key = key_item["key"]
        key_id = key_item["id"]
        self.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])

    # ── 7. legacy key 不更新 DB-backed key ─────────────

    def test_legacy_key_does_not_update_db_key_last_used(self) -> None:
        """legacy GATEWAY_API_KEY 成功访问后，DB-backed key 的 last_used_at 不变。"""
        C.GATEWAY_API_KEY = "am-legacy-lastused-test"
        key_item = self.create_db_key()
        key_id = key_item["id"]
        # 用 legacy key 访问
        self.client.get("/v1/models", headers={"Authorization": "Bearer am-legacy-lastused-test"})
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])

    # ── 8. Admin Session 不更新 last_used ──────────────

    def test_admin_session_does_not_update_last_used(self) -> None:
        """Admin Session 成功访问后，DB-backed key 的 last_used_at 不变。"""
        key_item = self.create_db_key()
        key_id = key_item["id"]
        client = self.login_admin()
        client.get("/v1/models")
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])

    # ── 9. Admin API 被 403 拒绝不更新 last_used ───────

    def test_admin_api_403_does_not_update_last_used(self) -> None:
        """DB-backed key 访问 /v1/admin/gateway-keys 返回 403，last_used_at 不变。"""
        key_item = self.create_db_key()
        key = key_item["key"]
        key_id = key_item["id"]
        resp = self.client.get(
            "/v1/admin/gateway-keys",
            headers={"Authorization": f"Bearer {key}"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        record = self._get_key_record(key_id)
        self.assertIsNone(record["last_used_at"])

    # ── 10. last_used 更新失败不阻断请求 ───────────────

    def test_last_used_update_failure_does_not_block_request(self) -> None:
        """mock update_gateway_api_key_last_used 抛异常时，请求仍返回 200。"""
        from unittest.mock import patch
        key_item = self.create_db_key()
        key = key_item["key"]

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated DB write failure")

        with patch(
            "angemedia_gateway.runtime.update_gateway_api_key_last_used",
            side_effect=_raise,
        ):
            resp = self.client.get(
                "/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)

"""Admin API 管理端点测试：gateway keys CRUD。"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
import angemedia_gateway.config as _C  # noqa: E402


class GatewayKeysApiTest(unittest.TestCase):
    """Admin API 管理 gateway keys 的端到端测试。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="gw-keys-api-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()
        # 保存原始配置
        self._orig_db = _C.DB_FILE
        self._orig_output = _C.OUTPUT_DIR
        self._orig_upload = _C.UPLOAD_DIR
        self._orig_base_url = _C.PUBLIC_BASE_URL
        self._orig_admin_user = os.environ.get("ADMIN_USERNAME")
        self._orig_admin_pass = os.environ.get("ADMIN_DEFAULT_PASSWORD")
        # 覆盖配置
        _C.DB_FILE = self._db_path
        _C.OUTPUT_DIR = self._output_dir
        _C.UPLOAD_DIR = self._upload_dir
        _C.PUBLIC_BASE_URL = "http://testserver"
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_DEFAULT_PASSWORD"] = "admin123456"
        from angemedia_gateway.state import init_db, ensure_default_admin_user  # noqa: E402
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        _C.DB_FILE = self._orig_db
        _C.OUTPUT_DIR = self._orig_output
        _C.UPLOAD_DIR = self._orig_upload
        _C.PUBLIC_BASE_URL = self._orig_base_url
        if self._orig_admin_user is None:
            os.environ.pop("ADMIN_USERNAME", None)
        else:
            os.environ["ADMIN_USERNAME"] = self._orig_admin_user
        if self._orig_admin_pass is None:
            os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        else:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = self._orig_admin_pass
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        resp = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── 1. 未登录 401 ──────────────────────────────────

    def test_unauthenticated_returns_401(self) -> None:
        """未登录访问返回 401。"""
        for method, path in [
            ("POST", "/v1/admin/gateway-keys"),
            ("GET", "/v1/admin/gateway-keys"),
        ]:
            resp = self.client.request(method, path)
            self.assertIn(resp.status_code, (401, 403), f"{method} {path}: {resp.status_code}")

    # ── 2-4. POST 创建 ─────────────────────────────────

    def test_post_create_returns_key_and_no_key_hash(self) -> None:
        """POST 创建返回完整 key，不返回 key_hash。"""
        self.login_admin()
        resp = self.client.post(
            "/v1/admin/gateway-keys",
            json={"name": "test-key", "note": "for testing"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("data", body)
        data = body["data"]
        self.assertIn("key", data)
        self.assertTrue(data["key"].startswith("am-"))
        self.assertNotIn("key_hash", data)
        self.assertEqual(data["name"], "test-key")
        self.assertEqual(data["note"], "for testing")
        self.assertTrue(data["enabled"])
        # warning 字段
        self.assertIn("warning", body)
        self.assertIn("仅显示一次", body["warning"])

    # ── 5. DB 不保存完整 key ───────────────────────────

    def test_db_does_not_store_full_key(self) -> None:
        """POST 创建后 DB 中不保存完整 key。"""
        self.login_admin()
        resp = self.client.post(
            "/v1/admin/gateway-keys",
            json={"name": "no-leak"},
        )
        data = resp.json()["data"]
        full_key = data["key"]
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM gateway_api_keys WHERE id = ?", (data["id"],)
            ).fetchone()
            self.assertIsNotNone(row)
            for col in row.keys():
                val = str(row[col] or "")
                self.assertNotIn(full_key, val, f"完整 key 出现在 DB 字段 {col}")
        finally:
            conn.close()

    # ── 6-8. GET list ──────────────────────────────────

    def test_get_list_returns_metadata(self) -> None:
        """GET list 返回 key_prefix 等 metadata。"""
        self.login_admin()
        self.client.post("/v1/admin/gateway-keys", json={"name": "list-test"})
        resp = self.client.get("/v1/admin/gateway-keys")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["data"]
        self.assertGreaterEqual(len(items), 1)
        item = items[0]
        self.assertIn("id", item)
        self.assertIn("key_prefix", item)
        self.assertIn("name", item)

    def test_get_list_no_full_key(self) -> None:
        """GET list 不返回完整 key。"""
        self.login_admin()
        self.client.post("/v1/admin/gateway-keys", json={"name": "no-key"})
        resp = self.client.get("/v1/admin/gateway-keys")
        for item in resp.json()["data"]:
            self.assertNotIn("key", item)

    def test_get_list_no_key_hash(self) -> None:
        """GET list 不返回 key_hash。"""
        self.login_admin()
        self.client.post("/v1/admin/gateway-keys", json={"name": "no-hash"})
        resp = self.client.get("/v1/admin/gateway-keys")
        for item in resp.json()["data"]:
            self.assertNotIn("key_hash", item)

    # ── 9-12. GET single ───────────────────────────────

    def test_get_single_returns_metadata(self) -> None:
        """GET single 存在时返回 metadata。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "single"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.get(f"/v1/admin/gateway-keys/{key_id}")
        self.assertEqual(resp.status_code, 200)
        item = resp.json()["data"]
        self.assertEqual(item["id"], key_id)
        self.assertEqual(item["name"], "single")

    def test_get_single_no_full_key(self) -> None:
        """GET single 不返回完整 key。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "no-key"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.get(f"/v1/admin/gateway-keys/{key_id}")
        self.assertNotIn("key", resp.json()["data"])

    def test_get_single_no_key_hash(self) -> None:
        """GET single 不返回 key_hash。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "no-hash"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.get(f"/v1/admin/gateway-keys/{key_id}")
        self.assertNotIn("key_hash", resp.json()["data"])

    def test_get_single_not_found_returns_404(self) -> None:
        """GET single 不存在返回 404。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/gateway-keys/nonexistent-id")
        self.assertEqual(resp.status_code, 404)

    # ── 13-17. PATCH ───────────────────────────────────

    def test_patch_updates_name(self) -> None:
        """PATCH 可更新 name。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "old"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.patch(f"/v1/admin/gateway-keys/{key_id}", json={"name": "new"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["name"], "new")

    def test_patch_updates_note(self) -> None:
        """PATCH 可更新 note。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "n"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.patch(f"/v1/admin/gateway-keys/{key_id}", json={"note": "updated"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["note"], "updated")

    def test_patch_updates_enabled_false(self) -> None:
        """PATCH 可更新 enabled=false。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "e"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.patch(f"/v1/admin/gateway-keys/{key_id}", json={"enabled": False})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["data"]["enabled"])

    def test_patch_cannot_override_key_fields(self) -> None:
        """PATCH 不能通过请求体覆盖 key_hash / key / key_prefix。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "safe"})
        key_id = create_resp.json()["data"]["id"]
        original = self.client.get(f"/v1/admin/gateway-keys/{key_id}").json()["data"]
        resp = self.client.patch(
            f"/v1/admin/gateway-keys/{key_id}",
            json={"name": "hacked", "key": "am-fake", "key_hash": "fakehash", "key_prefix": "fake"},
        )
        self.assertEqual(resp.status_code, 422)
        # 原始数据未改变
        after = self.client.get(f"/v1/admin/gateway-keys/{key_id}").json()["data"]
        self.assertEqual(after["name"], original["name"])
        self.assertEqual(after["key_prefix"], original["key_prefix"])

    def test_patch_not_found_returns_404(self) -> None:
        """PATCH 不存在返回 404。"""
        self.login_admin()
        resp = self.client.patch("/v1/admin/gateway-keys/fake-id", json={"name": "x"})
        self.assertEqual(resp.status_code, 404)

    # ── 18-21. DELETE ──────────────────────────────────

    def test_delete_revokes_key(self) -> None:
        """DELETE 吊销 key，返回 ok。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "del"})
        key_id = create_resp.json()["data"]["id"]
        resp = self.client.delete(f"/v1/admin/gateway-keys/{key_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_delete_sets_revoked_at(self) -> None:
        """DELETE 后 revoked_at 不为 null。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "rev"})
        key_id = create_resp.json()["data"]["id"]
        self.client.delete(f"/v1/admin/gateway-keys/{key_id}")
        detail = self.client.get(f"/v1/admin/gateway-keys/{key_id}").json()["data"]
        self.assertIsNotNone(detail["revoked_at"])

    def test_delete_not_found_returns_404(self) -> None:
        """DELETE 不存在返回 404。"""
        self.login_admin()
        resp = self.client.delete("/v1/admin/gateway-keys/fake-id")
        self.assertEqual(resp.status_code, 404)

    def test_double_delete_returns_404(self) -> None:
        """DELETE 已吊销 key 第二次返回 404。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "dbl"})
        key_id = create_resp.json()["data"]["id"]
        # 第一次 DELETE 成功
        resp1 = self.client.delete(f"/v1/admin/gateway-keys/{key_id}")
        self.assertEqual(resp1.status_code, 200)
        self.assertTrue(resp1.json()["ok"])
        # 第二次 DELETE 返回 404
        resp2 = self.client.delete(f"/v1/admin/gateway-keys/{key_id}")
        self.assertEqual(resp2.status_code, 404)

    def test_delete_prevents_verify(self) -> None:
        """DELETE 后该 key 无法通过 verify_gateway_api_key 验证。"""
        from angemedia_gateway.state import verify_gateway_api_key
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "v"})
        full_key = create_resp.json()["data"]["key"]
        key_id = create_resp.json()["data"]["id"]
        # 吊销前可验证
        self.assertIsNotNone(verify_gateway_api_key(full_key))
        # 吊销后不可验证
        self.client.delete(f"/v1/admin/gateway-keys/{key_id}")
        self.assertIsNone(verify_gateway_api_key(full_key))

    # ── 22. 旧 config 表不受影响 ──────────────────────

    def test_legacy_config_key_not_affected(self) -> None:
        """Admin API 创建新 key 不影响旧 config 表中的 GATEWAY_API_KEY。"""
        from angemedia_gateway.state import set_config, get_config
        self.login_admin()
        legacy_value = "am-legacy-admin-api-test"
        set_config("GATEWAY_API_KEY", legacy_value)
        self.assertEqual(get_config("GATEWAY_API_KEY"), legacy_value)
        # 创建新 key
        self.client.post("/v1/admin/gateway-keys", json={"name": "new-multi"})
        # 旧值不变
        self.assertEqual(get_config("GATEWAY_API_KEY"), legacy_value)

    # ── 23-24. 全面安全检查 ────────────────────────────

    def test_all_non_create_responses_exclude_full_key(self) -> None:
        """所有非创建响应都不包含完整 key。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "sec"})
        key_id = create_resp.json()["data"]["id"]
        full_key = create_resp.json()["data"]["key"]
        # GET list
        list_resp = self.client.get("/v1/admin/gateway-keys")
        for item in list_resp.json()["data"]:
            self.assertNotIn("key", item)
        # GET single
        get_resp = self.client.get(f"/v1/admin/gateway-keys/{key_id}")
        self.assertNotIn("key", get_resp.json()["data"])
        # PATCH
        patch_resp = self.client.patch(f"/v1/admin/gateway-keys/{key_id}", json={"name": "sec2"})
        self.assertNotIn("key", patch_resp.json()["data"])

    def test_all_responses_exclude_key_hash(self) -> None:
        """所有响应都不包含 key_hash。"""
        self.login_admin()
        create_resp = self.client.post("/v1/admin/gateway-keys", json={"name": "kh"})
        key_id = create_resp.json()["data"]["id"]
        # POST
        self.assertNotIn("key_hash", create_resp.json()["data"])
        # GET list
        list_resp = self.client.get("/v1/admin/gateway-keys")
        for item in list_resp.json()["data"]:
            self.assertNotIn("key_hash", item)
        # GET single
        get_resp = self.client.get(f"/v1/admin/gateway-keys/{key_id}")
        self.assertNotIn("key_hash", get_resp.json()["data"])
        # PATCH
        patch_resp = self.client.patch(f"/v1/admin/gateway-keys/{key_id}", json={"name": "kh2"})
        self.assertNotIn("key_hash", patch_resp.json()["data"])

"""GET/DELETE /v1/assets API 路由测试。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# 环境变量必须在 app 导入前设置（admin 密码 init_db 依赖）
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import save_asset  # noqa: E402
import angemedia_gateway.config as _C  # noqa: E402


class AssetsApiTest(unittest.TestCase):
    """测试 /v1/assets API 端点，每个测试独立临时 DB + 存储目录。"""

    def setUp(self) -> None:
        # 临时目录
        self._tmp_dir = tempfile.mkdtemp(prefix="assets-api-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()
        # 保存原始配置
        self._orig_db = _C.DB_FILE
        self._orig_output = _C.OUTPUT_DIR
        self._orig_upload = _C.UPLOAD_DIR
        # 覆盖配置
        _C.DB_FILE = self._db_path
        _C.OUTPUT_DIR = self._output_dir
        _C.UPLOAD_DIR = self._upload_dir
        # 初始化 assets 表
        from angemedia_gateway.state import init_db, ensure_default_admin_user  # noqa: E402
        init_db()
        ensure_default_admin_user()
        # TestClient
        self.client = TestClient(app)

    def tearDown(self) -> None:
        _C.DB_FILE = self._orig_db
        _C.OUTPUT_DIR = self._orig_output
        _C.UPLOAD_DIR = self._orig_upload
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _insert_test_asset(self, asset_id: str = "api-001", **overrides) -> None:
        """向 assets 表插入一条测试记录。"""
        defaults = {
            "id": asset_id,
            "filename": "test.png",
            "storage_area": "output",
            "relative_path": f"{asset_id}.png",
            "url_path": f"/generated/{asset_id}.png",
            "media_type": "image",
            "source": "generated",
            "size": 1024,
        }
        defaults.update(overrides)
        save_asset(**defaults)

    # ── GET /v1/assets ──────────────────────────────

    def test_list_empty(self) -> None:
        """空列表返回空 data 数组。"""
        self.login_admin()
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsInstance(resp.json()["data"], list)
        self.assertEqual(len(resp.json()["data"]), 0)

    def test_list_returns_saved_asset(self) -> None:
        """返回已保存的 asset。"""
        self.login_admin()
        self._insert_test_asset("api-002")
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json()["data"]
        self.assertGreaterEqual(len(items), 1)
        ids = [item["id"] for item in items]
        self.assertIn("api-002", ids)

    def test_list_limit_offset(self) -> None:
        """支持 limit/offset 分页。"""
        self.login_admin()
        for i in range(5):
            self._insert_test_asset(
                f"page-{i:03d}",
                relative_path=f"page-{i:03d}.png",
                url_path=f"/generated/page-{i:03d}.png",
            )
        resp = self.client.get("/v1/assets?limit=2&offset=0")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()["data"]), 2)
        resp2 = self.client.get("/v1/assets?limit=2&offset=2")
        self.assertEqual(resp2.status_code, 200, resp.text)
        self.assertEqual(len(resp2.json()["data"]), 2)

    def test_list_returns_fields(self) -> None:
        """返回包含所有预期字段。"""
        self.login_admin()
        self._insert_test_asset(
            "api-005", prompt="a cat", model="model-x",
            provider="test-p", duration_ms=3000,
        )
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        item = resp.json()["data"][0]
        expected_keys = {
            "id", "filename", "storage_area", "relative_path", "url_path",
            "media_type", "source", "size", "prompt", "model", "provider",
            "duration_ms", "created_at",
        }
        self.assertTrue(
            expected_keys.issubset(set(item.keys())),
            f"缺少字段: {expected_keys - set(item.keys())}",
        )

    # ── GET /v1/assets/{asset_id} ───────────────────

    def test_get_single_exists(self) -> None:
        """存在时返回单条 asset。"""
        self.login_admin()
        self._insert_test_asset("api-010")
        resp = self.client.get("/v1/assets/api-010")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["id"], "api-010")

    def test_get_single_not_found(self) -> None:
        """不存在时返回 404。"""
        self.login_admin()
        resp = self.client.get("/v1/assets/nonexistent-id")
        self.assertEqual(resp.status_code, 404, resp.text)

    # ── DELETE /v1/assets/{asset_id} ────────────────

    def test_delete_exists(self) -> None:
        """存在时删除 DB 记录并返回 ok。"""
        self.login_admin()
        self._insert_test_asset("api-020")
        resp = self.client.delete("/v1/assets/api-020")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["ok"])
        # 确认已删除
        resp2 = self.client.get("/v1/assets/api-020")
        self.assertEqual(resp2.status_code, 404, resp2.text)

    def test_delete_not_found(self) -> None:
        """不存在时返回 404。"""
        self.login_admin()
        resp = self.client.delete("/v1/assets/nonexistent-id")
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_delete_removes_file(self) -> None:
        """删除对应临时 OUTPUT_DIR 中的文件。"""
        self.login_admin()
        test_file = self._output_dir / "api-del-file.png"
        test_file.write_bytes(b"test data")
        self.assertTrue(test_file.exists())
        self._insert_test_asset(
            "api-030",
            relative_path="api-del-file.png",
            url_path="/generated/api-del-file.png",
        )
        resp = self.client.delete("/v1/assets/api-030")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(test_file.exists(), "物理文件应被删除")

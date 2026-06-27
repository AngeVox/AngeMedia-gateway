"""GET/DELETE /v1/assets API 路由测试。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# 环境变量必须在 app 导入前设置（admin 密码 init_db 依赖）
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

from fastapi.testclient import TestClient  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import create_job, record_generation, save_asset  # noqa: E402
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
        """返回安全 whitelist 字段。"""
        self.login_admin()
        self._insert_test_asset(
            "api-005", prompt="a cat", model="model-x",
            provider="test-p", duration_ms=3000,
        )
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        item = resp.json()["data"][0]
        expected_keys = {
            "id", "filename", "url_path",
            "media_type", "source", "size", "prompt", "model", "provider",
            "duration_ms", "created_at", "job_id", "job", "generation",
        }
        self.assertTrue(
            expected_keys.issubset(set(item.keys())),
            f"缺少字段: {expected_keys - set(item.keys())}",
        )
        self.assertNotIn("storage_area", item)
        self.assertNotIn("relative_path", item)

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

    # ── job_id 相关测试 ────────────────────────────────

    def test_list_includes_job_id(self) -> None:
        """GET /v1/assets list 返回的每个 asset 包含 job_id。"""
        self.login_admin()
        self._insert_test_asset("jid-list-001", job_id="job-a")
        self._insert_test_asset("jid-list-002")
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200)
        for item in resp.json()["data"]:
            self.assertIn("job_id", item)

    def test_list_job_id_null_appears_as_null(self) -> None:
        """job_id=NULL 的 asset 在响应中表现为 null。"""
        self.login_admin()
        self._insert_test_asset("jid-null-001")
        resp = self.client.get("/v1/assets")
        items = resp.json()["data"]
        jid_item = [i for i in items if i["id"] == "jid-null-001"][0]
        self.assertIsNone(jid_item["job_id"])

    def test_list_job_id_string_appears(self) -> None:
        """job_id=xxx 的 asset 在响应中返回对应字符串。"""
        self.login_admin()
        self._insert_test_asset("jid-str-001", job_id="job-xyz")
        resp = self.client.get("/v1/assets")
        items = resp.json()["data"]
        jid_item = [i for i in items if i["id"] == "jid-str-001"][0]
        self.assertEqual(jid_item["job_id"], "job-xyz")

    def test_detail_includes_job_id(self) -> None:
        """GET /v1/assets/{asset_id} detail 返回 job_id。"""
        self.login_admin()
        self._insert_test_asset("jid-det-001", job_id="job-det")
        resp = self.client.get("/v1/assets/jid-det-001")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["job_id"], "job-det")

    def test_filter_by_job_id(self) -> None:
        """GET /v1/assets?job_id=job-a 只返回 job-a 关联资产。"""
        self.login_admin()
        self._insert_test_asset("jid-filt-a1", job_id="job-a")
        self._insert_test_asset("jid-filt-a2", job_id="job-a")
        self._insert_test_asset("jid-filt-b1", job_id="job-b")
        resp = self.client.get("/v1/assets?job_id=job-a")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["data"]
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(item["job_id"], "job-a")

    def test_no_job_id_returns_all(self) -> None:
        """未传 job_id 时仍返回全部 assets。"""
        self.login_admin()
        self._insert_test_asset("jid-all-1", job_id="job-x")
        self._insert_test_asset("jid-all-2")
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["data"]
        self.assertGreaterEqual(len(items), 2)

    def test_nonexistent_job_id_returns_empty(self) -> None:
        """不存在的 job_id 返回空列表，不应 500。"""
        self.login_admin()
        self._insert_test_asset("jid-empty-001")
        resp = self.client.get("/v1/assets?job_id=nonexistent-job-id")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_list_includes_safe_linked_job_and_generation_summary(self) -> None:
        """Assets list 展示 linked job / generation 安全摘要。"""
        self.login_admin()
        job = create_job(
            kind="image",
            status="succeeded",
            provider="provider-a",
            model="model-a",
            prompt="asset prompt",
            request_hash="b" * 64,
            request_hash_version=1,
            input_json='{"api_key":"sk-asset-input-secret-123456"}',
            output_json='{"signed_url":"https://provider.test/a.png?token=asset-secret"}',
        )
        self._insert_test_asset(
            "asset-linked-001",
            provider="provider-a",
            model="model-a",
            media_type="image",
            url_path="/generated/asset-linked-001.png",
            job_id=job["id"],
        )
        record_generation(
            media_type="image",
            prompt="asset prompt",
            enhanced_prompt=None,
            model="model-a",
            status="completed",
            result={"data": [{"url": "https://provider.test/a.png?token=asset-secret"}]},
            provider="provider-a",
            duration_ms=25,
            job_id=job["id"],
        )

        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        item = [entry for entry in resp.json()["data"] if entry["id"] == "asset-linked-001"][0]
        self.assertEqual(item["provider"], "provider-a")
        self.assertEqual(item["model"], "model-a")
        self.assertEqual(item["media_type"], "image")
        self.assertEqual(item["job"]["job_id"], job["id"])
        self.assertEqual(item["job"]["status"], "succeeded")
        self.assertEqual(item["generation"]["media_type"], "image")
        body = json.dumps(item, ensure_ascii=False)
        self.assertNotIn("request_hash", body)
        self.assertNotIn("input_json", body)
        self.assertNotIn("output_json", body)
        self.assertNotIn("sk-asset-input-secret-123456", body)
        self.assertNotIn("token=asset-secret", body)

    def test_list_hides_local_storage_and_signed_provider_urls(self) -> None:
        """Assets presenter 不暴露 storage 路径或 signed/provider URL。"""
        self.login_admin()
        self._insert_test_asset(
            "asset-unsafe-path",
            storage_area="output",
            relative_path="nested/local-only.png",
            url_path="https://provider.test/local-only.png?token=secret",
            provider="p",
            model="m",
        )
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        item = [entry for entry in resp.json()["data"] if entry["id"] == "asset-unsafe-path"][0]
        self.assertIsNone(item["url_path"])
        body = json.dumps(item, ensure_ascii=False)
        self.assertNotIn("storage_area", body)
        self.assertNotIn("relative_path", body)
        self.assertNotIn("nested/local-only.png", body)
        self.assertNotIn("token=secret", body)

    def test_legacy_asset_without_job_renders_safely(self) -> None:
        """没有 job_id 的旧资产仍安全返回，不应 500。"""
        self.login_admin()
        self._insert_test_asset("asset-legacy-001", job_id=None)
        resp = self.client.get("/v1/assets")
        self.assertEqual(resp.status_code, 200, resp.text)
        item = [entry for entry in resp.json()["data"] if entry["id"] == "asset-legacy-001"][0]
        self.assertIsNone(item["job_id"])
        self.assertIsNone(item["job"])
        self.assertIsNone(item["generation"])

    def test_upload_cannot_fake_job_id_via_query(self) -> None:
        """upload API 不能通过 query 参数伪造 job_id。"""
        self.login_admin()
        resp = self.client.post(
            "/v1/uploads?job_id=fake-job-id",
            files={"files": ("upload-query-test.png", b"fake", "image/png")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # 用 upload 响应中的 filename 定位 asset
        upload_filename = resp.json()["data"][0]["filename"]
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT job_id FROM assets WHERE storage_area = 'upload' AND relative_path = ?",
                (upload_filename,),
            ).fetchone()
            self.assertIsNotNone(row, "upload asset 应已创建")
            self.assertIsNone(row[0], "upload asset 的 job_id 不应被伪造")

    def test_upload_cannot_fake_job_id_via_form(self) -> None:
        """upload API 不能通过 form 字段伪造 job_id。"""
        self.login_admin()
        resp = self.client.post(
            "/v1/uploads",
            files={"files": ("upload-form-test.png", b"fake", "image/png")},
            data={"job_id": "fake-job-id"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        upload_filename = resp.json()["data"][0]["filename"]
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute(
                "SELECT job_id FROM assets WHERE storage_area = 'upload' AND relative_path = ?",
                (upload_filename,),
            ).fetchone()
            self.assertIsNotNone(row, "upload asset 应已创建")
            self.assertIsNone(row[0], "upload asset 的 job_id 不应被伪造")

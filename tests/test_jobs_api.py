"""Jobs 查询 API 测试。"""
from __future__ import annotations

import json
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
    create_job,
    init_db,
    ensure_default_admin_user,
)


class _JobsApiTestBase(unittest.TestCase):
    """共享 setUp/tearDown。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="jobs-api-test-")
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

    def login_admin(self) -> None:
        resp = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def create_test_job(self, **kwargs) -> dict:
        defaults = {"kind": "image", "status": "succeeded", "prompt": "test prompt"}
        defaults.update(kwargs)
        return create_job(**defaults)


# ── 1. 未认证 401 ─────────────────────────────────────

class JobsApiAuthTest(_JobsApiTestBase):
    """鉴权测试。"""

    def test_unauthenticated_list_returns_401(self) -> None:
        """auth enabled 且无 key 返回 401。"""
        C.GATEWAY_API_KEY = "some-key"
        resp = self.client.get("/v1/jobs")
        self.assertEqual(resp.status_code, 401)

    def test_db_key_can_access_list(self) -> None:
        """DB-backed API Key 可以访问 GET /v1/jobs。"""
        from angemedia_gateway.state import create_gateway_api_key
        key_item = create_gateway_api_key(name="test")
        resp = self.client.get(
            "/v1/jobs",
            headers={"Authorization": f"Bearer {key_item['key']}"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_legacy_key_can_access_list(self) -> None:
        """legacy GATEWAY_API_KEY 可以访问 GET /v1/jobs。"""
        C.GATEWAY_API_KEY = "am-legacy-test-key"
        resp = self.client.get(
            "/v1/jobs",
            headers={"Authorization": "Bearer am-legacy-test-key"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_admin_session_can_access_list(self) -> None:
        """Admin Session 可以访问 GET /v1/jobs。"""
        self.login_admin()
        resp = self.client.get("/v1/jobs")
        self.assertEqual(resp.status_code, 200)


# ── 2-6. GET /v1/jobs 基本功能 ────────────────────────

class JobsApiListTest(_JobsApiTestBase):
    """GET /v1/jobs 列表端点。"""

    def test_empty_table_returns_empty_list(self) -> None:
        """空表返回 object=list, data=[]。"""
        self.login_admin()
        resp = self.client.get("/v1/jobs")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["object"], "list")
        self.assertEqual(body["data"], [])
        self.assertEqual(body["limit"], 50)
        self.assertEqual(body["offset"], 0)

    def test_list_returns_jobs(self) -> None:
        """GET /v1/jobs 返回列表。"""
        self.create_test_job(kind="image", prompt="cat")
        self.create_test_job(kind="video", prompt="dog")
        self.login_admin()
        resp = self.client.get("/v1/jobs")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 2)

    def test_filter_by_kind_image(self) -> None:
        """kind=image 过滤正确。"""
        self.create_test_job(kind="image", prompt="img")
        self.create_test_job(kind="video", prompt="vid")
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"kind": "image"})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["kind"], "image")

    def test_filter_by_kind_video(self) -> None:
        """kind=video 过滤正确。"""
        self.create_test_job(kind="image", prompt="img")
        self.create_test_job(kind="video", prompt="vid")
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"kind": "video"})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["kind"], "video")

    def test_filter_by_status(self) -> None:
        """status 过滤正确。"""
        self.create_test_job(kind="image", status="succeeded")
        self.create_test_job(kind="image", status="failed")
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"status": "succeeded"})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "succeeded")

    def test_limit_offset_pagination(self) -> None:
        """limit/offset 分页正确。"""
        for i in range(5):
            self.create_test_job(kind="image", prompt=f"job-{i}")
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"limit": 2, "offset": 0})
        data = resp.json()["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual(resp.json()["limit"], 2)
        self.assertEqual(resp.json()["offset"], 0)

    def test_invalid_kind_returns_400(self) -> None:
        """非法 kind 返回 400。"""
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"kind": "audio"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_status_returns_400(self) -> None:
        """非法 status 返回 400。"""
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"status": "unknown"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_limit_returns_400(self) -> None:
        """非法 limit 返回 400。"""
        self.login_admin()
        resp = self.client.get("/v1/jobs", params={"limit": -1})
        self.assertEqual(resp.status_code, 422)

    def test_list_excludes_input_output_json(self) -> None:
        """List response 不包含完整 input_json/output_json。"""
        self.create_test_job(kind="image", prompt="test",
                             input_json='{"model":"m"}', output_json='{"url":"u"}')
        self.login_admin()
        resp = self.client.get("/v1/jobs")
        item = resp.json()["data"][0]
        self.assertNotIn("input_json", item)
        self.assertNotIn("output_json", item)

    def test_list_excludes_forbidden_fields(self) -> None:
        """List response 不包含 local_path/asset_id/generation_id。"""
        self.create_test_job(kind="image", request_hash="a" * 64, request_hash_version=1)
        self.login_admin()
        resp = self.client.get("/v1/jobs")
        item = resp.json()["data"][0]
        self.assertNotIn("local_path", item)
        self.assertNotIn("asset_id", item)
        self.assertNotIn("generation_id", item)
        self.assertNotIn("request_hash", item)
        self.assertNotIn("request_hash_version", item)

    def test_list_error_message_redacted(self) -> None:
        """List response 中 error_message 已脱敏。"""
        self.create_test_job(kind="image", status="failed",
                             error_message="sk-list-secret-key-123 rejected")
        self.login_admin()
        resp = self.client.get("/v1/jobs")
        item = resp.json()["data"][0]
        self.assertNotIn("sk-list-secret-key-123", item["error_message"])
        self.assertIn("REDACTED", item["error_message"])


# ── 16-20. GET /v1/jobs/{job_id} 详情 ─────────────────

class JobsApiDetailTest(_JobsApiTestBase):
    """GET /v1/jobs/{job_id} 详情端点。"""

    def test_detail_returns_job(self) -> None:
        """GET /v1/jobs/{job_id} 返回详情。"""
        job = self.create_test_job(kind="video", prompt="detail test")
        self.login_admin()
        resp = self.client.get(f"/v1/jobs/{job['id']}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["id"], job["id"])
        self.assertEqual(data["kind"], "video")
        self.assertEqual(data["prompt"], "detail test")

    def test_detail_includes_input_output_json(self) -> None:
        """Detail response 包含 input_json/output_json。"""
        job = self.create_test_job(kind="image",
                                   input_json='{"model":"test"}',
                                   output_json='{"url":"http://x"}')
        self.login_admin()
        resp = self.client.get(f"/v1/jobs/{job['id']}")
        data = resp.json()["data"]
        self.assertIn("input_json", data)
        self.assertIn("output_json", data)
        self.assertEqual(json.loads(data["input_json"]), {"model": "test"})

    def test_detail_not_found_returns_404(self) -> None:
        """不存在 job 返回 404。"""
        self.login_admin()
        resp = self.client.get("/v1/jobs/nonexistent-id")
        self.assertEqual(resp.status_code, 404)

    def test_detail_excludes_forbidden_fields(self) -> None:
        """Detail response 不包含 local_path/asset_id/generation_id。"""
        job = self.create_test_job(kind="image", request_hash="a" * 64, request_hash_version=1)
        self.login_admin()
        resp = self.client.get(f"/v1/jobs/{job['id']}")
        data = resp.json()["data"]
        self.assertNotIn("local_path", data)
        self.assertNotIn("asset_id", data)
        self.assertNotIn("generation_id", data)
        self.assertNotIn("request_hash", data)
        self.assertNotIn("request_hash_version", data)

    def test_detail_no_secret_leak(self) -> None:
        """Detail response 中 input_json/output_json/error_message 已脱敏。"""
        job = self.create_test_job(
            kind="image",
            input_json='{"api_key": "sk-secret-abc123def456"}',
            output_json='{"Authorization": "Bearer am-real-token-xyz789"}',
            error_message="Provider rejected: sk-leaked-key-000",
        )
        self.login_admin()
        resp = self.client.get(f"/v1/jobs/{job['id']}")
        data = resp.json()["data"]
        # 原始 secret 不应出现
        self.assertNotIn("sk-secret-abc123def456", data["input_json"])
        self.assertNotIn("am-real-token-xyz789", data["output_json"])
        self.assertNotIn("sk-leaked-key-000", data["error_message"])
        # 应该被替换为 REDACTED
        self.assertIn("REDACTED", data["input_json"])
        self.assertIn("REDACTED", data["output_json"])
        self.assertIn("REDACTED", data["error_message"])


# ── 21-22. 暂不做端点验证 ────────────────────────────

class JobsApiNotImplementedTest(_JobsApiTestBase):
    """暂不做端点返回 404/405。"""

    def test_cancel_endpoint_not_exists(self) -> None:
        """POST /v1/jobs/{job_id}/cancel 不存在，返回 404 或 405。"""
        self.login_admin()
        resp = self.client.post("/v1/jobs/fake-id/cancel")
        self.assertIn(resp.status_code, (404, 405))

    def test_delete_endpoint_not_exists(self) -> None:
        """DELETE /v1/jobs/{job_id} 不存在，返回 404 或 405。"""
        self.login_admin()
        resp = self.client.delete("/v1/jobs/fake-id")
        self.assertIn(resp.status_code, (404, 405))

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
        from angemedia_gateway.state import create_gateway_api_key
        key_item = create_gateway_api_key(name="test")
        self.headers = {"Authorization": f"Bearer {key_item['key']}"}

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
        resp = self.client.get("/v1/admin/jobs")
        self.assertEqual(resp.status_code, 401)

    def test_db_key_cannot_access_admin_jobs_list(self) -> None:
        """DB-backed API Key 不能访问 Admin Jobs 任务中心。"""
        from angemedia_gateway.state import create_gateway_api_key
        key_item = create_gateway_api_key(name="test")
        resp = self.client.get(
            "/v1/admin/jobs",
            headers={"Authorization": f"Bearer {key_item['key']}"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_legacy_key_cannot_access_admin_jobs_list(self) -> None:
        """legacy GATEWAY_API_KEY 不能访问 Admin Jobs 任务中心。"""
        C.GATEWAY_API_KEY = "am-legacy-test-key"
        resp = self.client.get(
            "/v1/admin/jobs",
            headers={"Authorization": "Bearer am-legacy-test-key"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_session_can_access_list(self) -> None:
        """Admin Session 可以访问 GET /v1/admin/jobs。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs")
        self.assertEqual(resp.status_code, 200)


# ── 2-6. GET /v1/jobs 基本功能 ────────────────────────

class JobsApiListTest(_JobsApiTestBase):
    """GET /v1/jobs 列表端点。"""

    def test_empty_table_returns_empty_list(self) -> None:
        """空表返回 object=list, data=[]。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["object"], "list")
        self.assertEqual(body["data"], [])
        self.assertEqual(body["limit"], 50)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["total"], 0)

    def test_list_returns_jobs(self) -> None:
        """GET /v1/jobs 返回列表。"""
        self.create_test_job(kind="image", prompt="cat")
        self.create_test_job(kind="video", prompt="dog")
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 2)

    def test_filter_by_kind_image(self) -> None:
        """kind=image 过滤正确。"""
        self.create_test_job(kind="image", prompt="img")
        self.create_test_job(kind="video", prompt="vid")
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"kind": "image"})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["kind"], "image")

    def test_filter_by_kind_video(self) -> None:
        """kind=video 过滤正确。"""
        self.create_test_job(kind="image", prompt="img")
        self.create_test_job(kind="video", prompt="vid")
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"kind": "video"})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["kind"], "video")

    def test_filter_by_status(self) -> None:
        """status 过滤正确。"""
        self.create_test_job(kind="image", status="succeeded")
        self.create_test_job(kind="image", status="failed")
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"status": "succeeded"})
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "succeeded")

    def test_filter_by_provider_and_model(self) -> None:
        """provider/model 过滤正确。"""
        self.create_test_job(kind="image", provider="p1", model="m1")
        self.create_test_job(kind="image", provider="p2", model="m2")
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"provider": "p1", "model": "m1"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["provider"], "p1")
        self.assertEqual(data[0]["model"], "m1")

    def test_limit_offset_pagination(self) -> None:
        """limit/offset 分页正确。"""
        for i in range(5):
            self.create_test_job(kind="image", prompt=f"job-{i}")
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"limit": 2, "offset": 0})
        data = resp.json()["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual(resp.json()["limit"], 2)
        self.assertEqual(resp.json()["offset"], 0)
        self.assertEqual(resp.json()["total"], 5)

    def test_invalid_kind_returns_400(self) -> None:
        """非法 kind 返回 400。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"kind": "audio"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_status_returns_400(self) -> None:
        """非法 status 返回 400。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"status": "unknown"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_limit_returns_400(self) -> None:
        """非法 limit 返回 400。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"limit": -1})
        self.assertEqual(resp.status_code, 422)

    def test_invalid_sort_returns_400(self) -> None:
        """非法 sort 返回结构化 400。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs", params={"sort": "request_hash"})
        self.assertEqual(resp.status_code, 400)

    def test_list_excludes_input_output_json(self) -> None:
        """List response 不包含完整 input_json/output_json。"""
        self.create_test_job(kind="image", prompt="test",
                             input_json='{"model":"m"}', output_json='{"url":"u"}')
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs")
        item = resp.json()["data"][0]
        self.assertNotIn("input_json", item)
        self.assertNotIn("output_json", item)

    def test_list_excludes_forbidden_fields(self) -> None:
        """List response 不包含 local_path/asset_id/generation_id。"""
        self.create_test_job(kind="image", request_hash="a" * 64, request_hash_version=1)
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs")
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
        resp = self.client.get("/v1/admin/jobs")
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
        resp = self.client.get(f"/v1/admin/jobs/{job['id']}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["job_id"], job["id"])
        self.assertEqual(data["kind"], "video")
        self.assertEqual(data["prompt_summary"], "detail test")

    def test_detail_includes_safe_input_output_summary(self) -> None:
        """Detail response 只包含安全 input/output summary。"""
        job = self.create_test_job(kind="image",
                                   input_json='{"model":"test","api_key":"sk-secret-abc123def456"}',
                                   output_json='{"url":"/generated/a.png","signed_url":"https://x.test/a.png?token=secret"}')
        self.login_admin()
        resp = self.client.get(f"/v1/admin/jobs/{job['id']}")
        data = resp.json()["data"]
        self.assertNotIn("input_json", data)
        self.assertNotIn("output_json", data)
        self.assertEqual(data["input_summary"]["model"], "test")
        self.assertIn("url", data["output_summary"])
        self.assertNotIn("sk-secret-abc123def456", json.dumps(data, ensure_ascii=False))
        self.assertNotIn("token=secret", json.dumps(data, ensure_ascii=False))

    def test_detail_not_found_returns_404(self) -> None:
        """不存在 job 返回 404。"""
        self.login_admin()
        resp = self.client.get("/v1/admin/jobs/nonexistent-id")
        self.assertEqual(resp.status_code, 404)

    def test_detail_excludes_forbidden_fields(self) -> None:
        """Detail response 不包含 local_path/asset_id/generation_id。"""
        job = self.create_test_job(kind="image", request_hash="a" * 64, request_hash_version=1)
        self.login_admin()
        resp = self.client.get(f"/v1/admin/jobs/{job['id']}")
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
        resp = self.client.get(f"/v1/admin/jobs/{job['id']}")
        data = resp.json()["data"]
        body = json.dumps(data, ensure_ascii=False)
        # 原始 secret 不应出现
        self.assertNotIn("sk-secret-abc123def456", body)
        self.assertNotIn("am-real-token-xyz789", body)
        self.assertNotIn("sk-leaked-key-000", data["error_message"])
        # 应该被替换为 REDACTED
        self.assertIn("REDACTED", body)
        self.assertIn("REDACTED", data["error_message"])

    def test_detail_includes_events_attempts_assets_and_generation(self) -> None:
        """Detail response 聚合 events / attempts / linked assets / generation summary。"""
        from angemedia_gateway.state import append_job_event, create_job_attempt, record_generation, save_asset

        job = self.create_test_job(kind="image", provider="p1", model="m1")
        append_job_event(job["id"], "worker_attempt_started", {"token": "am-secret-token-123456"}, stage="image_generate")
        create_job_attempt(
            job_id=job["id"],
            attempt_number=1,
            stage="image_generate",
            status="failed",
            error_message="Bearer secret-token-123456789",
            detail={"signed_url": "https://x.test/a.png?token=secret"},
        )
        save_asset(
            id="asset-1",
            filename="a.png",
            storage_area="output",
            relative_path="a.png",
            url_path="/generated/a.png",
            media_type="image",
            source="generated",
            provider="p1",
            model="m1",
            duration_ms=42,
            job_id=job["id"],
        )
        record_generation(
            media_type="image",
            prompt="prompt",
            enhanced_prompt=None,
            model="m1",
            status="completed",
            result={"data": [{"url": "/generated/a.png"}], "provider": "p1", "model": "m1"},
            provider="p1",
            duration_ms=42,
            job_id=job["id"],
        )
        self.login_admin()
        resp = self.client.get(f"/v1/admin/jobs/{job['id']}")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(len(data["attempts"]), 1)
        self.assertEqual(data["assets"][0]["url_path"], "/generated/a.png")
        self.assertEqual(data["generation"]["media_type"], "image")
        body = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("am-secret-token-123456", body)
        self.assertNotIn("secret-token-123456789", body)
        self.assertNotIn("token=secret", body)


# ── 21-22. 暂不做端点验证 ────────────────────────────

class JobsApiNotImplementedTest(_JobsApiTestBase):
    """暂不做端点返回 404/405。"""

    def test_cancel_endpoint_not_exists(self) -> None:
        """POST /v1/jobs/{job_id}/cancel 不存在，返回 404 或 405。"""
        self.login_admin()
        resp = self.client.post("/v1/admin/jobs/fake-id/cancel")
        self.assertIn(resp.status_code, (404, 405))

    def test_delete_endpoint_not_exists(self) -> None:
        """DELETE /v1/jobs/{job_id} 不存在，返回 404 或 405。"""
        self.login_admin()
        resp = self.client.delete("/v1/jobs/fake-id")
        self.assertIn(resp.status_code, (404, 405))


class WErr1AJobsContractTest(unittest.TestCase):
    """W-ERR-1A-R2: 验证 Jobs API 结构化错误合同"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="jobs-api-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        C.DB_FILE = self._db_path
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        from angemedia_gateway.state import create_gateway_api_key
        key_item = create_gateway_api_key(name="test")
        self.headers = {"Authorization": f"Bearer {key_item['key']}"}

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_jobs_list_requires_structured_error_fields(self) -> None:
        """Jobs list 必须暴露 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.state import create_job

        # 创建一个 failed job
        created = create_job(
            kind="image", status="failed", prompt="test",
            error_code="all_providers_failed", error_message="test error"
        )

        self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        resp = self.client.get("/v1/admin/jobs?limit=10")
        self.assertEqual(resp.status_code, 200)
        jobs = resp.json().get("data", [])
        self.assertTrue(len(jobs) > 0, "Jobs list 应该有数据")

        # 按 job id 定位目标 job
        job = next((item for item in jobs if item.get("id") == created["id"]), None)
        self.assertIsNotNone(job, "Created job not found in list")

        # 断言结构化错误合同字段
        self.assertIn("error_category", job, "error_category 字段缺失")
        self.assertIn("human_hint", job, "human_hint 字段缺失")
        self.assertIn("retryable", job, "retryable 字段缺失")
        self.assertIn("gateway_stage", job, "gateway_stage 字段缺失")

        # 确保隐藏内部字段
        self.assertNotIn("request_hash", job)
        self.assertNotIn("request_hash_version", job)
        self.assertNotIn("input_json", job)
        self.assertNotIn("output_json", job)



# ── W-ERR-1A-R2-D1: Diagnostic values meaningful (not key-exists only) ──

class WErr1AJobsDiagnosticValuesTest(unittest.TestCase):
    """W-ERR-1A-R2-D1: Jobs API diagnostic values meaningful (not key-exists only)"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="jobs-diag-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        C.DB_FILE = self._db_path
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        from angemedia_gateway.state import create_gateway_api_key
        key_item = create_gateway_api_key(name="test")
        self.headers = {"Authorization": "Bearer " + key_item["key"]}

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _create_failed_job_with_diagnostics(
        self,
        error_category="provider_timeout",
        human_hint="network timeout retry",
        retryable=1,
        gateway_stage="provider_request",
    ) -> dict:
        from angemedia_gateway.state import create_job, update_job_status

        job = create_job(
            kind="image", status="failed", prompt="test diag",
            error_code="all_providers_failed", error_message="test error",
        )
        update_job_status(
            job["id"],
            status="failed",
            error_category=error_category,
            human_hint=human_hint,
            retryable=retryable,
            gateway_stage=gateway_stage,
        )
        return job

    def test_list_jobs_returns_meaningful_diagnostic_values(self):
        """GET /v1/jobs diagnostic fields must not be None"""
        created = self._create_failed_job_with_diagnostics()

        self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        resp = self.client.get("/v1/admin/jobs?limit=10")
        self.assertEqual(resp.status_code, 200)
        jobs = resp.json().get("data", [])
        self.assertTrue(len(jobs) > 0, "Jobs list should have data")

        # 按 job id 定位目标 job
        job_data = next((item for item in jobs if item.get("id") == created["id"]), None)
        self.assertIsNotNone(job_data, "Created job not found in list")

        self.assertIsNotNone(job_data.get("error_category"),
                             "error_category should not be None")
        self.assertEqual(job_data["error_category"], "provider_timeout")
        self.assertIsNotNone(job_data.get("human_hint"),
                             "human_hint should not be None")
        self.assertEqual(job_data["human_hint"], "network timeout retry")
        self.assertIsNotNone(job_data.get("retryable"),
                             "retryable should not be None")
        self.assertIsNotNone(job_data.get("gateway_stage"),
                             "gateway_stage should not be None")
        self.assertEqual(job_data["gateway_stage"], "provider_request")

    def test_list_jobs_retryable_is_bool(self):
        """retryable in API response should be bool type"""
        created = self._create_failed_job_with_diagnostics(retryable=1)

        self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        resp = self.client.get("/v1/admin/jobs?limit=10")
        jobs = resp.json().get("data", [])
        self.assertTrue(len(jobs) > 0, "Jobs list should have data")

        job_data = next((item for item in jobs if item.get("id") == created["id"]), None)
        self.assertIsNotNone(job_data, "Created job not found in list")

        self.assertIsNotNone(job_data.get("retryable"),
                             "retryable should not be None")
        self.assertIsInstance(job_data["retryable"], bool,
                              "retryable should be bool")


class WErr1AJobsDiagnosticDetailTest(unittest.TestCase):
    """W-ERR-1B: GET /v1/jobs/{id} 返回结构化诊断字段"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="jobs-api-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._orig_db = C.DB_FILE
        C.DB_FILE = self._db_path
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        from angemedia_gateway.state import create_gateway_api_key
        key_item = create_gateway_api_key(name="test")
        self.headers = {"Authorization": "Bearer " + key_item["key"]}

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_detail_returns_structured_error_fields(self) -> None:
        """GET /v1/jobs/{id} 返回 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.state import create_job, update_job_status

        job = create_job(
            kind="image", status="failed", prompt="test",
            error_code="all_providers_failed", error_message="test error",
        )
        update_job_status(
            job["id"],
            status="failed",
            error_category="model_unavailable",
            human_hint="请更换模型",
            retryable=0,
            gateway_stage="provider_response",
        )

        self.client.post("/v1/admin/login", json={"username": "admin", "password": "admin123456"})
        resp = self.client.get(f"/v1/admin/jobs/{job['id']}")
        self.assertEqual(resp.status_code, 200)
        job_data = resp.json().get("data", {})

        self.assertEqual(job_data["job_id"], job["id"])
        self.assertEqual(job_data["error_category"], "model_unavailable")
        self.assertEqual(job_data["human_hint"], "请更换模型")
        self.assertIsInstance(job_data["retryable"], bool)
        self.assertEqual(job_data["retryable"], False)
        self.assertEqual(job_data["gateway_stage"], "provider_response")

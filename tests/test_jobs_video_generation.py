"""视频提交路径接入 jobs 表的测试。"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")
TEST_SILICONFLOW_API_KEY = "sf-test-secret-value"

from fastapi import HTTPException  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.adapters.agnes_video import AgnesVideoError, AgnesVideoProvider  # noqa: E402
from angemedia_gateway.providers.errors import (  # noqa: E402
    BackendUnavailable,
    ProviderAuthError,
    ProviderProtocolError,
    ProviderTaskFailed,
    ProviderTimeout,
    RateLimited,
)
from angemedia_gateway.routes import media as media_routes  # noqa: E402
from angemedia_gateway.schemas import VideoRequest  # noqa: E402
from angemedia_gateway.services.media_service import (  # noqa: E402
    MediaService,
    VideoProviderDisabled,
)
from angemedia_gateway.state import (  # noqa: E402
    init_db,
    get_job,
    update_job_status,
)


class _VideoJobTestBase(unittest.TestCase):
    """共享 setUp/tearDown。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="video-job-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_base_url = C.PUBLIC_BASE_URL
        self._orig_siliconflow_api_key = C.SILICONFLOW_API_KEY

        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.PUBLIC_BASE_URL = "http://testserver"
        C.SILICONFLOW_API_KEY = TEST_SILICONFLOW_API_KEY
        init_db()
        self.service = MediaService()

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.PUBLIC_BASE_URL = self._orig_base_url
        C.SILICONFLOW_API_KEY = self._orig_siliconflow_api_key
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _count_jobs(self) -> int:
        conn = self._conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            conn.close()

    def _make_request(self, **overrides) -> VideoRequest:
        defaults = {"prompt": "a cat walking", "model": "agnes-video-v2.0"}
        defaults.update(overrides)
        return VideoRequest(**defaults)

    def _mock_agnes_submit(self, task_id: str = "agnes-task-001", status: str = "queued"):
        """返回 mock submit_task 方法。"""
        mock = AsyncMock(return_value={
            "task_id": task_id,
            "status": status,
            "provider": "agnes_video",
        })
        return mock


# ── 1-7. 异步提交成功路径 ──────────────────────────────

class VideoJobSubmitSuccessTest(_VideoJobTestBase):
    """视频异步提交成功后 job 行为。"""

    def test_submit_creates_video_job(self) -> None:
        """异步提交成功后创建 video job。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            mock_av.normalize_submit = lambda data: data
            from angemedia_gateway.services.media_service import builtin_provider_enabled
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        self.assertEqual(self._count_jobs(), 1)
        self.assertIn("job_id", result)

    def test_job_kind_is_video(self) -> None:
        """job kind == 'video'。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        job = get_job(result["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["kind"], "video")

    def test_job_status_is_running(self) -> None:
        """异步提交 job status == 'running'。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit(status="queued")
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        job = get_job(result["job_id"])
        self.assertEqual(job["status"], "running")

    def test_job_records_prompt_model_provider(self) -> None:
        """job 记录 prompt/model/provider。"""
        req = self._make_request(prompt="a dog running")
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        job = get_job(result["job_id"])
        self.assertEqual(job["prompt"], "a dog running")
        self.assertEqual(job["provider"], "agnes_video")
        self.assertEqual(job["model"], "agnes-video-v2.0")

    def test_job_records_external_task_id(self) -> None:
        """job external_task_id 等于 Agnes task_id。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit(task_id="agnes-xyz-789")
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        job = get_job(result["job_id"])
        self.assertEqual(job["external_task_id"], "agnes-xyz-789")
        self.assertEqual(result["task_id"], "agnes-xyz-789")

    def test_response_preserves_task_id_and_status(self) -> None:
        """response 保留原有 task_id/status 字段。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit(task_id="tid-001", status="queued")
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        self.assertEqual(result["task_id"], "tid-001")
        self.assertIn("status", result)

    def test_response_includes_job_id(self) -> None:
        """response 顶层包含 job_id。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        self.assertIn("job_id", result)
        self.assertIsInstance(result["job_id"], str)
        self.assertTrue(len(result["job_id"]) > 0)


# ── request_hash populate 契约 ────────────────────────

class VideoJobRequestHashPopulateTest(_VideoJobTestBase):
    """video submit 主流程应在创建 job 时写入 request_hash。"""

    def _submit_video(self, req: VideoRequest, task_id: str = "hash-task-001") -> dict:
        mock_submit = self._mock_agnes_submit(task_id=task_id)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                return await_compat(self.service.create_video(req))

    def _job_hash(self, job_id: str) -> tuple[str | None, int | None]:
        job = get_job(job_id)
        self.assertIsNotNone(job)
        return job["request_hash"], job["request_hash_version"]

    def test_async_video_submit_job_writes_request_hash_and_version(self) -> None:
        """async video submit job 应写入 request_hash / request_hash_version。"""
        result = self._submit_video(self._make_request(prompt="hash video cat"), task_id="hash-video-001")

        request_hash, request_hash_version = self._job_hash(result["job_id"])
        self.assertIsNotNone(request_hash)
        self.assertEqual(len(request_hash), 64)
        self.assertEqual(request_hash_version, 1)

    def test_video_task_id_and_external_task_id_do_not_affect_hash(self) -> None:
        """相同用户 video 请求即使返回不同 task_id，也应写入相同 request_hash。"""
        req_a = self._make_request(prompt="same video hash", image="/uploads/ref-a.png", seed=11)
        req_b = self._make_request(prompt="same video hash", image="/uploads/ref-a.png", seed=11)

        first = self._submit_video(req_a, task_id="hash-task-a")
        first_hash, first_version = self._job_hash(first["job_id"])
        update_job_status(first["job_id"], status="succeeded")
        second = self._submit_video(req_b, task_id="hash-task-b")

        second_hash, second_version = self._job_hash(second["job_id"])
        self.assertIsNotNone(first_hash)
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first_version, 1)
        self.assertEqual(second_version, 1)
        self.assertNotEqual(get_job(first["job_id"])["external_task_id"], get_job(second["job_id"])["external_task_id"])

    def test_different_video_request_writes_different_hash(self) -> None:
        """不同 video 请求应写入不同 request_hash。"""
        first = self._submit_video(self._make_request(prompt="video cat", seed=11), task_id="hash-diff-a")
        second = self._submit_video(self._make_request(prompt="video dog", seed=11), task_id="hash-diff-b")

        first_hash, _ = self._job_hash(first["job_id"])
        second_hash, _ = self._job_hash(second["job_id"])
        self.assertIsNotNone(first_hash)
        self.assertIsNotNone(second_hash)
        self.assertNotEqual(first_hash, second_hash)

    def test_unsupported_video_extra_body_creates_job_with_null_hash(self) -> None:
        """unsupported video extra_body 时应 fail-open，job 正常创建但 hash/version 为 NULL。"""
        result = self._submit_video(
            self._make_request(prompt="unsupported extra body", extra_body={"motion": "pan"}),
            task_id="hash-unsupported-extra",
        )

        request_hash, request_hash_version = self._job_hash(result["job_id"])
        self.assertIsNone(request_hash)
        self.assertIsNone(request_hash_version)

    def test_wait_for_completion_path_still_does_not_create_job(self) -> None:
        """wait_for_completion=true 保持当前语义：不创建 job，也不强行写 hash。"""
        req = self._make_request(prompt="sync hash path", wait_for_completion=True)
        mock_generate = AsyncMock(return_value={
            "task_id": "sync-hash-task",
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.generate_video = mock_generate
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))

        self.assertNotIn("job_id", result)
        self.assertEqual(self._count_jobs(), 0)


# ── 8-10. 现有表行为仍正常 ────────────────────────────

class VideoJobLegacyRecordsTest(_VideoJobTestBase):
    """video_tasks / generations / assets 写入仍正常。"""

    def test_video_tasks_still_written(self) -> None:
        """video_tasks 表仍正常写入。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit(task_id="vt-001")
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                await_compat(self.service.create_video(req))
        conn = self._conn()
        try:
            row = conn.execute("SELECT task_id FROM video_tasks WHERE task_id = 'vt-001'").fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_generations_still_written(self) -> None:
        """generations 表仍正常写入。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                await_compat(self.service.create_video(req))
        conn = self._conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM generations WHERE media_type = 'video'").fetchone()[0]
            self.assertGreaterEqual(count, 1)
        finally:
            conn.close()


# ── 11. job 创建失败不阻断 ────────────────────────────

class VideoJobGracefulDegradationTest(_VideoJobTestBase):
    """job 创建失败时，视频提交仍正常。"""

    def test_create_job_failure_does_not_block_request(self) -> None:
        """mock create_job 抛异常时，视频提交仍返回成功，且不包含 job_id。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True), \
                 patch("angemedia_gateway.services.media_service.create_job", side_effect=RuntimeError("DB failure")):
                result = await_compat(self.service.create_video(req))
        self.assertIn("task_id", result)
        self.assertNotIn("job_id", result)


# ── 12. submit_task 失败不创建 job ────────────────────

class VideoJobSubmitFailureTest(_VideoJobTestBase):
    """submit_task 失败时不创建 job。"""

    def test_submit_failure_no_job_created(self) -> None:
        """submit_task 失败时不创建 job，原异常行为不变。"""
        req = self._make_request()
        mock_submit = AsyncMock(side_effect=RuntimeError("Agnes API error"))
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                with self.assertRaises(RuntimeError):
                    await_compat(self.service.create_video(req))
        self.assertEqual(self._count_jobs(), 0)


# ── 13. input_json 安全 ───────────────────────────────

class VideoJobInputJsonTest(_VideoJobTestBase):
    """input_json 安全设计。"""

    def test_input_json_excludes_image_urls(self) -> None:
        """input_json 不保存原始 image/images URL。"""
        req = self._make_request(
            image="/uploads/secret-token.png",
            images=["/uploads/frame-1.png", "/generated/frame-2.png"],
        )
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        job = get_job(result["job_id"])
        input_data = json.loads(job["input_json"])
        # 不包含原始 URL
        self.assertNotIn("image", input_data)
        self.assertNotIn("images", input_data)
        # 包含摘要
        self.assertTrue(input_data["has_image"])
        self.assertEqual(input_data["image_count"], 2)

    def test_input_json_has_safe_fields(self) -> None:
        """input_json 包含安全字段。"""
        req = self._make_request(mode="keyframes", height=512, width=768, num_frames=81, frame_rate=30)
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        job = get_job(result["job_id"])
        input_data = json.loads(job["input_json"])
        self.assertEqual(input_data["model"], "agnes-video-v2.0")
        self.assertEqual(input_data["mode"], "keyframes")
        self.assertEqual(input_data["height"], 512)
        self.assertEqual(input_data["width"], 768)
        self.assertEqual(input_data["num_frames"], 81)
        self.assertEqual(input_data["frame_rate"], 30)
        self.assertFalse(input_data["wait_for_completion"])


# ── 14. response 不泄露 secret ────────────────────────

class VideoJobSecretLeakTest(_VideoJobTestBase):
    """response 不泄露 secret。"""

    def test_response_no_secret_leak(self) -> None:
        """response 不包含真实 Agnes API key / Bearer / env secret。"""
        req = self._make_request()
        mock_submit = self._mock_agnes_submit()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        # 验证 response 中不包含环境变量中的 secret
        result_str = json.dumps(result)
        self.assertNotIn(C.SILICONFLOW_API_KEY, result_str)
        self.assertNotIn("AGNES_API_KEY", result_str)
        # 验证 input_json 也不泄露
        if "job_id" in result:
            job = get_job(result["job_id"])
            self.assertNotIn(C.SILICONFLOW_API_KEY, job["input_json"])


class VideoRouteExceptionDetailSafetyTest(_VideoJobTestBase):
    """视频路由异常 detail 脱敏与截断。"""

    def _assert_redacted_and_truncated_detail(self, detail: str, prefix: str, secret: str, bearer_token: str) -> None:
        self.assertTrue(detail.startswith(prefix), detail)
        suffix = detail[len(prefix):]
        self.assertLessEqual(len(suffix), 500)
        self.assertNotIn(secret, detail)
        self.assertNotIn(bearer_token, detail)
        self.assertNotIn("TAIL_SHOULD_BE_TRUNCATED", detail)
        self.assertIn("***REDACTED***", detail)

    def test_create_video_exception_detail_redacted_and_truncated(self) -> None:
        """POST /v1/videos 异常 detail 不回显原始 secret，并有 500 字符截断。"""
        secret = "sk-video-create-secret-token-1234567890"
        bearer_token = "video-create-bearer-token-1234567890"
        exc_text = (
            f"upstream rejected {secret}; Authorization: Bearer {bearer_token}; "
            + ("x" * 700)
            + "TAIL_SHOULD_BE_TRUNCATED"
        )
        mock_create = AsyncMock(side_effect=RuntimeError(exc_text))

        with patch.object(media_routes.media_service, "create_video", mock_create):
            with self.assertRaises(HTTPException) as caught:
                await_compat(media_routes._create_video_response(self._make_request()))

        self.assertEqual(caught.exception.status_code, 502)
        self._assert_redacted_and_truncated_detail(
            str(caught.exception.detail),
            "Agnes AI 视频生成失败：",
            secret,
            bearer_token,
        )

    def test_get_video_exception_detail_redacted_and_truncated(self) -> None:
        """GET /v1/videos/{task_id} 异常 detail 不回显原始 secret，并有 500 字符截断。"""
        secret = "sk-video-get-secret-token-1234567890"
        bearer_token = "video-get-bearer-token-1234567890"
        exc_text = (
            f"poll failed {secret}; Authorization: Bearer {bearer_token}; "
            + ("x" * 700)
            + "TAIL_SHOULD_BE_TRUNCATED"
        )
        mock_get = AsyncMock(side_effect=RuntimeError(exc_text))

        with patch.object(media_routes.media_service, "get_video", mock_get):
            with self.assertRaises(HTTPException) as caught:
                await_compat(media_routes._get_video_response("route-task-001"))

        self.assertEqual(caught.exception.status_code, 502)
        self._assert_redacted_and_truncated_detail(
            str(caught.exception.detail),
            "Agnes AI 视频任务查询失败：",
            secret,
            bearer_token,
        )


class AgnesVideoAdapterSafeMessageTest(_VideoJobTestBase):
    """Agnes video adapter 不把 raw body / raw dict 写进异常消息。"""

    def _provider(self) -> AgnesVideoProvider:
        return AgnesVideoProvider(
            api_key="av-test-secret-token-123456",
            base_url="https://agnes.example.test/v1",
            poll_interval=0,
        )

    def _client(self, *, post=None, get=None):
        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, *args, **kwargs):
                if isinstance(post, Exception):
                    raise post
                return post

            async def get(self, *args, **kwargs):
                if isinstance(get, Exception):
                    raise get
                return get

        return Client()

    def _patch_provider_client(self, client):
        return patch("angemedia_gateway.adapters.agnes_video.provider_client", return_value=client)

    def test_submit_uses_provider_client_with_trust_env_false_and_limits(self) -> None:
        """submit 通过统一 provider_client，client 固定 trust_env=False 且带 limits。"""

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, *args, **kwargs):
                return httpx.Response(202, json={"task_id": "submit-task-001"})

        with patch("angemedia_gateway.providers.http.httpx.AsyncClient", return_value=Client()) as async_client:
            result = await_compat(self._provider().submit_task(self._make_request()))

        self.assertEqual(result["task_id"], "submit-task-001")
        self.assertFalse(async_client.call_args.kwargs["trust_env"])
        self.assertIsInstance(async_client.call_args.kwargs["timeout"], httpx.Timeout)
        self.assertIsInstance(async_client.call_args.kwargs["limits"], httpx.Limits)

    def test_submit_http_error_does_not_leak_raw_body(self) -> None:
        """submit HTTP error 不泄露 upstream raw body。"""
        marker = "AGNES_VIDEO_SUBMIT_RAW_BODY_SHOULD_NOT_LEAK"
        client = self._client(post=httpx.Response(500, text=f"raw {marker} sk-secret-token"))

        with self._patch_provider_client(client):
            with self.assertRaises(BackendUnavailable) as caught:
                await_compat(self._provider().submit_task(self._make_request()))

        text = str(caught.exception)
        self.assertIn("HTTP 500", text)
        self.assertNotIn(marker, text)
        self.assertNotIn("raw", text)
        self.assertNotIn("sk-secret-token", text)
        self.assertEqual(caught.exception.error_category, "upstream")

    def test_poll_http_error_does_not_leak_raw_body(self) -> None:
        """poll HTTP error 不泄露 upstream raw body。"""
        marker = "AGNES_VIDEO_POLL_RAW_BODY_SHOULD_NOT_LEAK"
        client = self._client(get=httpx.Response(500, text=f"raw {marker} sk-secret-token"))

        with self._patch_provider_client(client):
            with self.assertRaises(BackendUnavailable) as caught:
                await_compat(self._provider().poll_task("poll-task-safe-message"))

        text = str(caught.exception)
        self.assertIn("HTTP 500", text)
        self.assertNotIn(marker, text)
        self.assertNotIn("raw", text)
        self.assertNotIn("sk-secret-token", text)
        self.assertEqual(caught.exception.error_category, "upstream")

    def test_submit_invalid_json_is_protocol_error_without_raw_body(self) -> None:
        marker = "AGNES_VIDEO_INVALID_JSON_SHOULD_NOT_LEAK"
        client = self._client(post=httpx.Response(200, text=f"not-json {marker} sk-secret-token"))

        with self._patch_provider_client(client):
            with self.assertRaises(ProviderProtocolError) as caught:
                await_compat(self._provider().submit_task(self._make_request()))

        text = str(caught.exception)
        self.assertIn("invalid JSON", text)
        self.assertEqual(caught.exception.error_category, "protocol")
        self.assertNotIn(marker, text)
        self.assertNotIn("sk-secret-token", text)

    def test_submit_timeout_and_network_errors_are_structured_and_safe(self) -> None:
        timeout_client = self._client(post=httpx.ReadTimeout("sk-timeout-secret token password"))
        with self._patch_provider_client(timeout_client):
            with self.assertRaises(ProviderTimeout) as caught_timeout:
                await_compat(self._provider().submit_task(self._make_request()))
        self.assertEqual(caught_timeout.exception.error_category, "timeout")
        self.assertNotIn("sk-timeout-secret", str(caught_timeout.exception))

        network_client = self._client(post=httpx.ConnectError("sk-network-secret token password"))
        with self._patch_provider_client(network_client):
            with self.assertRaises(BackendUnavailable) as caught_network:
                await_compat(self._provider().submit_task(self._make_request()))
        self.assertEqual(caught_network.exception.error_category, "network")
        self.assertNotIn("sk-network-secret", str(caught_network.exception))

    def test_submit_status_errors_are_classified(self) -> None:
        cases = [
            (401, ProviderAuthError, "auth"),
            (429, RateLimited, "rate_limit"),
            (500, BackendUnavailable, "upstream"),
        ]
        for status_code, expected_type, category in cases:
            with self.subTest(status_code=status_code):
                client = self._client(post=httpx.Response(status_code, text="sk-secret raw upstream body"))
                with self._patch_provider_client(client):
                    with self.assertRaises(expected_type) as caught:
                        await_compat(self._provider().submit_task(self._make_request()))
                text = str(caught.exception)
                self.assertIn(f"HTTP {status_code}", text)
                self.assertEqual(caught.exception.error_category, category)
                self.assertNotIn("sk-secret", text)
                self.assertNotIn("raw upstream body", text)

    def test_missing_task_id_does_not_leak_submit_dict(self) -> None:
        """missing task_id 不泄露 submit 原始 dict。"""
        marker = "AGNES_VIDEO_SUBMIT_DICT_SHOULD_NOT_LEAK"
        provider = self._provider()
        provider.submit_task = AsyncMock(return_value={"error": marker})  # type: ignore[method-assign]

        with self.assertRaises(ProviderProtocolError) as caught:
            await_compat(provider.generate_video(self._make_request(wait_for_completion=True)))

        text = str(caught.exception)
        self.assertIn("missing task_id", text)
        self.assertEqual(caught.exception.error_category, "protocol")
        self.assertNotIn(marker, text)
        self.assertNotIn("'error'", text)
        self.assertNotIn('"error"', text)

    def test_failed_task_does_not_leak_result_dict(self) -> None:
        """failed task 不泄露 poll 原始 result dict。"""
        marker = "AGNES_VIDEO_FAILED_TASK_SHOULD_NOT_LEAK"
        provider = self._provider()
        provider.submit_task = AsyncMock(return_value={"task_id": "failed-task-001"})  # type: ignore[method-assign]
        provider.poll_task = AsyncMock(return_value={"status": "failed", "error": marker})  # type: ignore[method-assign]

        with self.assertRaises(ProviderTaskFailed) as caught:
            await_compat(provider.generate_video(self._make_request(wait_for_completion=True)))

        text = str(caught.exception)
        self.assertIn("task failed", text)
        self.assertIn("failed", text)
        self.assertEqual(caught.exception.error_category, "task_failed")
        self.assertNotIn(marker, text)
        self.assertNotIn("'error'", text)
        self.assertNotIn('"error"', text)

    def test_generate_video_poll_timeout_is_structured(self) -> None:
        provider = AgnesVideoProvider(
            api_key="av-test-secret-token-123456",
            base_url="https://agnes.example.test/v1",
            max_poll_time=0,
            poll_interval=0,
        )
        provider.submit_task = AsyncMock(return_value={"task_id": "timeout-task-001"})  # type: ignore[method-assign]

        with self.assertRaises(ProviderTimeout) as caught:
            await_compat(provider.generate_video(self._make_request(wait_for_completion=True)))

        self.assertEqual(caught.exception.error_category, "timeout")
        self.assertNotIn("av-test-secret-token", str(caught.exception))

    def test_agnes_video_error_legacy_name_is_structured_provider_error(self) -> None:
        self.assertIsInstance(AgnesVideoError("safe legacy message"), BackendUnavailable)

    def test_agnes_video_errors_do_not_reference_raw_response_text(self) -> None:
        """静态防线：Agnes video 生产代码不得引用 raw response body/dict。"""
        source = (ROOT / "scripts" / "angemedia_gateway" / "adapters" / "agnes_video.py").read_text(encoding="utf-8")
        forbidden = [
            "httpx.AsyncClient",
            "resp.json",
            "response.json",
            "resp.text",
            "response.text",
            ".text[:300]",
            ".text[:200]",
            "str(exc)",
            ": {data}",
            ": {task}",
            ": {result}",
            ": {submit}",
        ]
        for pattern in forbidden:
            self.assertNotIn(pattern, source)


# ── 15. GET /v1/videos/{task_id} 未修改 ───────────────

class VideoJobGetVideoUnchangedTest(_VideoJobTestBase):
    """GET /v1/videos/{task_id} 行为不变。"""

    def test_get_video_not_modified(self) -> None:
        """get_video 方法未被修改，仍正常工作。"""
        mock_poll = AsyncMock(return_value={
            "task_id": "existing-task",
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("existing-task"))
        self.assertEqual(result["task_id"], "existing-task")
        self.assertEqual(result["status"], "completed")


# ── 16-17. wait_for_completion=true 不创建 job ────────

class VideoJobSyncPathTest(_VideoJobTestBase):
    """wait_for_completion=true 路径不创建 job。"""

    def test_sync_path_no_job_created(self) -> None:
        """wait_for_completion=true 时不创建 job。"""
        req = self._make_request(wait_for_completion=True)
        mock_generate = AsyncMock(return_value={
            "task_id": "sync-task-001",
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.generate_video = mock_generate
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                await_compat(self.service.create_video(req))
        self.assertEqual(self._count_jobs(), 0)

    def test_sync_path_response_no_job_id(self) -> None:
        """wait_for_completion=true response 不包含 job_id。"""
        req = self._make_request(wait_for_completion=True)
        mock_generate = AsyncMock(return_value={
            "task_id": "sync-task-002",
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.generate_video = mock_generate
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                result = await_compat(self.service.create_video(req))
        self.assertNotIn("job_id", result)
        self.assertIn("task_id", result)


# ── 18-28. poll 路径更新 job ───────────────────────────

class VideoJobPollTest(_VideoJobTestBase):
    """GET /v1/videos/{task_id} poll 更新 job。"""

    def _create_video_job(self, external_task_id: str = "poll-task-001") -> str:
        """辅助：创建一个 running video job 并返回 job_id。"""
        from angemedia_gateway.state import create_job
        job = create_job(
            kind="video", status="running", provider="agnes_video",
            model="agnes-video-v2.0", prompt="test",
            external_task_id=external_task_id,
        )
        return job["id"]

    def test_poll_completed_updates_job_to_succeeded(self) -> None:
        """poll completed 后 job status 变为 succeeded。"""
        job_id = self._create_video_job("poll-task-001")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-001",
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-task-001"))
        from angemedia_gateway.state import get_job
        job = get_job(job_id)
        self.assertEqual(job["status"], "succeeded")

    def test_poll_completed_response_includes_job_id(self) -> None:
        """completed 后 response 包含 job_id。"""
        self._create_video_job("poll-task-002")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-002",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("poll-task-002"))
        self.assertIn("job_id", result)

    def test_succeeded_job_output_json_has_summary(self) -> None:
        """succeeded job 的 output_json 包含 task_id/status/has_video_url 摘要。"""
        job_id = self._create_video_job("poll-task-003")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-003",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-task-003"))
        from angemedia_gateway.state import get_job
        job = get_job(job_id)
        output = json.loads(job["output_json"])
        self.assertEqual(output["task_id"], "poll-task-003")
        self.assertEqual(output["status"], "completed")
        self.assertTrue(output["has_video_url"])
        self.assertNotIn("video_url", output)

    def test_output_json_no_secret(self) -> None:
        """output_json 不包含 secret / Authorization / API key。"""
        job_id = self._create_video_job("poll-task-004")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-004",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-task-004"))
        from angemedia_gateway.state import get_job
        job = get_job(job_id)
        output_str = job["output_json"]
        self.assertNotIn("Bearer", output_str)
        self.assertNotIn("AGNES_API_KEY", output_str)

    def test_poll_failed_updates_job_to_failed(self) -> None:
        """poll failed 后 job status 变为 failed。"""
        job_id = self._create_video_job("poll-task-005")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-005",
            "status": "failed",
            "error": "generation failed",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-task-005"))
        from angemedia_gateway.state import get_job
        job = get_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_code"], "video_generation_failed")

    def test_failed_job_error_message_redacted(self) -> None:
        """failed job 的 error_message 已脱敏。"""
        job_id = self._create_video_job("poll-task-006")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-006",
            "status": "error",
            "error": "Bearer am-secret-token rejected",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-task-006"))
        from angemedia_gateway.state import get_job
        job = get_job(job_id)
        self.assertNotIn("am-secret-token", job["error_message"])

    def test_poll_running_keeps_job_running(self) -> None:
        """poll running/submitted 时 job 保持 running。"""
        job_id = self._create_video_job("poll-task-007")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-007",
            "status": "running",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-task-007"))
        from angemedia_gateway.state import get_job
        job = get_job(job_id)
        self.assertEqual(job["status"], "running")

    def test_no_matching_job_poll_unchanged(self) -> None:
        """找不到对应 job 时，poll 行为保持原样，response 不包含 job_id。"""
        mock_poll = AsyncMock(return_value={
            "task_id": "no-match-task",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("no-match-task"))
        self.assertNotIn("job_id", result)
        self.assertIn("task_id", result)

    def test_job_update_failure_does_not_block_poll(self) -> None:
        """update_job_status 抛异常时，poll response 不被阻断。"""
        self._create_video_job("poll-task-008")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-008",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.update_job_status", side_effect=RuntimeError("DB failure")):
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("poll-task-008"))
        # response 仍然正常，包含 job_id（因为 job 找到了，只是更新失败）
        self.assertIn("job_id", result)
        self.assertIn("task_id", result)

    def test_video_tasks_upsert_still_works(self) -> None:
        """existing video_tasks upsert 行为仍正常。"""
        mock_poll = AsyncMock(return_value={
            "task_id": "vt-poll-001",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("vt-poll-001"))
        conn = self._conn()
        try:
            row = conn.execute("SELECT status FROM video_tasks WHERE task_id = 'vt-poll-001'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "completed")
        finally:
            conn.close()

    def test_poll_response_preserves原有字段(self) -> None:
        """GET /v1/videos/{task_id} 原有字段仍保留。"""
        self._create_video_job("poll-task-010")
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-task-010",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
            "some_extra": "value",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("poll-task-010"))
        self.assertEqual(result["task_id"], "poll-task-010")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["video_url"], "http://example.com/v.mp4")
        self.assertEqual(result["some_extra"], "value")


# ── 29-32. kind 限定匹配 ──────────────────────────────

class VideoJobKindFilterTest(_VideoJobTestBase):
    """poll 只匹配 kind=video 的 job，不误匹配 image job。"""

    def _create_job(self, kind: str, external_task_id: str) -> str:
        from angemedia_gateway.state import create_job
        job = create_job(kind=kind, status="running", external_task_id=external_task_id, prompt="test")
        return job["id"]

    def test_image_job_with_same_task_id_not_matched(self) -> None:
        """存在 kind=image 且 external_task_id 相同的 job 时，video poll 不匹配它。"""
        image_job_id = self._create_job("image", "shared-task-001")
        mock_poll = AsyncMock(return_value={
            "task_id": "shared-task-001",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("shared-task-001"))
        # image job 不应被匹配
        from angemedia_gateway.state import get_job
        image_job = get_job(image_job_id)
        self.assertEqual(image_job["status"], "running")
        # response 不应包含 image job 的 id
        self.assertNotIn("job_id", result)

    def test_both_jobs_match_video_job(self) -> None:
        """同时存在 image 和 video job 时，匹配 video job。"""
        image_job_id = self._create_job("image", "dual-task-001")
        video_job_id = self._create_job("video", "dual-task-001")
        mock_poll = AsyncMock(return_value={
            "task_id": "dual-task-001",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("dual-task-001"))
        from angemedia_gateway.state import get_job
        # video job 更新为 succeeded
        video_job = get_job(video_job_id)
        self.assertEqual(video_job["status"], "succeeded")
        # image job 保持 running
        image_job = get_job(image_job_id)
        self.assertEqual(image_job["status"], "running")
        # response 返回 video job 的 id
        self.assertEqual(result["job_id"], video_job_id)

    def test_poll_completed_only_updates_video_job(self) -> None:
        """poll completed 后，只更新 video job，不更新 image job。"""
        image_job_id = self._create_job("image", "only-video-001")
        video_job_id = self._create_job("video", "only-video-001")
        mock_poll = AsyncMock(return_value={
            "task_id": "only-video-001",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("only-video-001"))
        from angemedia_gateway.state import get_job
        self.assertEqual(get_job(video_job_id)["status"], "succeeded")
        self.assertEqual(get_job(image_job_id)["status"], "running")

    def test_response_job_id_is_video_job(self) -> None:
        """response 返回的 job_id 应该是 video job 的 id。"""
        self._create_job("image", "verify-video-id-001")
        video_job_id = self._create_job("video", "verify-video-id-001")
        mock_poll = AsyncMock(return_value={
            "task_id": "verify-video-id-001",
            "status": "completed",
            "video_url": "http://example.com/v.mp4",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("verify-video-id-001"))
        self.assertEqual(result["job_id"], video_job_id)


# ── 33-42. poll completed 写入 video asset ────────────

class VideoPollAssetJobIdTest(_VideoJobTestBase):
    """GET /v1/videos/{task_id} poll completed 写入 asset + job_id。"""

    def _create_video_job(self, external_task_id: str = "poll-asset-001", **kwargs) -> str:
        from angemedia_gateway.state import create_job
        defaults = dict(kind="video", status="running", provider="agnes_video",
                        model="agnes-video-v2.0", prompt="test prompt")
        defaults.update(kwargs)
        defaults["external_task_id"] = external_task_id
        job = create_job(**defaults)
        return job["id"]

    def _make_completed_result(self, task_id: str, local_path: str | None = None) -> dict:
        result = {
            "task_id": task_id,
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
            "prompt": "test prompt",
            "model": "agnes-video-v2.0",
            "provider": "agnes_video",
            "duration_ms": 5000,
        }
        if local_path:
            result["local_path"] = local_path
        return result

    def _get_assets_by_job_id(self, job_id: str) -> list[dict]:
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM assets WHERE job_id = ?", (job_id,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _count_assets(self) -> int:
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        finally:
            conn.close()

    def _make_video_file(self, name: str) -> Path:
        f = self._output_dir / name
        f.write_bytes(b"\x00\x00\x00\x00")
        return f

    def test_poll_completed_writes_asset(self) -> None:
        """poll completed 后写入 video asset。"""
        self._create_video_job("poll-asset-001")
        fake_file = self._make_video_file("video-poll.mp4")
        mock_result = self._make_completed_result("poll-asset-001", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-asset-001"))
        self.assertGreaterEqual(self._count_assets(), 1)

    def test_poll_completed_asset_job_id_matches_job(self) -> None:
        """poll completed 后 asset.job_id 等于 video job id。"""
        job_id = self._create_video_job("poll-asset-002")
        fake_file = self._make_video_file("video-poll-2.mp4")
        mock_result = self._make_completed_result("poll-asset-002", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-asset-002"))
        assets = self._get_assets_by_job_id(job_id)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["job_id"], job_id)

    def test_poll_completed_no_job_asset_job_id_null(self) -> None:
        """poll completed 找不到 job 时，asset 仍保存且 job_id=NULL。"""
        fake_file = self._make_video_file("video-no-job.mp4")
        mock_result = self._make_completed_result("no-job-task", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("no-job-task"))
        self.assertGreaterEqual(self._count_assets(), 1)
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT job_id FROM assets WHERE url_path LIKE '%video-no-job%'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row[0])
        finally:
            conn.close()

    def test_poll_running_no_asset(self) -> None:
        """poll running/submitted 时不写 asset。"""
        self._create_video_job("poll-running-001")
        count_before = self._count_assets()
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-running-001",
            "status": "running",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-running-001"))
        self.assertEqual(self._count_assets(), count_before)

    def test_poll_failed_no_asset(self) -> None:
        """poll failed/error 时不写 asset。"""
        self._create_video_job("poll-failed-001")
        count_before = self._count_assets()
        mock_poll = AsyncMock(return_value={
            "task_id": "poll-failed-001",
            "status": "failed",
            "error": "generation failed",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-failed-001"))
        self.assertEqual(self._count_assets(), count_before)

    def test_repeated_poll_completed_no_duplicate(self) -> None:
        """repeated poll completed 不产生重复 asset。"""
        self._create_video_job("poll-repeated-001")
        fake_file = self._make_video_file("video-repeated.mp4")
        mock_result = self._make_completed_result("poll-repeated-001", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-repeated-001"))
            count_after_first = self._count_assets()
            await_compat(self.service.get_video("poll-repeated-001"))
            count_after_second = self._count_assets()
            self.assertEqual(count_after_first, count_after_second)

    def test_existing_asset_job_id_not_overwritten(self) -> None:
        """已有 asset.job_id=old 时，poll completed 不覆盖 old。"""
        from angemedia_gateway.state import save_asset
        save_asset(
            id="old-asset", filename="existing.mp4", storage_area="output",
            relative_path="existing-video.mp4", url_path="/generated/existing-video.mp4",
            media_type="video", source="generated", size=100,
            job_id="old-job-id",
        )
        fake_file = self._make_video_file("existing-video.mp4")
        self._create_video_job("existing-asset-001")
        mock_result = self._make_completed_result("existing-asset-001", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("existing-asset-001"))
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT job_id FROM assets WHERE relative_path = 'existing-video.mp4'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "old-job-id")
        finally:
            conn.close()

    def test_asset_save_failure_does_not_block_poll(self) -> None:
        """_save_generated_asset 抛异常时，poll response 仍正常。"""
        self._create_video_job("poll-save-fail-001")
        fake_file = self._make_video_file("fail-video.mp4")
        mock_result = self._make_completed_result("poll-save-fail-001", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize), \
             patch("angemedia_gateway.services.media_service._save_generated_asset",
                    side_effect=RuntimeError("asset write failed")):
            mock_av.poll_task = mock_poll
            result = await_compat(self.service.get_video("poll-save-fail-001"))
        self.assertIn("task_id", result)
        self.assertEqual(result["task_id"], "poll-save-fail-001")

    def test_poll_completed_asset_url_path_uses_localized(self) -> None:
        """_save_generated_asset 收到的 result 包含 localize 后的 local_path。"""
        self._create_video_job("poll-url-001")
        fake_file = self._make_video_file("localized-video.mp4")
        mock_result = self._make_completed_result("poll-url-001", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        mock_save = MagicMock()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize), \
             patch("angemedia_gateway.services.media_service._save_generated_asset", mock_save):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-url-001"))
        mock_save.assert_called_once()
        call_result = mock_save.call_args.kwargs.get("result")
        self.assertIsNotNone(call_result)
        self.assertIn("local_path", call_result)
        self.assertIn("localized-video.mp4", call_result["local_path"])

    def test_null_job_id_filled_by_poll(self) -> None:
        """已有 asset.job_id=NULL 时，poll completed 补写 job_id（真实 DB 集成测试）。"""
        from angemedia_gateway.state import save_asset
        # 1. 创建 job_id=NULL 的 asset
        save_asset(
            id="null-asset", filename="fill.mp4", storage_area="output",
            relative_path="fill-video.mp4", url_path="/generated/fill-video.mp4",
            media_type="video", source="generated", size=100,
        )
        # 2. 创建同名真实文件
        fake_file = self._make_video_file("fill-video.mp4")
        # 3. 创建 video job
        job_id = self._create_video_job("fill-job-001")
        # 4. mock poll_task + localize_video_result
        mock_result = self._make_completed_result("fill-job-001", local_path=str(fake_file))
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize):
            mock_av.poll_task = mock_poll
            # 5. 调用 get_video
            await_compat(self.service.get_video("fill-job-001"))
        # 6. 直接查询 DB
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            # 只有 1 条记录
            count = conn.execute(
                "SELECT COUNT(*) FROM assets WHERE relative_path = 'fill-video.mp4'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
            # job_id 已被补写
            row = conn.execute(
                "SELECT job_id FROM assets WHERE relative_path = 'fill-video.mp4'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], job_id)
        finally:
            conn.close()

    def test_metadata_fallback_from_job(self) -> None:
        """poll result 无 prompt/model/provider 时，从 job 获取 metadata。"""
        job_id = self._create_video_job(
            "poll-meta-001", prompt="from-job-prompt", model="from-job-model", provider="from-job-provider",
        )
        fake_file = self._make_video_file("meta-video.mp4")
        mock_result = {
            "task_id": "poll-meta-001",
            "status": "completed",
            "video_url": "http://example.com/video.mp4",
            "local_path": str(fake_file),
            "duration_ms": 3000,
        }
        mock_poll = AsyncMock(return_value=mock_result)
        mock_localize = AsyncMock(return_value=mock_result)
        mock_save = MagicMock()
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av, \
             patch("angemedia_gateway.services.media_service.localize_video_result", mock_localize), \
             patch("angemedia_gateway.services.media_service._save_generated_asset", mock_save):
            mock_av.poll_task = mock_poll
            await_compat(self.service.get_video("poll-meta-001"))
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        self.assertEqual(call_kwargs.kwargs.get("prompt"), "from-job-prompt")
        self.assertEqual(call_kwargs.kwargs.get("model"), "from-job-model")
        self.assertEqual(call_kwargs.kwargs.get("provider"), "from-job-provider")

    def test_submit_path_still_no_asset(self) -> None:
        """submit path 仍不产生 asset。"""
        count_before = self._count_assets()
        mock_submit = AsyncMock(return_value={
            "task_id": "submit-check-001",
            "status": "queued",
        })
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = mock_submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                await_compat(self.service.create_video(self._make_request()))
        self.assertEqual(self._count_assets(), count_before)


def await_compat(coro):
    """兼容同步测试调用异步方法。"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

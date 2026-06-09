"""Dedupe/admission red-contract tests for generation jobs."""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")

from fastapi.testclient import TestClient  # noqa: E402

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.request_hash import compute_request_hash  # noqa: E402
from angemedia_gateway.request_hash_builders import (  # noqa: E402
    build_image_request_hash_payload,
    build_video_request_hash_payload,
)
from angemedia_gateway.routing import RouteTarget  # noqa: E402
from angemedia_gateway.schemas import ImageRequest, VideoRequest  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402
from angemedia_gateway.state import (  # noqa: E402
    create_job,
    ensure_default_admin_user,
    init_db,
    list_jobs,
)


REQUEST_HASH_VERSION = 1
IMAGE_CHAIN = [RouteTarget(provider="siliconflow", model="kolors")]
IMAGE_SUCCESS = {
    "created": 1717500000,
    "data": [{"url": "http://testserver/generated/dedupe.png", "revised_prompt": "dedupe cat"}],
}


class CountingImageProvider:
    """Fake image provider that can fail when admission does not stop calls."""

    def __init__(self, *, fail_on_call: bool = False) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    async def generate(self, req: ImageRequest, target: object) -> dict:
        self.calls += 1
        if self.fail_on_call:
            raise AssertionError("provider should not be called for duplicate admission")
        return copy.deepcopy(IMAGE_SUCCESS)


class _DedupeAdmissionApiTestBase(unittest.TestCase):
    """Shared API test setup."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="dedupe-admission-test-")
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
        self.login_admin()

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

    def _image_hash(self, payload: dict) -> str | None:
        result = build_image_request_hash_payload(
            ImageRequest(**payload),
            provider_mode="builtin",
            resolved_chain=IMAGE_CHAIN,
        )
        if result.payload is None:
            return None
        return compute_request_hash(result.payload, version=REQUEST_HASH_VERSION)

    def _video_hash(self, payload: dict) -> str | None:
        result = build_video_request_hash_payload(VideoRequest(**payload), provider="agnes_video")
        if result.payload is None:
            return None
        return compute_request_hash(result.payload, version=REQUEST_HASH_VERSION)

    def _assert_duplicate_response_safe(self, resp, *, existing_job: dict, expected_kind: str) -> None:
        self.assertEqual(resp.status_code, 409, resp.text)
        body = resp.json()
        rendered = repr(body)
        for forbidden in (
            "request_hash",
            "request_hash_version",
            "prompt",
            "input_json",
            "output_json",
            "raw",
            "api_key",
            "base_url",
            "status_url",
            "quota_url",
        ):
            self.assertNotIn(forbidden, rendered)
        detail = body.get("detail")
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("code"), "duplicate_in_flight_job")
        summary = detail.get("existing_job")
        self.assertIsInstance(summary, dict)
        self.assertEqual(set(summary), {"job_id", "kind", "status", "created_at"})
        self.assertEqual(summary["job_id"], existing_job["id"])
        self.assertEqual(summary["kind"], expected_kind)
        self.assertEqual(summary["status"], existing_job["status"])
        self.assertEqual(summary["created_at"], existing_job["created_at"])

    def _count_jobs(self) -> int:
        return len(list_jobs(limit=100))


class ImageDedupeAdmissionApiTest(_DedupeAdmissionApiTestBase):
    """Image in-flight duplicate admission contract."""

    def _post_image(self, payload: dict, provider: CountingImageProvider):
        with patch("angemedia_gateway.services.media_service.resolve_chain", return_value=IMAGE_CHAIN), \
             patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
            return self.client.post("/v1/images/generations", json=payload)

    def _existing_image_job(self, *, status: str, payload: dict, request_hash: str | None | object = ...):
        if request_hash is ...:
            request_hash = self._image_hash(payload)
        return create_job(
            kind="image",
            status=status,
            prompt="existing prompt must not leak",
            request_hash=request_hash,
            request_hash_version=REQUEST_HASH_VERSION if request_hash else None,
        )

    def test_running_image_duplicate_returns_409_without_provider_call_or_new_job(self) -> None:
        payload = {"prompt": "duplicate image cat", "model": "agnes-image", "size": "1024x1024", "seed": 77}
        existing = self._existing_image_job(status="running", payload=payload)
        provider = CountingImageProvider(fail_on_call=True)

        resp = self._post_image(payload, provider)

        self._assert_duplicate_response_safe(resp, existing_job=existing, expected_kind="image")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self._count_jobs(), 1)

    def test_queued_image_duplicate_returns_409_without_provider_call_or_new_job(self) -> None:
        payload = {"prompt": "queued duplicate image cat", "model": "agnes-image", "size": "1024x1024", "seed": 78}
        existing = self._existing_image_job(status="queued", payload=payload)
        provider = CountingImageProvider(fail_on_call=True)

        resp = self._post_image(payload, provider)

        self._assert_duplicate_response_safe(resp, existing_job=existing, expected_kind="image")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self._count_jobs(), 1)

    def test_terminal_image_jobs_do_not_block_new_generation(self) -> None:
        for status in ("succeeded", "failed", "canceled"):
            with self.subTest(status=status):
                payload = {"prompt": f"terminal image {status}", "model": "agnes-image", "seed": 79}
                self._existing_image_job(status=status, payload=payload)
                provider = CountingImageProvider()

                resp = self._post_image(payload, provider)

                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(provider.calls, 1)
                self.assertIn("job_id", resp.json())

    def test_null_hash_image_request_fails_open(self) -> None:
        payload = {
            "prompt": "unsupported image reference",
            "model": "agnes-image",
            "image": "https://example.com/ref.png?token=secret",
        }
        self._existing_image_job(status="running", payload=payload, request_hash=None)
        provider = CountingImageProvider()

        resp = self._post_image(payload, provider)

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self._count_jobs(), 2)


class VideoDedupeAdmissionApiTest(_DedupeAdmissionApiTestBase):
    """Video async submit best-effort duplicate admission contract."""

    def _post_video(self, payload: dict, *, submit_result: dict | None = None, fail_on_submit: bool = False):
        submit = AsyncMock()
        if fail_on_submit:
            submit.side_effect = AssertionError("submit_task should not be called for duplicate admission")
        else:
            submit.return_value = submit_result or {
                "task_id": "dedupe-video-task",
                "status": "queued",
                "provider": "agnes_video",
            }
        with patch("angemedia_gateway.services.media_service.agnes_video") as mock_av:
            mock_av.submit_task = submit
            with patch("angemedia_gateway.services.media_service.builtin_provider_enabled", return_value=True):
                resp = self.client.post("/v1/videos", json=payload)
        return resp, submit

    def _existing_video_job(self, *, status: str, payload: dict, request_hash: str | None | object = ...):
        if request_hash is ...:
            request_hash = self._video_hash(payload)
        return create_job(
            kind="video",
            status=status,
            provider="agnes_video",
            model=payload.get("model", "agnes-video-v2.0"),
            prompt="existing video prompt must not leak",
            external_task_id=f"existing-{status}-task",
            request_hash=request_hash,
            request_hash_version=REQUEST_HASH_VERSION if request_hash else None,
        )

    def test_running_video_duplicate_returns_409_without_submit_or_new_job(self) -> None:
        payload = {"prompt": "duplicate video cat", "model": "agnes-video-v2.0", "image": "/uploads/ref-a.png", "seed": 12}
        existing = self._existing_video_job(status="running", payload=payload)

        resp, submit = self._post_video(payload, fail_on_submit=True)

        self._assert_duplicate_response_safe(resp, existing_job=existing, expected_kind="video")
        self.assertEqual(submit.await_count, 0)
        self.assertEqual(self._count_jobs(), 1)

    def test_terminal_video_jobs_do_not_block_new_async_submit(self) -> None:
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                payload = {"prompt": f"terminal video {status}", "model": "agnes-video-v2.0", "seed": 13}
                self._existing_video_job(status=status, payload=payload)

                resp, submit = self._post_video(payload, submit_result={
                    "task_id": f"new-{status}-task",
                    "status": "queued",
                    "provider": "agnes_video",
                })

                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(submit.await_count, 1)
                self.assertIn("job_id", resp.json())

    def test_null_hash_video_request_fails_open(self) -> None:
        payload = {
            "prompt": "unsupported video extra body",
            "model": "agnes-video-v2.0",
            "extra_body": {"motion": "pan"},
        }
        self._existing_video_job(status="running", payload=payload, request_hash=None)

        resp, submit = self._post_video(payload, submit_result={
            "task_id": "unsupported-extra-body-task",
            "status": "queued",
            "provider": "agnes_video",
        })

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(self._count_jobs(), 2)

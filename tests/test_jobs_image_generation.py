"""图片生成接入 jobs 表的测试。"""
from __future__ import annotations

import base64
import copy
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("SILICONFLOW_API_KEY", "sf-test-secret-value")

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.schemas import ImageRequest  # noqa: E402
from angemedia_gateway.services.media_service import (  # noqa: E402
    MediaService,
    ImageProvidersFailed,
    NoImageProviderAvailable,
)
from angemedia_gateway.state import (  # noqa: E402
    init_db,
    list_jobs,
    get_job,
    upsert_custom_provider,
)


class FakeImageProvider:
    """模拟成功的图片 provider。"""

    def __init__(self, result: dict) -> None:
        self.result = result

    async def generate(self, req: ImageRequest, target: object) -> dict:
        return copy.deepcopy(self.result)


class RecordingImageProvider(FakeImageProvider):
    """记录调用的成功图片 provider。"""

    def __init__(self, result: dict) -> None:
        super().__init__(result)
        self.calls: list[tuple[ImageRequest, object]] = []

    async def generate(self, req: ImageRequest, target: object) -> dict:
        self.calls.append((req, target))
        return await super().generate(req, target)


class FakeFailingProvider:
    """模拟失败的图片 provider。"""

    async def generate(self, req: ImageRequest, target: object) -> dict:
        raise RuntimeError("simulated provider failure")


class _ImageJobTestBase(unittest.TestCase):
    """共享 setUp/tearDown。"""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp(prefix="image-job-test-")
        self._db_path = Path(self._tmp_dir) / "test.db"
        self._output_dir = Path(self._tmp_dir) / "output"
        self._upload_dir = Path(self._tmp_dir) / "upload"
        self._output_dir.mkdir()
        self._upload_dir.mkdir()

        self._orig_db = C.DB_FILE
        self._orig_output = C.OUTPUT_DIR
        self._orig_upload = C.UPLOAD_DIR
        self._orig_base_url = C.PUBLIC_BASE_URL

        C.DB_FILE = self._db_path
        C.OUTPUT_DIR = self._output_dir
        C.UPLOAD_DIR = self._upload_dir
        C.PUBLIC_BASE_URL = "http://testserver"
        init_db()
        self.service = MediaService()

    def tearDown(self) -> None:
        C.DB_FILE = self._orig_db
        C.OUTPUT_DIR = self._orig_output
        C.UPLOAD_DIR = self._orig_upload
        C.PUBLIC_BASE_URL = self._orig_base_url
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

    def _get_job_by_id(self, job_id: str) -> dict | None:
        job = get_job(job_id)
        return job

    def _make_request(self, **overrides) -> ImageRequest:
        defaults = {"prompt": "a cat", "size": "1024x1024", "response_format": "url"}
        defaults.update(overrides)
        return ImageRequest(**defaults)


SUCCESS_RESULT = {
    "created": 1717500000,
    "data": [{"url": "http://testserver/generated/test.png", "revised_prompt": "a cat"}],
}


# ── 1-7. 成功路径 ──────────────────────────────────────

class ImageJobSuccessTest(_ImageJobTestBase):
    """图片生成成功后 job 行为。"""

    def test_success_creates_job(self) -> None:
        """图片生成成功后创建 job。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        self.assertEqual(self._count_jobs(), 1)
        self.assertIn("job_id", result)

    def test_success_job_kind_is_image(self) -> None:
        """成功 job kind == 'image'。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        job = self._get_job_by_id(result["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["kind"], "image")

    def test_success_job_status_is_succeeded(self) -> None:
        """成功后 job status == 'succeeded'。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        job = self._get_job_by_id(result["job_id"])
        self.assertEqual(job["status"], "succeeded")

    def test_success_job_records_prompt_model_provider(self) -> None:
        """成功 job 记录 prompt/model/provider。"""
        req = self._make_request(prompt="a dog")
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        job = self._get_job_by_id(result["job_id"])
        self.assertEqual(job["prompt"], "a dog")
        self.assertEqual(job["provider"], "siliconflow")
        self.assertEqual(job["model"], "kolors")

    def test_success_job_records_duration_ms(self) -> None:
        """成功 job 记录 duration_ms。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        job = self._get_job_by_id(result["job_id"])
        self.assertIsNotNone(job["duration_ms"])
        self.assertGreaterEqual(job["duration_ms"], 0)

    def test_response_preserves_data_structure(self) -> None:
        """response 保留原有 data 结构。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        self.assertIn("data", result)
        self.assertEqual(len(result["data"]), 1)
        self.assertIn("url", result["data"][0])

    def test_response_includes_job_id(self) -> None:
        """response 顶层包含 job_id。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        self.assertIn("job_id", result)
        self.assertIsInstance(result["job_id"], str)
        self.assertTrue(len(result["job_id"]) > 0)


# ── request_hash populate 契约 ────────────────────────

class ImageJobRequestHashPopulateTest(_ImageJobTestBase):
    """image 主流程应在创建 job 时写入 request_hash。"""

    def _run_builtin(self, req: ImageRequest) -> dict:
        from angemedia_gateway.routing import RouteTarget

        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [RouteTarget(provider="siliconflow", model="kolors")]
            return await_compat(self.service.create_image(req))

    def _job_hash(self, job_id: str) -> tuple[str | None, int | None]:
        job = self._get_job_by_id(job_id)
        self.assertIsNotNone(job)
        return job["request_hash"], job["request_hash_version"]

    def test_builtin_image_job_writes_request_hash_and_version(self) -> None:
        """built-in image 成功 job 应写入 request_hash / request_hash_version。"""
        result = self._run_builtin(self._make_request(prompt="hash cat"))

        request_hash, request_hash_version = self._job_hash(result["job_id"])
        self.assertIsNotNone(request_hash)
        self.assertEqual(len(request_hash), 64)
        self.assertEqual(request_hash_version, 1)

    def test_same_builtin_image_request_writes_same_hash_without_dedupe(self) -> None:
        """相同 image 请求应写入相同 hash，但仍创建两个 job。"""
        req_a = self._make_request(prompt="same hash cat", model="agnes-image", size="1024x1024", seed=7)
        req_b = self._make_request(prompt="same hash cat", model="agnes-image", size="1024x1024", seed=7)

        first = self._run_builtin(req_a)
        second = self._run_builtin(req_b)

        first_hash, first_version = self._job_hash(first["job_id"])
        second_hash, second_version = self._job_hash(second["job_id"])
        self.assertIsNotNone(first_hash)
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first_version, 1)
        self.assertEqual(second_version, 1)
        self.assertEqual(self._count_jobs(), 2)

    def test_different_builtin_image_request_writes_different_hash(self) -> None:
        """不同 image 请求应写入不同 request_hash。"""
        first = self._run_builtin(self._make_request(prompt="hash cat", model="agnes-image", seed=7))
        second = self._run_builtin(self._make_request(prompt="hash dog", model="agnes-image", seed=7))

        first_hash, _ = self._job_hash(first["job_id"])
        second_hash, _ = self._job_hash(second["job_id"])
        self.assertIsNotNone(first_hash)
        self.assertIsNotNone(second_hash)
        self.assertNotEqual(first_hash, second_hash)

    def test_custom_provider_hash_ignores_secret_and_url_config(self) -> None:
        """custom image hash 应只依赖安全 identity，不依赖 api_key/base_url/status_url/quota_url。"""
        async def fake_custom_image(req, provider):
            return copy.deepcopy(SUCCESS_RESULT)

        provider_id = "hash-custom-provider"
        upsert_custom_provider({
            "id": provider_id,
            "name": "Hash Provider",
            "provider_type": "openai_image",
            "base_url": "https://first.example.invalid/v1",
            "api_key": "sk-first-secret",
            "default_model": "custom-image-model",
            "status_url": "https://first.example.invalid/status",
            "quota_url": "https://first.example.invalid/quota",
            "enabled": True,
        })
        with patch("angemedia_gateway.services.media_service.generate_custom_openai_image", fake_custom_image):
            first = await_compat(self.service.create_image(
                self._make_request(prompt="custom hash cat", model=f"custom:{provider_id}")
            ))

        upsert_custom_provider({
            "id": provider_id,
            "name": "Hash Provider",
            "provider_type": "openai_image",
            "base_url": "https://second.example.invalid/v1",
            "api_key": "sk-second-secret",
            "default_model": "custom-image-model",
            "status_url": "https://second.example.invalid/status",
            "quota_url": "https://second.example.invalid/quota",
            "enabled": True,
        })
        with patch("angemedia_gateway.services.media_service.generate_custom_openai_image", fake_custom_image):
            second = await_compat(self.service.create_image(
                self._make_request(prompt="custom hash cat", model=f"custom:{provider_id}")
            ))

        first_hash, first_version = self._job_hash(first["job_id"])
        second_hash, second_version = self._job_hash(second["job_id"])
        self.assertIsNotNone(first_hash)
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first_version, 1)
        self.assertEqual(second_version, 1)

    def test_unsupported_image_reference_creates_job_with_null_hash(self) -> None:
        """unsupported reference identity 时应 fail-open，job 正常创建但 hash/version 为 NULL。"""
        result = self._run_builtin(self._make_request(image="https://example.com/ref.png?token=secret"))

        request_hash, request_hash_version = self._job_hash(result["job_id"])
        self.assertIsNone(request_hash)
        self.assertIsNone(request_hash_version)

    def test_secret_like_image_extra_fails_before_provider_call(self) -> None:
        """secret-like extra field 应 fail-fast，且不调用 provider、不创建 job。"""
        provider = FakeImageProvider(SUCCESS_RESULT)
        req = self._make_request(providerToken="sk-should-not-hash")
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            with self.assertRaises(ValueError):
                await_compat(self.service.create_image(req))

        self.assertEqual(self._count_jobs(), 0)


class ImageOperationValidationTest(_ImageJobTestBase):
    """Catalog operation validation must be wired into the real image path."""

    def _kolors_target(self):
        from angemedia_gateway.routing import RouteTarget

        return RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")

    def _modelscope_unknown_target(self):
        from angemedia_gateway.routing import RouteTarget

        return RouteTarget(provider="modelscope", model="not-a-catalog-model")

    def _qwen_target(self):
        from angemedia_gateway.routing import RouteTarget

        return RouteTarget(provider="modelscope", model="Qwen/Qwen-Image-2512")

    def _agnes_target(self, model="agnes-image-2.1-flash"):
        from angemedia_gateway.routing import RouteTarget

        return RouteTarget(provider="agnes_image", model=model)

    def test_valid_kolors_operation_params_pass_to_provider(self) -> None:
        provider = RecordingImageProvider(SUCCESS_RESULT)
        req = self._make_request(
            model="kolors",
            size="960x1280",
            negative_prompt="blur",
            seed=12,
            steps=30,
            guidance=8.5,
        )
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
            mock_chain.return_value = [self._kolors_target()]
            result = await_compat(self.service.create_image(req))

        self.assertEqual(len(provider.calls), 1)
        self.assertIn("job_id", result)

    def test_valid_kolors_image_to_image_reference_passes_to_provider(self) -> None:
        provider = RecordingImageProvider(SUCCESS_RESULT)
        req = self._make_request(
            model="kolors",
            size="1024x1024",
            image="https://example.com/source.png",
            steps=20,
            guidance=7.5,
        )
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
            mock_chain.return_value = [self._kolors_target()]
            result = await_compat(self.service.create_image(req))

        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][0].image, "https://example.com/source.png")
        self.assertIn("job_id", result)

    def test_invalid_kolors_operation_params_are_rejected_before_provider_call(self) -> None:
        cases = [
            {"steps": 200},
            {"guidance": 999},
            {"seed": -1},
            {"size": "1536x1024"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                provider = RecordingImageProvider(SUCCESS_RESULT)
                payload = {"model": "kolors", "size": "1024x1024"}
                payload.update(overrides)
                req = self._make_request(**payload)
                with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
                    patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
                    mock_chain.return_value = [self._kolors_target()]
                    from angemedia_gateway.services.image_generation import InvalidImageRequest

                    with self.assertRaises(InvalidImageRequest):
                        await_compat(self.service.create_image(req))

                self.assertEqual(provider.calls, [])
        self.assertEqual(self._count_jobs(), 0)

    def test_invalid_kolors_image_reference_is_rejected_before_provider_call(self) -> None:
        cases = [
            {"image": "https://example.com/source.png?token=secret"},
            {"image": "D:/tmp/source.png"},
            {"image": "/private/source.png"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                provider = RecordingImageProvider(SUCCESS_RESULT)
                payload = {"model": "kolors", "size": "1024x1024"}
                payload.update(overrides)
                req = self._make_request(**payload)
                with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
                    patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
                    mock_chain.return_value = [self._kolors_target()]
                    from angemedia_gateway.services.image_generation import InvalidImageRequest

                    with self.assertRaises(InvalidImageRequest):
                        await_compat(self.service.create_image(req))

                self.assertEqual(provider.calls, [])
        self.assertEqual(self._count_jobs(), 0)

    def test_invalid_kolors_operation_params_return_safe_400_from_api(self) -> None:
        from fastapi.testclient import TestClient
        from angemedia_gateway.server import app
        from angemedia_gateway.state import create_gateway_api_key

        key_item = create_gateway_api_key(name="kolors-operation-validation")
        client = TestClient(app)
        cases = [
            {"steps": 200},
            {"guidance": 999},
            {"seed": -1},
            {"size": "1536x1024"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                provider = RecordingImageProvider(SUCCESS_RESULT)
                payload = {"prompt": "kolors cat", "model": "kolors", "size": "1024x1024"}
                payload.update(overrides)
                with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
                    patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
                    mock_chain.return_value = [self._kolors_target()]
                    response = client.post(
                        "/v1/images/generations",
                        json=payload,
                        headers={"Authorization": f"Bearer {key_item['key']}"},
                    )

                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(provider.calls, [])
                rendered = response.text.lower()
                for forbidden in ("api_key", "authorization", "bearer", "secret", "token"):
                    self.assertNotIn(forbidden, rendered)
        self.assertEqual(self._count_jobs(), 0)

    def test_invalid_kolors_image_reference_returns_safe_400_from_api(self) -> None:
        from fastapi.testclient import TestClient
        from angemedia_gateway.server import app
        from angemedia_gateway.state import create_gateway_api_key

        key_item = create_gateway_api_key(name="kolors-image-ref-validation")
        client = TestClient(app)
        provider = RecordingImageProvider(SUCCESS_RESULT)
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
            mock_chain.return_value = [self._kolors_target()]
            response = client.post(
                "/v1/images/generations",
                json={
                    "prompt": "kolors cat",
                    "model": "kolors",
                    "size": "1024x1024",
                    "image": "https://example.com/source.png?token=secret",
                },
                headers={"Authorization": f"Bearer {key_item['key']}"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(provider.calls, [])
        rendered = response.text.lower()
        for forbidden in ("api_key", "authorization", "bearer", "secret", "token=secret"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(self._count_jobs(), 0)

    def test_modelscope_rejects_unsupported_size_params_and_image_before_provider_call(self) -> None:
        cases = [
            {"size": "512x512"},
            {"seed": 42},
            {"steps": 20},
            {"guidance": 4.0},
            {"image": "https://example.com/source.png"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                provider = RecordingImageProvider(SUCCESS_RESULT)
                payload = {"model": "qwen", "size": "1024x1024"}
                payload.update(overrides)
                req = self._make_request(**payload)
                with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
                    patch("angemedia_gateway.services.media_service.PROVIDERS", {"modelscope": provider}):
                    mock_chain.return_value = [self._qwen_target()]
                    from angemedia_gateway.services.image_generation import InvalidImageRequest

                    with self.assertRaises(InvalidImageRequest):
                        await_compat(self.service.create_image(req))

                self.assertEqual(provider.calls, [])
        self.assertEqual(self._count_jobs(), 0)

    def test_agnes_verified_custom_sizes_and_reference_sources_pass_to_provider(self) -> None:
        data_url = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nprobe").decode("ascii")
        cases = [
            (
                "agnes-2.1",
                self._agnes_target(),
                "2560x1440",
                {"image": data_url},
            ),
            (
                "agnes-2.1",
                self._agnes_target(),
                "1280x720",
                {"image": "/uploads/source.png"},
            ),
            (
                "agnes-2.0",
                self._agnes_target("agnes-image-2.0-flash"),
                "2048x1536",
                {
                    "images": [
                        "https://example.com/one.png",
                        "https://example.com/two.png",
                        "https://example.com/three.png",
                        "https://example.com/four.png",
                    ],
                    "seed": 42,
                },
            ),
        ]
        for model, target, size, overrides in cases:
            with self.subTest(model=model):
                provider = RecordingImageProvider(SUCCESS_RESULT)
                req = self._make_request(model=model, size=size, **overrides)
                with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
                    patch("angemedia_gateway.services.media_service.PROVIDERS", {"agnes_image": provider}):
                    mock_chain.return_value = [target]
                    result = await_compat(self.service.create_image(req))

                self.assertEqual(len(provider.calls), 1)
                self.assertIn("job_id", result)

    def test_agnes_rejects_unverified_params_out_of_bounds_sizes_and_bad_refs(self) -> None:
        cases = [
            (self._agnes_target(), {"seed": 42}),
            (self._agnes_target(), {"steps": 20}),
            (self._agnes_target(), {"guidance": 4.0}),
            (self._agnes_target("agnes-image-2.0-flash"), {"negative_prompt": "unsupported"}),
            (self._agnes_target(), {"size": "4096x4096"}),
            (self._agnes_target("agnes-image-2.0-flash"), {"size": "2560x1440"}),
            (self._agnes_target(), {"image": "data:image/png;base64,not-valid-base64!"}),
            (
                self._agnes_target("agnes-image-2.0-flash"),
                {"images": [f"https://example.com/{index}.png" for index in range(5)]},
            ),
        ]
        for target, overrides in cases:
            with self.subTest(model=target.model, overrides=overrides):
                provider = RecordingImageProvider(SUCCESS_RESULT)
                payload = {"model": "agnes-2.1", "size": "1024x1024"}
                if target.model == "agnes-image-2.0-flash":
                    payload["model"] = "agnes-2.0"
                payload.update(overrides)
                req = self._make_request(**payload)
                with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
                    patch("angemedia_gateway.services.media_service.PROVIDERS", {"agnes_image": provider}):
                    mock_chain.return_value = [target]
                    from angemedia_gateway.services.image_generation import InvalidImageRequest

                    with self.assertRaises(InvalidImageRequest) as ctx:
                        await_compat(self.service.create_image(req))

                self.assertEqual(provider.calls, [])

    def test_modelscope_unknown_model_is_not_rejected_by_kolors_rules(self) -> None:
        provider = RecordingImageProvider(SUCCESS_RESULT)
        req = self._make_request(
            model="not-a-catalog-model",
            size="1536x1024",
            steps=200,
            guidance=999,
            image="https://example.com/source.png",
        )
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"modelscope": provider}):
            mock_chain.return_value = [self._modelscope_unknown_target()]
            result = await_compat(self.service.create_image(req))

        self.assertEqual(len(provider.calls), 1)
        self.assertIn("job_id", result)

    def test_custom_provider_is_not_rejected_by_kolors_rules(self) -> None:
        provider_id = "operation-custom-provider"
        upsert_custom_provider({
            "id": provider_id,
            "name": "Operation Custom Provider",
            "provider_type": "openai_image",
            "base_url": "https://operation.example.invalid/v1",
            "api_key": "sk-operation-secret",
            "default_model": "custom-image-model",
            "enabled": True,
        })
        seen: dict[str, ImageRequest] = {}

        async def fake_custom_image(req, provider):
            seen["req"] = req
            return copy.deepcopy(SUCCESS_RESULT)

        req = self._make_request(
            model=f"custom:{provider_id}",
            size="1536x1024",
            steps=200,
            guidance=999,
            image="https://example.com/source.png",
        )
        with patch("angemedia_gateway.services.media_service.generate_custom_openai_image", fake_custom_image):
            result = await_compat(self.service.create_image(req))

        self.assertEqual(seen["req"].steps, 200)
        self.assertEqual(seen["req"].guidance, 999)
        self.assertEqual(result["model"], "custom-image-model")


class ImageCustomProviderModelOverrideRedContractTest(_ImageJobTestBase):
    """custom provider per-request provider_model override contracts."""

    def _setup_custom_provider(self, provider_id: str = "override-custom-provider") -> str:
        upsert_custom_provider({
            "id": provider_id,
            "name": "Override Provider",
            "provider_type": "openai_image",
            "base_url": "https://override.example.invalid/v1",
            "api_key": "sk-override-secret",
            "default_model": "default-provider-model",
            "enabled": True,
        })
        return provider_id

    def test_image_request_accepts_provider_model_extra_for_custom_route(self) -> None:
        """ImageRequest should accept provider_model so API schema does not reject the new field."""
        req = self._make_request(model="custom:override-custom-provider", provider_model="override-provider-model")

        self.assertEqual(req.model, "custom:override-custom-provider")
        self.assertEqual(getattr(req, "provider_model", None), "override-provider-model")

    def test_custom_provider_model_override_updates_runtime_metadata_and_skips_default_chain(self) -> None:
        """custom:<id> + provider_model should use provider_model for this request."""
        provider_id = self._setup_custom_provider()
        seen: dict[str, object] = {}

        async def fake_custom_image(req, provider):
            seen["provider_model"] = getattr(req, "provider_model", None)
            seen["default_model"] = provider.get("default_model")
            return copy.deepcopy(SUCCESS_RESULT)

        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.generate_custom_openai_image", fake_custom_image):
            result = await_compat(self.service.create_image(
                self._make_request(
                    prompt="custom override cat",
                    model=f"custom:{provider_id}",
                    provider_model="override-provider-model",
                )
            ))

        mock_chain.assert_not_called()
        self.assertEqual(seen["provider_model"], "override-provider-model")
        self.assertEqual(seen["default_model"], "default-provider-model")
        self.assertEqual(result["model"], "override-provider-model")
        job = self._get_job_by_id(result["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["provider"], f"custom:{provider_id}")
        self.assertEqual(job["model"], "override-provider-model")

    def test_custom_provider_missing_or_empty_provider_model_keeps_default_model(self) -> None:
        """provider_model missing/empty should preserve existing custom default_model behavior."""
        provider_id = self._setup_custom_provider("override-default-provider")

        async def fake_custom_image(req, provider):
            return copy.deepcopy(SUCCESS_RESULT)

        for provider_model in (None, ""):
            with self.subTest(provider_model=provider_model):
                kwargs = {"prompt": "custom default cat", "model": f"custom:{provider_id}"}
                if provider_model is not None:
                    kwargs["provider_model"] = provider_model
                with patch("angemedia_gateway.services.media_service.generate_custom_openai_image", fake_custom_image):
                    result = await_compat(self.service.create_image(self._make_request(**kwargs)))
                self.assertEqual(result["model"], "default-provider-model")

    def test_custom_provider_adapter_sends_provider_model_to_openai_payload(self) -> None:
        """custom OpenAI-compatible adapter should send provider_model, not the stored default_model."""
        from angemedia_gateway.providers.custom import generate_custom_openai_image
        import httpx

        class RecordingAsyncClient:
            instances: list["RecordingAsyncClient"] = []

            def __init__(self, *args, **kwargs) -> None:
                self.kwargs = kwargs
                self.posts: list[dict] = []
                RecordingAsyncClient.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, **kwargs):
                self.posts.append({"url": url, **kwargs})
                return httpx.Response(200, json=copy.deepcopy(SUCCESS_RESULT))

        provider = {
            "enabled": True,
            "base_url": "https://example.com/v1",
            "api_key": "sk-override-secret",
            "default_model": "default-provider-model",
        }
        req = self._make_request(
            model="custom:override-custom-provider",
            provider_model="override-provider-model",
            size="1024x1024",
        )

        with patch("httpx.AsyncClient", new=RecordingAsyncClient):
            await_compat(generate_custom_openai_image(req, provider))

        self.assertEqual(len(RecordingAsyncClient.instances), 1)
        request = RecordingAsyncClient.instances[0].posts[0]
        self.assertEqual(request["url"], "https://example.com/v1/images/generations")
        self.assertIs(RecordingAsyncClient.instances[0].kwargs.get("trust_env"), False)
        self.assertEqual(request["json"]["model"], "override-provider-model")
        rendered = json.dumps(request["json"])
        self.assertNotIn("sk-override-secret", rendered)
        self.assertNotIn("Authorization", rendered)

    def test_non_custom_provider_model_override_returns_safe_400_before_generation(self) -> None:
        """provider_model is only meaningful with model=custom:<id>; builtin routes should reject it safely."""
        from fastapi.testclient import TestClient
        from angemedia_gateway.routing import RouteTarget
        from angemedia_gateway.server import app
        from angemedia_gateway.state import create_gateway_api_key

        key_item = create_gateway_api_key(name="provider-model-safety-red")
        client = TestClient(app)
        provider = FakeImageProvider(SUCCESS_RESULT)
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": provider}):
            mock_chain.return_value = [RouteTarget(provider="siliconflow", model="kolors")]
            response = client.post(
                "/v1/images/generations",
                json={
                    "prompt": "builtin cat",
                    "model": "kolors",
                    "provider_model": "sk-provider-model-secret raw body",
                    "size": "1024x1024",
                },
                headers={"Authorization": f"Bearer {key_item['key']}"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        text = response.text
        self.assertNotIn("sk-provider-model-secret", text)
        self.assertNotIn("raw body", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)


# ── 8-9. generations 和 assets 仍正常 ─────────────────

class ImageJobLegacyRecordsTest(_ImageJobTestBase):
    """generations 和 assets 写入仍正常。"""

    def test_generations_record_still_written(self) -> None:
        """generations 历史记录仍正常。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            await_compat(self.service.create_image(req))
        conn = self._conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
            self.assertGreaterEqual(count, 1)
        finally:
            conn.close()

    def test_asset_still_written(self) -> None:
        """generated asset 写入仍正常。"""
        # 创建真实输出文件，让 _save_generated_asset 能找到它
        fake_file = self._output_dir / "test_asset.png"
        fake_file.write_bytes(b"\x89PNG\r\n")
        fake_result = {
            "created": 1717500000,
            "data": [{"url": f"{C.PUBLIC_BASE_URL}/generated/test_asset.png", "local_path": str(fake_file), "revised_prompt": "a cat"}],
        }
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(fake_result)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            await_compat(self.service.create_image(req))
        conn = self._conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            self.assertGreaterEqual(count, 1)
        finally:
            conn.close()


# ── 10. provider fallback 只创建一个 job ──────────────

class ImageJobFallbackTest(_ImageJobTestBase):
    """provider fallback 只创建一个 job。"""

    def test_fallback_first_fails_second_succeeds_one_job(self) -> None:
        """第一个 provider 失败、第二个成功时，只创建一个 job，最终 succeeded。"""
        from angemedia_gateway.routing import RouteTarget
        req = self._make_request()
        fail_target = RouteTarget(provider="fail_provider", model="fail-model")
        success_target = RouteTarget(provider="ok_provider", model="ok-model")
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {
                 "fail_provider": FakeFailingProvider(),
                 "ok_provider": FakeImageProvider(SUCCESS_RESULT),
             }):
            mock_chain.return_value = [fail_target, success_target]
            result = await_compat(self.service.create_image(req))
        self.assertEqual(self._count_jobs(), 1)
        job = self._get_job_by_id(result["job_id"])
        self.assertEqual(job["status"], "succeeded")


# ── 11-13. 失败路径 ────────────────────────────────────

class ImageJobFailureTest(_ImageJobTestBase):
    """所有 provider 失败时创建 failed job。"""

    def test_all_providers_fail_creates_failed_job(self) -> None:
        """所有 provider 失败时创建 failed job。"""
        from angemedia_gateway.routing import RouteTarget
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {
                 "fail1": FakeFailingProvider(),
                 "fail2": FakeFailingProvider(),
             }):
            mock_chain.return_value = [
                RouteTarget(provider="fail1", model="m1"),
                RouteTarget(provider="fail2", model="m2"),
            ]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))
        self.assertEqual(self._count_jobs(), 1)
        conn = self._conn()
        try:
            row = conn.execute("SELECT status, error_code, error_message FROM jobs").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error_code"], "all_providers_failed")
            self.assertTrue(len(row["error_message"]) > 0)
        finally:
            conn.close()

    def test_failed_job_has_error_code_and_message(self) -> None:
        """failed job 包含 error_code / error_message。"""
        from angemedia_gateway.routing import RouteTarget
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"f": FakeFailingProvider()}):
            mock_chain.return_value = [RouteTarget(provider="f", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))
        conn = self._conn()
        try:
            row = conn.execute("SELECT error_code, error_message FROM jobs").fetchone()
            self.assertEqual(row["error_code"], "all_providers_failed")
            # error_message 不泄露 secret
            self.assertNotIn("Bearer", row["error_message"])
            self.assertNotIn("sk-", row["error_message"])
        finally:
            conn.close()

    def test_failed_job_error_message_no_secret_leak(self) -> None:
        """failed job 的 error_message 不泄露 secret。"""
        from angemedia_gateway.routing import RouteTarget
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"f": FakeFailingProvider()}):
            mock_chain.return_value = [RouteTarget(provider="f", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))
        conn = self._conn()
        try:
            row = conn.execute("SELECT error_message FROM jobs").fetchone()
            msg = row["error_message"]
            self.assertNotIn("am-", msg)
            self.assertNotIn("Bearer", msg)
        finally:
            conn.close()

    def test_error_message_redacts_secrets_from_exception_text(self) -> None:
        """异常消息包含 sk-xxx / am-xxx / Bearer 时，error_message 不含原始 secret。"""
        from angemedia_gateway.routing import RouteTarget

        class SecretLeakingProvider:
            async def generate(self, req, target):
                raise RuntimeError("API key sk-leaked-secret-12345 rejected, Bearer am-leaked-token-67890 denied")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"leak": SecretLeakingProvider()}):
            mock_chain.return_value = [RouteTarget(provider="leak", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))
        conn = self._conn()
        try:
            row = conn.execute("SELECT error_message FROM jobs").fetchone()
            msg = row["error_message"]
            self.assertNotIn("sk-leaked-secret-12345", msg)
            self.assertNotIn("am-leaked-token-67890", msg)
            self.assertIn("REDACTED", msg)
        finally:
            conn.close()


# ── 补充: built-in 成功 job started_at 非空 ────────────

class ImageJobStartedAtTest(_ImageJobTestBase):
    """built-in 成功 job 的 started_at 非空。"""

    def test_builtin_success_job_started_at_non_null(self) -> None:
        """built-in 成功 job 的 started_at 非空。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        job = self._get_job_by_id(result["job_id"])
        self.assertIsNotNone(job["started_at"])
        self.assertIn("T", job["started_at"])


# ── 14-15. job 写入失败不阻断 ─────────────────────────

class ImageJobGracefulDegradationTest(_ImageJobTestBase):
    """job 写入失败时，图片生成仍正常。"""

    def test_create_job_failure_does_not_block_request(self) -> None:
        """mock create_job 抛异常时，图片生成成功 response 仍为 200，且不包含 job_id。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}), \
            patch("angemedia_gateway.services.media_service.create_job", side_effect=RuntimeError("simulated DB failure")):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        self.assertIn("data", result)
        self.assertEqual(len(result["data"]), 1)
        self.assertNotIn("job_id", result)

    def test_update_job_failure_does_not_block_request(self) -> None:
        """mock update_job_status 抛异常时，图片生成成功 response 仍为 200。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}), \
            patch("angemedia_gateway.services.media_service.update_job_status", side_effect=RuntimeError("simulated DB failure")):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        self.assertIn("data", result)
        self.assertEqual(len(result["data"]), 1)


# ── 16. b64_json 不存完整 base64 ───────────────────────

class ImageJobOutputJsonTest(_ImageJobTestBase):
    """output_json 不存储完整 b64 内容。"""

    def test_b64_response_not_stored_in_output_json(self) -> None:
        """b64_json response 不得把完整 base64 写入 jobs.output_json。"""
        b64_result = {
            "created": 1717500000,
            "data": [{"b64_json": "A" * 10000, "revised_prompt": "a cat"}],
        }
        req = self._make_request(response_format="b64_json")
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(b64_result)}), \
            patch("angemedia_gateway.services.media_service.maybe_to_b64", return_value=b64_result):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        job = self._get_job_by_id(result["job_id"])
        self.assertIsNotNone(job)
        output = json.loads(job["output_json"])
        self.assertTrue(output["has_b64_json"])
        self.assertFalse(output["has_url"])
        # 完整 b64 不在 output_json 中
        self.assertNotIn("A" * 10000, job["output_json"])


# ── 17-22. asset.job_id 关联测试 ─────────────────────

class ImageAssetJobIdTest(_ImageJobTestBase):
    """图片生成后 asset 关联 job_id。"""

    def _get_asset_job_id(self, asset_id: str) -> str | None:
        """查询 asset 的 job_id。"""
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT job_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
            return row["job_id"] if row else None
        finally:
            conn.close()

    def _get_asset_by_url_path(self, url_path: str) -> dict | None:
        """按 url_path 查询 asset。"""
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM assets WHERE url_path = ?", (url_path,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def test_asset_job_id_matches_response_job_id(self) -> None:
        """image generation 成功后，asset 的 job_id 等于 response 的 job_id。"""
        # 创建真实输出文件
        fake_file = self._output_dir / "test-asset-job.png"
        fake_file.write_bytes(b"\x89PNG\r\n")
        fake_result = {
            "created": 1717500000,
            "data": [{"url": f"{C.PUBLIC_BASE_URL}/generated/test-asset-job.png", "local_path": str(fake_file), "revised_prompt": "a cat"}],
        }
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(fake_result)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        # asset 存在
        asset = self._get_asset_by_url_path("/generated/test-asset-job.png")
        self.assertIsNotNone(asset)
        # asset.job_id 等于 response.job_id
        self.assertEqual(asset["job_id"], result["job_id"])

    def test_asset_job_kind_is_image(self) -> None:
        """通过 asset["job_id"] 查 job，断言 kind == 'image'。"""
        fake_file = self._output_dir / "kind-check.png"
        fake_file.write_bytes(b"\x89PNG\r\n")
        fake_result = {
            "created": 1717500000,
            "data": [{"url": f"{C.PUBLIC_BASE_URL}/generated/kind-check.png", "local_path": str(fake_file), "revised_prompt": "test"}],
        }
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(fake_result)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            await_compat(self.service.create_image(req))
        # 先查 asset
        asset = self._get_asset_by_url_path("/generated/kind-check.png")
        self.assertIsNotNone(asset)
        self.assertIsNotNone(asset["job_id"])
        # 再用 asset["job_id"] 查 job
        job = self._get_job_by_id(asset["job_id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["kind"], "image")

    def test_job_create_failure_asset_job_id_null(self) -> None:
        """job 创建失败时，image generation 仍成功，asset 仍存在且 job_id=NULL。"""
        fake_file = self._output_dir / "no-job-asset.png"
        fake_file.write_bytes(b"\x89PNG\r\n")
        fake_result = {
            "created": 1717500000,
            "data": [{"url": f"{C.PUBLIC_BASE_URL}/generated/no-job-asset.png", "local_path": str(fake_file), "revised_prompt": "test"}],
        }
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(fake_result)}), \
            patch("angemedia_gateway.services.media_service.create_job", side_effect=RuntimeError("DB failure")):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        # response 不含 job_id
        self.assertNotIn("job_id", result)
        # asset 仍存在且 job_id=NULL
        asset = self._get_asset_by_url_path("/generated/no-job-asset.png")
        self.assertIsNotNone(asset)
        self.assertIsNone(asset["job_id"])

    def test_generations_still_written(self) -> None:
        """generations 记录仍正常。"""
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(SUCCESS_RESULT)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            await_compat(self.service.create_image(req))
        conn = self._conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
            self.assertGreaterEqual(count, 1)
        finally:
            conn.close()

    def test_multi_image_all_assets_share_job_id(self) -> None:
        """多图输出时，每个 asset 的 job_id 都等于 response 的 job_id。"""
        # 创建两个真实输出文件
        fake_file1 = self._output_dir / "multi-1.png"
        fake_file2 = self._output_dir / "multi-2.png"
        fake_file1.write_bytes(b"\x89PNG\r\n1")
        fake_file2.write_bytes(b"\x89PNG\r\n2")
        fake_result = {
            "created": 1717500000,
            "data": [
                {"url": f"{C.PUBLIC_BASE_URL}/generated/multi-1.png", "local_path": str(fake_file1), "revised_prompt": "cat"},
                {"url": f"{C.PUBLIC_BASE_URL}/generated/multi-2.png", "local_path": str(fake_file2), "revised_prompt": "dog"},
            ],
        }
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {"siliconflow": FakeImageProvider(fake_result)}):
            mock_chain.return_value = [type("T", (), {"provider": "siliconflow", "model": "kolors"})()]
            result = await_compat(self.service.create_image(req))
        self.assertIn("job_id", result)
        job_id = result["job_id"]
        # 两个 asset 都存在
        asset1 = self._get_asset_by_url_path("/generated/multi-1.png")
        asset2 = self._get_asset_by_url_path("/generated/multi-2.png")
        self.assertIsNotNone(asset1)
        self.assertIsNotNone(asset2)
        # 两个 asset 的 job_id 都等于 response 的 job_id
        self.assertEqual(asset1["job_id"], job_id)
        self.assertEqual(asset2["job_id"], job_id)

    def test_fallback_asset_job_id_matches_job(self) -> None:
        """provider fallback 场景：第一个失败，第二个成功，asset.job_id = job_id，只创建一个 job。"""
        from angemedia_gateway.routing import RouteTarget
        # 第一个 provider 失败
        fail_target = RouteTarget(provider="fail_provider", model="fail-model")
        # 第二个 provider 成功，生成本地文件
        ok_file = self._output_dir / "fallback-ok.png"
        ok_file.write_bytes(b"\x89PNG\r\nok")
        ok_result = {
            "created": 1717500000,
            "data": [{"url": f"{C.PUBLIC_BASE_URL}/generated/fallback-ok.png", "local_path": str(ok_file), "revised_prompt": "fallback"}],
        }
        ok_target = RouteTarget(provider="ok_provider", model="ok-model")
        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain, \
            patch("angemedia_gateway.services.media_service.PROVIDERS", {
                 "fail_provider": FakeFailingProvider(),
                 "ok_provider": FakeImageProvider(ok_result),
             }):
            mock_chain.return_value = [fail_target, ok_target]
            result = await_compat(self.service.create_image(req))
        # 只创建一个 job
        self.assertEqual(self._count_jobs(), 1)
        self.assertIn("job_id", result)
        # 成功生成的 asset.job_id 等于 response["job_id"]
        asset = self._get_asset_by_url_path("/generated/fallback-ok.png")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["job_id"], result["job_id"])


def await_compat(coro):
    """兼容同步测试调用异步方法。"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# W-ERR-1A Red-contract Tests


class WErr1ASanitizedErrorContractTest(_ImageJobTestBase):
    """W-ERR-1A-R2: 验证错误诊断合同 - 必须断言结构化字段"""

    def test_model_unavailable_requires_structured_error_contract(self) -> None:
        """model disabled 必须有 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.routing import RouteTarget

        class ModelDisabledProvider:
            async def generate(self, req, target):
                raise RuntimeError("30003 Model disabled")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": ModelDisabledProvider()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        jobs = list_jobs(kind="image", status="failed")
        job = next((j for j in jobs if j.get("model") == "m"), None)
        self.assertIsNotNone(job, "failed job should be accessible via list_jobs")
        self.assertIn("error_category", job, "error_category 字段缺失")
        self.assertEqual(job["error_category"], "model_unavailable")
        self.assertIn("human_hint", job, "human_hint 字段缺失")
        self.assertIn("retryable", job, "retryable 字段缺失")
        self.assertFalse(job["retryable"])
        self.assertIn("gateway_stage", job, "gateway_stage 字段缺失")

    def test_content_filtered_requires_structured_error_contract(self) -> None:
        """prompt filtered 必须有 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.routing import RouteTarget

        class ContentFilteredProvider:
            async def generate(self, req, target):
                raise RuntimeError("content policy violation, prompt filtered")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": ContentFilteredProvider()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        jobs = list_jobs(kind="image", status="failed")
        job = next((j for j in jobs if j.get("model") == "m"), None)
        self.assertIsNotNone(job, "failed job should be accessible via list_jobs")
        self.assertIn("error_category", job, "error_category 字段缺失")
        self.assertEqual(job["error_category"], "content_filtered")
        self.assertIn("human_hint", job, "human_hint 字段缺失")
        self.assertIn("retryable", job, "retryable 字段缺失")
        self.assertFalse(job["retryable"])
        self.assertIn("gateway_stage", job, "gateway_stage 字段缺失")

    def test_auth_failed_requires_structured_error_contract(self) -> None:
        """HTTP 401/403 必须有 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.routing import RouteTarget

        class AuthFailedProvider:
            async def generate(self, req, target):
                raise RuntimeError("401 Unauthorized, invalid api key")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": AuthFailedProvider()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        jobs = list_jobs(kind="image", status="failed")
        job = next((j for j in jobs if j.get("model") == "m"), None)
        self.assertIsNotNone(job, "failed job should be accessible via list_jobs")
        self.assertIn("error_category", job, "error_category 字段缺失")
        self.assertEqual(job["error_category"], "auth_failed")
        self.assertIn("human_hint", job, "human_hint 字段缺失")
        self.assertIn("retryable", job, "retryable 字段缺失")
        self.assertFalse(job["retryable"])
        self.assertIn("gateway_stage", job, "gateway_stage 字段缺失")
        self.assertNotIn("sk-test-secret", job.get("error_message") or "")

    def test_quota_or_rate_limited_requires_structured_error_contract(self) -> None:
        """HTTP 429 必须有 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.routing import RouteTarget

        class RateLimitedProvider:
            async def generate(self, req, target):
                raise RuntimeError("429 rate limit exceeded, quota insufficient")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": RateLimitedProvider()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        jobs = list_jobs(kind="image", status="failed")
        job = next((j for j in jobs if j.get("model") == "m"), None)
        self.assertIsNotNone(job, "failed job should be accessible via list_jobs")
        self.assertIn("error_category", job, "error_category 字段缺失")
        self.assertIn(job["error_category"], ["quota_exceeded", "provider_rate_limited"])
        self.assertIn("human_hint", job, "human_hint 字段缺失")
        self.assertIn("retryable", job, "retryable 字段缺失")
        self.assertTrue(job["retryable"])
        self.assertIn("gateway_stage", job, "gateway_stage 字段缺失")

    def test_provider_error_never_leaks_secret_or_raw_body(self) -> None:
        """provider 错误中不得泄露 secret / raw body / headers"""
        from angemedia_gateway.routing import RouteTarget

        class SecretLeakingProvider:
            async def generate(self, req, target):
                raise RuntimeError("api_key=sk-test-secret-12345 Authorization: Bearer am-leaked-token raw body: test")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": SecretLeakingProvider()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        jobs = list_jobs(kind="image", status="failed")
        job = next((j for j in jobs if j.get("model") == "m"), None)
        self.assertIsNotNone(job, "failed job should be accessible via list_jobs")
        self.assertNotIn("sk-test-secret-12345", job.get("error_message") or "")
        self.assertNotIn("am-leaked-token", job.get("error_message") or "")



class WErr2AResponseContractTest(_ImageJobTestBase):
    """W-ERR-2A: 验证 POST /v1/images/generations 502 response 结构化合同"""

    def test_502_response_includes_structured_error_fields(self) -> None:
        """502 response 必须包含 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.routing import RouteTarget
        from fastapi.testclient import TestClient
        from angemedia_gateway.server import app
        from angemedia_gateway.state import create_gateway_api_key

        class ModelDisabledProvider:
            async def generate(self, req, target):
                raise RuntimeError("30003 Model disabled")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": ModelDisabledProvider()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        key_item = create_gateway_api_key(name="test")
        api_key = key_item["key"]
        client = TestClient(app)
        resp = client.post("/v1/images/generations", json={"prompt": "test"},
                           headers={"Authorization": f"Bearer {api_key}"})
        self.assertEqual(resp.status_code, 502)
        detail = resp.json().get("detail", {})

        # 断言结构化字段存在于 response
        self.assertIn("error_category", detail, "502 response 应包含 error_category")
        self.assertIn("human_hint", detail, "502 response 应包含 human_hint")
        self.assertIn("retryable", detail, "502 response 应包含 retryable")
        self.assertIn("gateway_stage", detail, "502 response 应包含 gateway_stage")

        # 断言不泄露敏感信息
        detail_str = json.dumps(detail)
        self.assertNotIn("request_hash", detail_str)
        self.assertNotIn("request_hash_version", detail_str)
        self.assertNotIn("input_json", detail_str)
        self.assertNotIn("output_json", detail_str)
        self.assertNotIn("api_key", detail_str)
        self.assertNotIn("base_url", detail_str)
        self.assertNotIn("status_url", detail_str)
        self.assertNotIn("quota_url", detail_str)


class WErr2ABuiltinProviderFailureDiagnosticsTest(_ImageJobTestBase):
    """W-ERR-2A: 验证 builtin fallback chain failure 写入结构化 job 错误字段（非 custom provider 路径）"""

    def test_custom_provider_failure_writes_structured_fields(self) -> None:
        """builtin fallback chain 全失败时应写入 error_category / human_hint / retryable / gateway_stage"""
        from angemedia_gateway.routing import RouteTarget

        class CustomProviderFailure:
            async def generate(self, req, target):
                raise RuntimeError("custom provider timeout")

        req = self._make_request()
        with patch("angemedia_gateway.services.media_service.resolve_chain") as mock_chain,              patch("angemedia_gateway.services.media_service.PROVIDERS", {"test": CustomProviderFailure()}):
            mock_chain.return_value = [RouteTarget(provider="test", model="m")]
            with self.assertRaises(ImageProvidersFailed):
                await_compat(self.service.create_image(req))

        jobs = list_jobs(kind="image", status="failed")
        job = next((j for j in jobs if j.get("model") == "m"), None)
        self.assertIsNotNone(job, "failed job should be accessible via list_jobs")

        # 断言结构化字段存在
        self.assertIn("error_category", job, "error_category 字段缺失")
        self.assertIn("human_hint", job, "human_hint 字段缺失")
        self.assertIn("retryable", job, "retryable 字段缺失")
        self.assertIn("gateway_stage", job, "gateway_stage 字段缺失")

        # 断言不是旧的 image_generation_failed
        self.assertNotEqual(job["error_code"], "image_generation_failed",
                           "builtin failure 不应使用旧的 image_generation_failed")



# ── W-ERR-2A-R2-D2: Real custom provider failure diagnostics (red-contract) ──

class WErr2ACustomProviderDiagRedTest(_ImageJobTestBase):
    """W-ERR-2A-R2-D2: 验证真实 custom provider failure 写入结构化诊断字段

    与 WErr2ABuiltinProviderFailureDiagnosticsTest 不同：
    - 本测试使用 model="custom:{provider_id}" 走 _create_custom_image
    - 不 patch resolve_chain / PROVIDERS（那是 builtin path 专用）
    - 直接 patch generate_custom_openai_image（custom provider 入口）
    """

    def _setup_custom_provider(self, provider_id="diag-red-custom"):
        upsert_custom_provider({
            "id": provider_id,
            "name": "Red Test Provider",
            "provider_type": "openai_image",
            "base_url": "https://diag-red.example.invalid/v1",
            "api_key": "sk-diag-red-secret",
            "default_model": "diag-red-model",
            "enabled": True,
        })
        return provider_id

    async def _failing_custom_generate(self, req, provider):
        from angemedia_gateway.providers.base import BackendUnavailable
        raise BackendUnavailable("upstream timeout for custom provider diag test")

    def test_custom_provider_failure_writes_structured_job_fields(self):
        """真实 custom provider 失败时应写入 error_category/human_hint/retryable/gateway_stage"""
        provider_id = self._setup_custom_provider()
        req = self._make_request(model="custom:" + provider_id)

        with patch(
            "angemedia_gateway.services.media_service.generate_custom_openai_image",
            self._failing_custom_generate,
        ):
            from angemedia_gateway.providers.base import BackendUnavailable
            with self.assertRaises(BackendUnavailable):
                await_compat(self.service.create_image(req))

        conn = self._conn()
        try:
            # 通过 list_jobs API 按 provider 定位目标 job，不靠 raw SQL latest row
            custom_provider_key = "custom:" + provider_id
            jobs = list_jobs(kind="image", status="failed")
            job = next((j for j in jobs if j.get("provider") == custom_provider_key), None)
            self.assertIsNotNone(job, "custom provider failure should create a job")

            self.assertEqual(job["kind"], "image")
            self.assertEqual(job["status"], "failed")

            self.assertIsNotNone(
                job.get("error_category"),
                "error_category should not be None for custom provider failure",
            )
            self.assertIsNotNone(
                job.get("human_hint"),
                "human_hint should not be None for custom provider failure",
            )
            self.assertIsNotNone(
                job.get("retryable"),
                "retryable should not be None for custom provider failure",
            )
            self.assertIsNotNone(
                job.get("gateway_stage"),
                "gateway_stage should not be None for custom provider failure",
            )

            self.assertNotEqual(
                job.get("error_code"),
                "image_generation_failed",
                "custom provider failure should use classified error_code, not generic",
            )
        finally:
            conn.close()

    def test_custom_provider_failure_502_response_is_structured(self):
        """custom provider 失败时 HTTP 502 response detail 应为结构化 dict 而非 string"""
        from fastapi.testclient import TestClient
        from angemedia_gateway.server import app
        from angemedia_gateway.state import create_gateway_api_key

        provider_id = self._setup_custom_provider()

        key_item = create_gateway_api_key(name="test-custom-red")
        api_key = key_item["key"]

        client = TestClient(app)

        with patch(
            "angemedia_gateway.services.media_service.generate_custom_openai_image",
            self._failing_custom_generate,
        ):
            resp = client.post(
                "/v1/images/generations",
                json={
                    "model": "custom:" + provider_id,
                    "prompt": "test custom diag",
                    "response_format": "url",
                    "size": "1024x1024",
                },
                headers={"Authorization": "Bearer " + api_key},
            )

        self.assertEqual(resp.status_code, 502)
        detail = resp.json().get("detail", {})

        self.assertIsInstance(
            detail,
            dict,
            "502 response detail should be a structured dict, "
            "not a plain string (custom provider BackendUnavailable handler "
            "not calling classify_provider_error)",
        )

        self.assertIn(
            "error_category",
            detail,
            "502 detail should contain error_category",
        )
        self.assertIn(
            "human_hint",
            detail,
            "502 detail should contain human_hint",
        )
        self.assertIn(
            "retryable",
            detail,
            "502 detail should contain retryable",
        )
        self.assertIn(
            "gateway_stage",
            detail,
            "502 detail should contain gateway_stage",
        )

        # 断言不泄露敏感信息
        import json
        detail_str = json.dumps(detail)
        self.assertNotIn("sk-diag-red-secret", detail_str)
        self.assertNotIn("api_key", detail_str)
        self.assertNotIn("base_url", detail_str)
        self.assertNotIn("request_hash", detail_str)
        self.assertNotIn("input_json", detail_str)
        self.assertNotIn("output_json", detail_str)


class SiliconFlowPayloadMappingTest(unittest.TestCase):
    """SiliconFlow payload should reflect validated Kolors request parameters."""

    def test_siliconflow_payload_maps_optional_kolors_params(self) -> None:
        from angemedia_gateway.providers.image import SiliconFlowProvider
        from angemedia_gateway.providers.base import RouteTarget
        import asyncio
        import httpx

        class RecordingAsyncClient:
            instances: list["RecordingAsyncClient"] = []

            def __init__(self, *args, **kwargs) -> None:
                self.posts: list[dict] = []
                RecordingAsyncClient.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, **kwargs):
                self.posts.append({"url": url, **kwargs})
                return httpx.Response(200, json={"images": [{"url": "https://example.com/kolors.png"}]})

        async def run() -> dict:
            with patch("httpx.AsyncClient", new=RecordingAsyncClient):
                req = ImageRequest(
                    prompt="test",
                    model="kolors",
                    size="960x1280",
                    negative_prompt="blur",
                    seed=123,
                    steps=30,
                    guidance=8.5,
                )
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                await SiliconFlowProvider().generate(req, target)
            return RecordingAsyncClient.instances[0].posts[0]["json"]

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.HTTP_TIMEOUT", 10),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024", "960x1280"}),
        ):
            payload = asyncio.run(run())

        self.assertEqual(payload["model"], "Kwai-Kolors/Kolors")
        self.assertEqual(payload["prompt"], "test")
        self.assertEqual(payload["image_size"], "960x1280")
        self.assertEqual(payload["batch_size"], 1)
        self.assertEqual(payload["num_inference_steps"], 30)
        self.assertEqual(payload["guidance_scale"], 8.5)
        self.assertEqual(payload["negative_prompt"], "blur")
        self.assertEqual(payload["seed"], 123)
        self.assertTrue(all(value is not None for value in payload.values()))

    def test_siliconflow_payload_keeps_legacy_defaults_without_none_fields(self) -> None:
        from angemedia_gateway.providers.image import SiliconFlowProvider
        from angemedia_gateway.providers.base import RouteTarget
        import asyncio
        import httpx

        class RecordingAsyncClient:
            instances: list["RecordingAsyncClient"] = []

            def __init__(self, *args, **kwargs) -> None:
                self.posts: list[dict] = []
                RecordingAsyncClient.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, **kwargs):
                self.posts.append({"url": url, **kwargs})
                return httpx.Response(200, json={"images": [{"url": "https://example.com/default.png"}]})

        async def run() -> dict:
            with patch("httpx.AsyncClient", new=RecordingAsyncClient):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                await SiliconFlowProvider().generate(req, target)
            return RecordingAsyncClient.instances[0].posts[0]["json"]

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.HTTP_TIMEOUT", 10),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            payload = asyncio.run(run())

        self.assertEqual(payload["num_inference_steps"], 20)
        self.assertEqual(payload["guidance_scale"], 7.5)
        self.assertEqual(payload["batch_size"], 1)
        self.assertNotIn("negative_prompt", payload)
        self.assertNotIn("seed", payload)
        self.assertTrue(all(value is not None for value in payload.values()))

    def test_siliconflow_payload_forwards_image_reference_for_image_to_image(self) -> None:
        from angemedia_gateway.providers.image import SiliconFlowProvider
        from angemedia_gateway.providers.base import RouteTarget
        import asyncio
        import httpx

        class RecordingAsyncClient:
            instances: list["RecordingAsyncClient"] = []

            def __init__(self, *args, **kwargs) -> None:
                self.posts: list[dict] = []
                RecordingAsyncClient.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, **kwargs):
                self.posts.append({"url": url, **kwargs})
                return httpx.Response(200, json={"images": [{"url": "https://example.com/i2i.png"}]})

        async def run(image: str) -> dict:
            with patch("httpx.AsyncClient", new=RecordingAsyncClient):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024", image=image)
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                await SiliconFlowProvider().generate(req, target)
            return RecordingAsyncClient.instances[-1].posts[0]["json"]

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.HTTP_TIMEOUT", 10),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            remote_payload = asyncio.run(run("https://example.com/source.png"))
            with patch(
                "angemedia_gateway.providers.image.siliconflow.materialize_image_reference",
                return_value="data:image/png;base64,AAAA",
            ):
                path_payload = asyncio.run(run("/uploads/source.png"))

        self.assertEqual(remote_payload["image"], "https://example.com/source.png")
        self.assertEqual(path_payload["image"], "data:image/png;base64,AAAA")
        self.assertEqual(remote_payload["image_size"], "1024x1024")
        self.assertTrue(all(value is not None for value in remote_payload.values()))


class ProviderSafeMessageTest(unittest.TestCase):
    """Provider error messages 不泄露上游 raw body / raw dict。"""

    def _mock_httpx(self, fake_resp):
        """创建一个 mock httpx.AsyncClient，返回 fake_resp。"""
        from unittest.mock import AsyncMock
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)
        mock_client.get = AsyncMock(return_value=fake_resp)
        return patch("httpx.AsyncClient", return_value=mock_client)

    def _mock_httpx_custom(self, fake_resp):
        """创建一个 mock httpx.AsyncClient for custom.py。"""
        from unittest.mock import AsyncMock
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_resp)
        return patch("httpx.AsyncClient", return_value=mock_client)

    def test_siliconflow_http_error_no_raw_body(self) -> None:
        from angemedia_gateway.providers.image import SiliconFlowProvider
        from angemedia_gateway.providers.base import BackendUnavailable, RouteTarget
        from angemedia_gateway.schemas import ImageRequest
        import asyncio

        marker = "SILICONFLOW_RAW_BODY_SHOULD_NOT_LEAK"
        fake_resp = type("Resp", (), {"status_code": 500, "text": marker})()

        async def run():
            with self._mock_httpx(fake_resp):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(BackendUnavailable) as ctx:
                    await SiliconFlowProvider().generate(req, target)
                self.assertNotIn(marker, str(ctx.exception))
                self.assertIn("HTTP 500", str(ctx.exception))
                self.assertEqual(ctx.exception.status_code, 500)

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.HTTP_TIMEOUT", 10),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            asyncio.run(run())

    def test_openai_compatible_http_error_no_raw_body(self) -> None:
        from angemedia_gateway.providers.image import OpenAICompatibleImageProvider
        from angemedia_gateway.providers.base import BackendUnavailable, RouteTarget
        from angemedia_gateway.schemas import ImageRequest
        import asyncio

        marker = "OPENAI_IMAGE_RAW_BODY_SHOULD_NOT_LEAK"
        fake_resp = type("Resp", (), {"status_code": 500, "text": marker})()

        async def run():
            with self._mock_httpx(fake_resp):
                req = ImageRequest(prompt="test", model="gpt-image-2", size="1024x1024")
                target = RouteTarget(provider="openai_image", model="gpt-image-2")
                with self.assertRaises(BackendUnavailable) as ctx:
                    await OpenAICompatibleImageProvider().generate(req, target)
                self.assertNotIn(marker, str(ctx.exception))
                self.assertIn("HTTP 500", str(ctx.exception))
                self.assertEqual(ctx.exception.status_code, 500)

        with (
            patch("angemedia_gateway.config.OPENAI_IMAGE_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.OPENAI_IMAGE_BASE_URL", "https://api.openai.com/v1"),
            patch("angemedia_gateway.config.HTTP_TIMEOUT", 10),
        ):
            asyncio.run(run())

    def test_custom_provider_http_error_no_raw_body(self) -> None:
        from angemedia_gateway.providers.custom import generate_custom_openai_image
        from angemedia_gateway.providers.base import BackendUnavailable
        from angemedia_gateway.schemas import ImageRequest
        import asyncio

        marker = "CUSTOM_RAW_BODY_SHOULD_NOT_LEAK"
        fake_resp = type("Resp", (), {"status_code": 500, "text": marker})()

        async def run():
            with self._mock_httpx_custom(fake_resp):
                req = ImageRequest(prompt="test", model="test-model", size="1024x1024")
                provider = {"enabled": True, "base_url": "https://example.com/v1", "api_key": "sk-test", "default_model": "test-model"}
                with self.assertRaises(BackendUnavailable) as ctx:
                    await generate_custom_openai_image(req, provider)
                self.assertNotIn(marker, str(ctx.exception))
                self.assertIn("HTTP 500", str(ctx.exception))
                self.assertEqual(ctx.exception.status_code, 500)

        asyncio.run(run())

    def test_siliconflow_no_image_url_no_raw_dict(self) -> None:
        from angemedia_gateway.providers.image import SiliconFlowProvider
        from angemedia_gateway.providers.base import BackendUnavailable, RouteTarget
        from angemedia_gateway.schemas import ImageRequest
        import asyncio

        marker = "RAW_DICT_SHOULD_NOT_LEAK"
        fake_data = {"error": marker, "images": []}
        fake_resp = type("Resp", (), {"status_code": 200, "text": "{}", "json": lambda s: fake_data})()

        async def run():
            with self._mock_httpx(fake_resp):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(BackendUnavailable) as ctx:
                    await SiliconFlowProvider().generate(req, target)
                self.assertNotIn(marker, str(ctx.exception))

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.HTTP_TIMEOUT", 10),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            asyncio.run(run())

    def test_custom_provider_no_image_data_no_raw_dict(self) -> None:
        from angemedia_gateway.providers.custom import generate_custom_openai_image
        from angemedia_gateway.providers.base import BackendUnavailable
        from angemedia_gateway.schemas import ImageRequest
        import asyncio

        marker = "RAW_DICT_SHOULD_NOT_LEAK"
        fake_data = {"error": marker, "data": [{}]}
        fake_resp = type("Resp", (), {"status_code": 200, "text": "{}", "json": lambda s: fake_data})()

        async def run():
            with self._mock_httpx_custom(fake_resp):
                req = ImageRequest(prompt="test", model="test-model", size="1024x1024")
                provider = {"enabled": True, "base_url": "https://example.com/v1", "api_key": "sk-test", "default_model": "test-model"}
                with self.assertRaises(BackendUnavailable) as ctx:
                    await generate_custom_openai_image(req, provider)
                self.assertNotIn(marker, str(ctx.exception))

        asyncio.run(run())

    def test_provider_error_messages_do_not_reference_raw_response_text(self) -> None:
        """静态回归：生产 provider 文件中不得出现 raw body 拼接模式。"""
        dangerous_patterns = [
            "resp.text",
            "submit.text",
            "poll.text",
            "response.text",
            ".text[:300]",
            ".text[:200]",
            ": {data}",
            ": {task}",
            "redact_secret_text(resp.text",
        ]
        provider_files = [
            ROOT / "scripts" / "angemedia_gateway" / "providers" / "custom.py",
        ]
        provider_files.extend(
            sorted((ROOT / "scripts" / "angemedia_gateway" / "providers" / "image").glob("*.py"))
        )
        for file_path in provider_files:
            source = file_path.read_text(encoding="utf-8")
            for pattern in dangerous_patterns:
                if pattern in source:
                    self.fail(
                        f"Dangerous pattern {pattern!r} found in {file_path.name}. "
                        "Provider error messages must not include raw upstream body."
                    )

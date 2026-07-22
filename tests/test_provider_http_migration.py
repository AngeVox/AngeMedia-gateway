from __future__ import annotations

import sys
import socket
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.base import BackendUnavailable, RouteTarget  # noqa: E402
from angemedia_gateway.providers.errors import (  # noqa: E402
    ProviderProtocolError,
    ProviderTimeout,
    RateLimited,
)
from angemedia_gateway.providers.custom import generate_custom_openai_image  # noqa: E402
from angemedia_gateway.providers.http import provider_client  # noqa: E402
from angemedia_gateway.providers.image import (  # noqa: E402
    AgnesImageProvider,
    ByteDanceImageProvider,
    ModelScopeProvider,
    OpenAICompatibleImageProvider,
    SiliconFlowProvider,
)
from angemedia_gateway.providers.image.bytedance import build_bytedance_image_payload  # noqa: E402
from angemedia_gateway.providers.image import modelscope as modelscope_module  # noqa: E402
from angemedia_gateway.schemas import ImageRequest  # noqa: E402


SECRET_MARKERS = (
    "sk-secret",
    "Bearer secret",
    "Authorization",
    "api_key",
    "token",
    "password",
    "SECRET_HTML",
)


class FakeAsyncClient:
    def __init__(self, *, post=None, get=None) -> None:
        self.post_values = list(post if isinstance(post, list) else [post])
        self.get_values = list(get if isinstance(get, list) else [get])
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        self.post_calls.append((url, kwargs))
        return self._next(self.post_values)

    async def get(self, url: str, **kwargs):
        self.get_calls.append((url, kwargs))
        return self._next(self.get_values)

    @staticmethod
    def _next(values):
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _response(status_code: int, *, json_data=None, text: str | None = None) -> httpx.Response:
    if json_data is not None:
        return httpx.Response(status_code, json=json_data)
    return httpx.Response(status_code, content=(text or "").encode("utf-8"))


class ProviderHttpFoundationMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dns_patch = patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )
        self._dns_patch.start()

    def tearDown(self) -> None:
        self._dns_patch.stop()

    def assert_safe_error(self, exc: Exception) -> None:
        text = str(exc)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, text)

    def test_provider_client_disables_trust_env(self) -> None:
        with patch("httpx.AsyncClient") as async_client:
            provider_client(timeout=3)

        self.assertFalse(async_client.call_args.kwargs["trust_env"])
        self.assertIsInstance(async_client.call_args.kwargs["timeout"], httpx.Timeout)

    def test_openai_compatible_success_shape_and_payload_are_unchanged(self) -> None:
        result = {"data": [{"url": "https://example.test/out.png"}]}
        fake = FakeAsyncClient(post=_response(200, json_data=result))

        async def run() -> dict:
            with self._openai_patches(fake):
                req = ImageRequest(prompt="test", model="gpt-image-2", size="1024x1024", quality="high", user="u1")
                return await OpenAICompatibleImageProvider().generate(req, self._openai_target())

        import asyncio

        self.assertEqual(asyncio.run(run()), result)
        payload = fake.post_calls[0][1]["json"]
        headers = fake.post_calls[0][1]["headers"]
        self.assertEqual(
            payload,
            {
                "model": "gpt-image-2",
                "prompt": "test",
                "n": 1,
                "size": "1024x1024",
                "response_format": "url",
                "quality": "high",
                "user": "u1",
            },
        )
        self.assertEqual(headers["Authorization"], "Bearer sk-openai-config-secret")

    def test_openai_compatible_errors_are_safe(self) -> None:
        async def http_500() -> None:
            fake = FakeAsyncClient(post=_response(500, text="SECRET_HTML sk-secret Authorization: Bearer secret"))
            with self._openai_patches(fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await OpenAICompatibleImageProvider().generate(self._image_request(), self._openai_target())
            self.assertEqual(ctx.exception.status_code, 500)
            self.assert_safe_error(ctx.exception)

        async def invalid_json() -> None:
            fake = FakeAsyncClient(post=_response(200, text="not json sk-secret token password"))
            with self._openai_patches(fake):
                with self.assertRaises(ProviderProtocolError) as ctx:
                    await OpenAICompatibleImageProvider().generate(self._image_request(), self._openai_target())
            self.assert_safe_error(ctx.exception)

        async def rate_limit() -> None:
            fake = FakeAsyncClient(post=_response(429, text="sk-secret token password"))
            with self._openai_patches(fake):
                with self.assertRaises(RateLimited) as ctx:
                    await OpenAICompatibleImageProvider().generate(self._image_request(), self._openai_target())
            self.assertEqual(ctx.exception.status_code, 429)
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(http_500())
        asyncio.run(invalid_json())
        asyncio.run(rate_limit())

    def test_bytedance_seedream_payload_auth_and_success_shapes(self) -> None:
        expected_payload = {
            "model": "seedream-3-0-t2i-250415",
            "prompt": "test",
            "size": "1280x720",
            "seed": 42,
            "response_format": "b64_json",
        }
        req = ImageRequest(
            prompt="test",
            model="seedream-3",
            size="1280x720",
            seed=42,
            aspect_ratio="16:9",
            image="https://example.test/reference.png",
            negative_prompt="unsupported",
            guidance=7.5,
        )
        self.assertEqual(build_bytedance_image_payload(req, self._bytedance_target()), expected_payload)

        import asyncio

        for item in ({"b64_json": "U0VFRA=="}, {"url": "https://example.test/seedream.png"}):
            with self.subTest(item=item):
                result = {"data": [item]}
                fake = FakeAsyncClient(post=_response(200, json_data=result))

                async def run() -> dict:
                    with self._bytedance_patches(fake):
                        clean_req = ImageRequest(
                            prompt="test", model="seedream-3", size="1280x720", seed=42
                        )
                        return await ByteDanceImageProvider().generate(clean_req, self._bytedance_target())

                self.assertEqual(asyncio.run(run()), result)
                self.assertEqual(fake.post_calls[0][0], "https://ark.example.test/api/v3/images/generations")
                self.assertEqual(fake.post_calls[0][1]["json"], expected_payload)
                self.assertEqual(
                    fake.post_calls[0][1]["headers"]["Authorization"],
                    "Bearer bytedance-test-key",
                )

    def test_bytedance_seedream_errors_are_safe(self) -> None:
        import asyncio

        cases = [
            (_response(500, text="SECRET_HTML sk-secret Authorization: Bearer secret"), BackendUnavailable),
            (_response(200, json_data={"data": [{}]}), ProviderProtocolError),
        ]
        for response, error_type in cases:
            with self.subTest(error=error_type.__name__):
                fake = FakeAsyncClient(post=response)

                async def run() -> None:
                    with self._bytedance_patches(fake):
                        with self.assertRaises(error_type) as ctx:
                            await ByteDanceImageProvider().generate(
                                self._image_request(), self._bytedance_target()
                            )
                    self.assert_safe_error(ctx.exception)

                asyncio.run(run())

    def test_openai_compatible_timeout_and_network_errors_are_safe(self) -> None:
        async def timeout_case() -> None:
            fake = FakeAsyncClient(post=httpx.ReadTimeout("sk-secret token password"))
            with self._openai_patches(fake):
                with self.assertRaises(ProviderTimeout) as ctx:
                    await OpenAICompatibleImageProvider().generate(self._image_request(), self._openai_target())
            self.assertEqual(ctx.exception.error_category, "timeout")
            self.assert_safe_error(ctx.exception)

        async def network_case() -> None:
            fake = FakeAsyncClient(post=httpx.ConnectError("sk-secret token password"))
            with self._openai_patches(fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await OpenAICompatibleImageProvider().generate(self._image_request(), self._openai_target())
            self.assertEqual(ctx.exception.error_category, "network")
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(timeout_case())
        asyncio.run(network_case())

    def test_custom_provider_success_shape_and_payload_are_unchanged(self) -> None:
        result = {"data": [{"b64_json": "abc"}]}
        fake = FakeAsyncClient(post=_response(200, json_data=result))

        async def run() -> dict:
            provider = self._custom_provider()
            with patch("httpx.AsyncClient", return_value=fake):
                req = ImageRequest(
                    prompt="test",
                    model="ignored",
                    size="1024x1024",
                    response_format="b64_json",
                    quality="high",
                    user="u1",
                    negative_prompt="no blur",
                    seed=123,
                )
                return await generate_custom_openai_image(req, provider)

        import asyncio

        self.assertEqual(asyncio.run(run()), result)
        payload = fake.post_calls[0][1]["json"]
        headers = fake.post_calls[0][1]["headers"]
        self.assertEqual(
            payload,
            {
                "model": "custom-model",
                "prompt": "test",
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
                "quality": "high",
                "user": "u1",
                "negative_prompt": "no blur",
                "seed": 123,
            },
        )
        self.assertEqual(headers["Authorization"], "Bearer sk-custom-provider-secret")

    def test_custom_provider_errors_are_safe(self) -> None:
        async def http_500() -> None:
            fake = FakeAsyncClient(post=_response(500, text="SECRET_HTML sk-custom-provider-secret Authorization: Bearer secret"))
            with patch("httpx.AsyncClient", return_value=fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await generate_custom_openai_image(self._image_request(), self._custom_provider())
            self.assertEqual(ctx.exception.status_code, 500)
            self.assert_safe_error(ctx.exception)
            self.assertNotIn("sk-custom-provider-secret", str(ctx.exception))

        async def invalid_json() -> None:
            fake = FakeAsyncClient(post=_response(200, text="not json sk-custom-provider-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                with self.assertRaises(ProviderProtocolError) as ctx:
                    await generate_custom_openai_image(self._image_request(), self._custom_provider())
            self.assert_safe_error(ctx.exception)
            self.assertNotIn("sk-custom-provider-secret", str(ctx.exception))

        async def rate_limit() -> None:
            fake = FakeAsyncClient(post=_response(429, text="sk-custom-provider-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                with self.assertRaises(RateLimited) as ctx:
                    await generate_custom_openai_image(self._image_request(), self._custom_provider())
            self.assertEqual(ctx.exception.status_code, 429)
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(http_500())
        asyncio.run(invalid_json())
        asyncio.run(rate_limit())

    def test_custom_provider_timeout_and_network_errors_are_safe(self) -> None:
        async def timeout_case() -> None:
            fake = FakeAsyncClient(post=httpx.ReadTimeout("sk-custom-provider-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                with self.assertRaises(ProviderTimeout) as ctx:
                    await generate_custom_openai_image(self._image_request(), self._custom_provider())
            self.assertEqual(ctx.exception.error_category, "timeout")
            self.assert_safe_error(ctx.exception)

        async def network_case() -> None:
            fake = FakeAsyncClient(post=httpx.ConnectError("sk-custom-provider-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await generate_custom_openai_image(self._image_request(), self._custom_provider())
            self.assertEqual(ctx.exception.error_category, "network")
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(timeout_case())
        asyncio.run(network_case())

    def test_siliconflow_500_body_is_not_leaked(self) -> None:
        fake = FakeAsyncClient(post=_response(500, text="SECRET_HTML sk-secret Authorization: Bearer secret"))

        async def run() -> None:
            with patch("httpx.AsyncClient", return_value=fake):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(BackendUnavailable) as ctx:
                    await SiliconFlowProvider().generate(req, target)

            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("HTTP 500", str(ctx.exception))
            self.assert_safe_error(ctx.exception)

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            import asyncio

            asyncio.run(run())

    def test_siliconflow_invalid_json_and_rate_limit_are_safe(self) -> None:
        async def invalid_json() -> None:
            fake = FakeAsyncClient(post=_response(200, text="not json sk-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(ProviderProtocolError) as ctx:
                    await SiliconFlowProvider().generate(req, target)
            self.assert_safe_error(ctx.exception)
            self.assertIn("invalid JSON", str(ctx.exception))

        async def rate_limit() -> None:
            fake = FakeAsyncClient(post=_response(429, text="sk-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(RateLimited) as ctx:
                    await SiliconFlowProvider().generate(req, target)
            self.assertEqual(ctx.exception.status_code, 429)
            self.assert_safe_error(ctx.exception)

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            import asyncio

            asyncio.run(invalid_json())
            asyncio.run(rate_limit())

    def test_siliconflow_timeout_and_network_errors_are_safe(self) -> None:
        async def timeout_case() -> None:
            fake = FakeAsyncClient(post=httpx.ReadTimeout("sk-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(ProviderTimeout) as ctx:
                    await SiliconFlowProvider().generate(req, target)
            self.assertEqual(ctx.exception.error_category, "timeout")
            self.assert_safe_error(ctx.exception)

        async def network_case() -> None:
            fake = FakeAsyncClient(post=httpx.ConnectError("sk-secret token password"))
            with patch("httpx.AsyncClient", return_value=fake):
                req = ImageRequest(prompt="test", model="kolors", size="1024x1024")
                target = RouteTarget(provider="siliconflow", model="Kwai-Kolors/Kolors")
                with self.assertRaises(BackendUnavailable) as ctx:
                    await SiliconFlowProvider().generate(req, target)
            self.assertEqual(ctx.exception.error_category, "network")
            self.assert_safe_error(ctx.exception)

        with (
            patch("angemedia_gateway.config.SILICONFLOW_API_KEY", "sk-test"),
            patch("angemedia_gateway.config.KOLORS_SIZES", {"1024x1024"}),
        ):
            import asyncio

            asyncio.run(timeout_case())
            asyncio.run(network_case())

    def test_modelscope_submit_errors_and_quota_rate_limit_are_safe(self) -> None:
        async def submit_500() -> None:
            fake = FakeAsyncClient(post=_response(500, text="SECRET_HTML sk-secret token password"))
            with self._modelscope_patches(fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await ModelScopeProvider().generate(self._image_request(), self._modelscope_target())
            self.assertEqual(ctx.exception.status_code, 500)
            self.assert_safe_error(ctx.exception)

        async def invalid_json() -> None:
            fake = FakeAsyncClient(post=_response(200, text="not json sk-secret token password"))
            with self._modelscope_patches(fake):
                with self.assertRaises(ProviderProtocolError) as ctx:
                    await ModelScopeProvider().generate(self._image_request(), self._modelscope_target())
            self.assert_safe_error(ctx.exception)

        async def rate_limit() -> None:
            fake = FakeAsyncClient(post=_response(429, text="sk-secret token password"))
            mark = AsyncMock()
            with self._modelscope_patches(fake, mark_exhausted=mark):
                with self.assertRaises(RateLimited) as ctx:
                    await ModelScopeProvider().generate(self._image_request(), self._modelscope_target())
            self.assertEqual(mark.await_count, 1)
            self.assertEqual(str(ctx.exception), "ModelScope remote quota is exhausted")
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(submit_500())
        asyncio.run(invalid_json())
        asyncio.run(rate_limit())

    def test_modelscope_submit_forwards_supported_size_only(self) -> None:
        fake = FakeAsyncClient(
            post=_response(200, json_data={"task_id": "task-1"}),
            get=_response(200, json_data={"task_status": "SUCCEED", "output_images": ["https://example.test/out.png"]}),
        )

        async def run() -> None:
            with self._modelscope_patches(fake):
                req = ImageRequest(
                    prompt="test",
                    model="qwen",
                    size="1664x928",
                    negative_prompt="must-not-leak",
                    seed=42,
                    steps=20,
                    guidance=4.0,
                )
                await ModelScopeProvider().generate(req, self._modelscope_target())

        import asyncio

        asyncio.run(run())
        payload = fake.post_calls[0][1]["json"]
        self.assertEqual(
            payload,
            {
                "model": "modelscope-model",
                "prompt": "test",
                "n": 1,
                "size": "1664x928",
            },
        )

    def test_modelscope_poll_errors_and_terminal_failed_are_safe(self) -> None:
        async def poll_500() -> None:
            fake = FakeAsyncClient(
                post=_response(200, json_data={"task_id": "task-1"}),
                get=_response(500, text="SECRET_HTML sk-secret token password"),
            )
            with self._modelscope_patches(fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await ModelScopeProvider().generate(self._image_request(), self._modelscope_target())
            self.assertEqual(ctx.exception.status_code, 500)
            self.assert_safe_error(ctx.exception)

        async def terminal_failed() -> None:
            fake = FakeAsyncClient(
                post=_response(200, json_data={"task_id": "task-1"}),
                get=_response(200, json_data={"task_status": "FAILED", "error": "sk-secret token password"}),
            )
            with self._modelscope_patches(fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await ModelScopeProvider().generate(self._image_request(), self._modelscope_target())
            self.assertEqual(str(ctx.exception), "ModelScope 任务失败")
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(poll_500())
        asyncio.run(terminal_failed())

    def test_agnes_image_http_invalid_json_and_missing_field_are_safe(self) -> None:
        async def http_500() -> None:
            fake = FakeAsyncClient(post=_response(500, text="SECRET_HTML sk-secret token password"))
            with self._agnes_patches(fake):
                with self.assertRaises(BackendUnavailable) as ctx:
                    await AgnesImageProvider().generate(self._image_request(), self._agnes_target())
            self.assertEqual(ctx.exception.status_code, 500)
            self.assert_safe_error(ctx.exception)

        async def invalid_json() -> None:
            fake = FakeAsyncClient(post=_response(200, text="not json sk-secret token password"))
            with self._agnes_patches(fake):
                with self.assertRaises(ProviderProtocolError) as ctx:
                    await AgnesImageProvider().generate(self._image_request(), self._agnes_target())
            self.assert_safe_error(ctx.exception)

        async def missing_expected_field() -> None:
            fake = FakeAsyncClient(post=_response(200, json_data={"error": "sk-secret token password"}))
            with self._agnes_patches(fake):
                with self.assertRaises(ProviderProtocolError) as ctx:
                    await AgnesImageProvider().generate(self._image_request(), self._agnes_target())
            self.assertEqual(str(ctx.exception), "Agnes Image generate failed: protocol")
            self.assert_safe_error(ctx.exception)

        import asyncio

        asyncio.run(http_500())
        asyncio.run(invalid_json())
        asyncio.run(missing_expected_field())

    def test_agnes_text_to_image_payload_uses_only_documented_fields(self) -> None:
        fake = FakeAsyncClient(post=_response(200, json_data={"data": [{"url": "https://example.test/out.png"}]}))

        async def run() -> None:
            with self._agnes_patches(fake):
                req = ImageRequest(
                    prompt="test",
                    model="agnes-image",
                    size="1024x768",
                    response_format="b64_json",
                    negative_prompt="unsupported",
                    seed=42,
                    steps=20,
                    guidance=4.0,
                    reference_image="https://example.test/in.png",
                    api_key="sk-secret",
                )
                await AgnesImageProvider().generate(req, self._agnes_target())

        import asyncio

        asyncio.run(run())
        payload = fake.post_calls[0][1]["json"]
        self.assertEqual(
            payload,
            {
                "model": "agnes-image-2.1-flash",
                "prompt": "test",
                "size": "1024x768",
                "return_base64": True,
            },
        )

    def test_agnes_image_to_image_payload_uses_image_array_without_legacy_tag(self) -> None:
        fake = FakeAsyncClient(post=_response(200, json_data={"data": [{"url": "https://example.test/out.png"}]}))

        async def run() -> None:
            with self._agnes_patches(fake):
                req = ImageRequest(
                    prompt="test",
                    model="agnes-2.0",
                    size="768x1024",
                    image="https://example.test/base.png",
                    images=["https://example.test/second.png", "https://example.test/third.png"],
                    seed=7,
                    negative_prompt="unsupported",
                    steps=20,
                )
                await AgnesImageProvider().generate(req, self._agnes_target("agnes-image-2.0-flash"))

        import asyncio

        asyncio.run(run())
        payload = fake.post_calls[0][1]["json"]
        self.assertEqual(payload["seed"], 7)
        self.assertNotIn("tags", payload)
        self.assertEqual(
            payload["extra_body"],
            {
                "image": [
                    "https://example.test/base.png",
                    "https://example.test/second.png",
                    "https://example.test/third.png",
                ],
                "response_format": "url",
            },
        )
        for unsupported in ("image", "images", "negative_prompt", "steps"):
            self.assertNotIn(unsupported, payload)

    def test_agnes_materializes_gateway_paths_and_preserves_data_urls(self) -> None:
        fake = FakeAsyncClient(post=_response(200, json_data={"data": [{"url": "https://example.test/out.png"}]}))
        data_url = "data:image/png;base64,AAAA"

        async def run() -> None:
            with self._agnes_patches(fake), patch(
                "angemedia_gateway.providers.image.agnes.materialize_image_reference",
                side_effect=lambda value: data_url if value == "/uploads/source.png" else value,
            ):
                req = ImageRequest(
                    prompt="test",
                    model="agnes-2.1",
                    size="1280x720",
                    image="/uploads/source.png",
                    images=[data_url],
                )
                await AgnesImageProvider().generate(req, self._agnes_target())

        import asyncio

        asyncio.run(run())
        payload = fake.post_calls[0][1]["json"]
        self.assertNotIn("tags", payload)
        self.assertEqual(payload["extra_body"]["image"], [data_url, data_url])
        self.assertEqual(payload["extra_body"]["response_format"], "url")

    def test_agnes_21_named_size_ratio_and_image_base64_follow_current_api(self) -> None:
        fake = FakeAsyncClient(post=_response(200, json_data={"data": [{"b64_json": "AAAA"}]}))

        async def run() -> None:
            with self._agnes_patches(fake):
                req = ImageRequest(
                    prompt="test",
                    model="agnes-2.1",
                    size="2K",
                    aspect_ratio="16:9",
                    image="https://example.test/base.png",
                    response_format="b64_json",
                )
                await AgnesImageProvider().generate(req, self._agnes_target())

        import asyncio

        asyncio.run(run())
        payload = fake.post_calls[0][1]["json"]
        self.assertEqual(payload["size"], "2K")
        self.assertEqual(payload["ratio"], "16:9")
        self.assertEqual(payload["extra_body"], {
            "image": ["https://example.test/base.png"],
            "response_format": "b64_json",
        })
        self.assertNotIn("return_base64", payload)
        self.assertNotIn("tags", payload)

    @staticmethod
    def _image_request() -> ImageRequest:
        return ImageRequest(prompt="test", model="model", size="1024x1024")

    @staticmethod
    def _openai_target() -> RouteTarget:
        return RouteTarget(provider="openai_image", model="gpt-image-2")

    @staticmethod
    def _custom_provider() -> dict[str, object]:
        return {
            "enabled": True,
            "base_url": "https://example.com/v1",
            "api_key": "sk-custom-provider-secret",
            "default_model": "custom-model",
        }

    @staticmethod
    def _modelscope_target() -> RouteTarget:
        return RouteTarget(provider="modelscope", model="modelscope-model")

    @staticmethod
    def _agnes_target(model: str = "agnes-image-2.1-flash") -> RouteTarget:
        return RouteTarget(provider="agnes_image", model=model)

    @staticmethod
    def _bytedance_target() -> RouteTarget:
        return RouteTarget(provider="bytedance", model="seedream-3-0-t2i-250415")

    def _modelscope_patches(self, fake: FakeAsyncClient, *, mark_exhausted: AsyncMock | None = None):
        stack = ExitStack()
        stack.enter_context(patch("httpx.AsyncClient", return_value=fake))
        stack.enter_context(patch("angemedia_gateway.config.MODELSCOPE_API_KEY", "ms-test"))
        stack.enter_context(patch("angemedia_gateway.config.POLL_INTERVAL", 0))
        stack.enter_context(patch("angemedia_gateway.config.MAX_POLL_TIME", 0.1))
        stack.enter_context(patch.object(modelscope_module.quota, "available", AsyncMock(return_value=True)))
        stack.enter_context(patch.object(modelscope_module.quota, "consume_one", AsyncMock()))
        stack.enter_context(patch.object(modelscope_module.quota, "mark_exhausted", mark_exhausted or AsyncMock()))
        return stack

    def _agnes_patches(self, fake: FakeAsyncClient):
        stack = ExitStack()
        stack.enter_context(patch("httpx.AsyncClient", return_value=fake))
        stack.enter_context(patch("angemedia_gateway.config.AGNES_API_KEY", "agnes-test"))
        stack.enter_context(patch("angemedia_gateway.config.AGNES_BASE_URL", "https://agnes.example.test"))
        return stack

    def _openai_patches(self, fake: FakeAsyncClient):
        stack = ExitStack()
        stack.enter_context(patch("httpx.AsyncClient", return_value=fake))
        stack.enter_context(patch("angemedia_gateway.config.OPENAI_IMAGE_API_KEY", "sk-openai-config-secret"))
        stack.enter_context(patch("angemedia_gateway.config.OPENAI_IMAGE_BASE_URL", "https://openai.example.test/v1"))
        return stack

    def _bytedance_patches(self, fake: FakeAsyncClient):
        stack = ExitStack()
        stack.enter_context(patch("httpx.AsyncClient", return_value=fake))
        stack.enter_context(patch("angemedia_gateway.config.BYTEDANCE_API_KEY", "bytedance-test-key"))
        stack.enter_context(
            patch("angemedia_gateway.config.BYTEDANCE_BASE_URL", "https://ark.example.test/api/v3")
        )
        return stack


if __name__ == "__main__":
    unittest.main()

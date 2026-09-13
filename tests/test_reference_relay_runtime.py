from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.providers.base import RouteTarget
from angemedia_gateway.providers.image.bytedance import ByteDanceImageProvider
from angemedia_gateway.providers.reference_delivery import PUBLIC_URL, ReferenceDeliveryDecision
from angemedia_gateway.providers.reference_relay import (
    ReferenceRelayUnavailable,
    prepare_reference_with_configured_relay,
)
from angemedia_gateway.schemas import ImageRequest, VideoRequest


class _Response:
    status_code = 200
    def json(self):
        return {"data": [{"url": "https://cdn.example.test/output.png"}]}


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict] = []
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return None
    async def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return _Response()


class ReferenceRelayRuntimeTest(unittest.TestCase):
    def test_relay_required_without_configured_backend_fails(self) -> None:
        with (
            patch("angemedia_gateway.providers.reference_relay.configured_reference_relay_backend", return_value=None),
            self.assertRaises(ReferenceRelayUnavailable),
        ):
            asyncio.run(prepare_reference_with_configured_relay(
                "bytedance", "/uploads/private.png", model="seedream-5-0-lite-260128"
            ))

    def test_bytedance_runtime_replaces_local_reference_before_payload(self) -> None:
        client = _Client()
        decision = ReferenceDeliveryDecision(
            kind=PUBLIC_URL,
            value="https://cdn.example.test/relay.png",
            source="relay",
        )
        req = ImageRequest(
            prompt="edit",
            model="seedream-5-lite",
            operation="edit",
            size="2K",
            reference_images=["/uploads/private.png"],
        )
        target = RouteTarget(provider="bytedance", model="seedream-5-0-lite-260128")
        runtime = SimpleNamespace(api_key="key", base_url="https://ark.example.test/api/v3")
        with (
            patch("angemedia_gateway.providers.image.bytedance.resolve_provider_runtime_config", return_value=runtime),
            patch("angemedia_gateway.providers.image.bytedance.prepare_reference_with_configured_relay", new=AsyncMock(return_value=decision)) as relay,
            patch("angemedia_gateway.providers.image.bytedance.provider_client", return_value=client),
        ):
            result = asyncio.run(ByteDanceImageProvider().generate(req, target))
        self.assertEqual(result["data"][0]["url"], "https://cdn.example.test/output.png")
        relay.assert_awaited_once_with("bytedance", "/uploads/private.png", model=target.model)
        payload = client.posts[0]["json"]
        self.assertEqual(payload["image"], "https://cdn.example.test/relay.png")

    def test_agnes_v25_runtime_relays_before_submit(self) -> None:
        provider = AgnesVideoProvider("key", "https://agnes.example.test/v1")
        decision = ReferenceDeliveryDecision(
            kind=PUBLIC_URL,
            value="https://cdn.example.test/frame.png",
            source="relay",
        )
        request_json = AsyncMock(return_value={"video_id": "video-1", "status": "queued"})
        req = VideoRequest(
            prompt="animate",
            model="agnes-video-2.5",
            mode="reference",
            images=["/uploads/frame.png"],
        )
        with (
            patch("angemedia_gateway.adapters.agnes_video.prepare_reference_with_configured_relay", new=AsyncMock(return_value=decision)) as relay,
            patch.object(provider, "_request_json", new=request_json),
        ):
            result = asyncio.run(provider.submit_task(req))
        self.assertEqual(result["task_id"], "video-1")
        relay.assert_awaited_once_with("agnes_video", "/uploads/frame.png", model="agnes-video-2.5")
        payload = request_json.await_args.kwargs["json"]
        self.assertEqual(payload["images"], ["https://cdn.example.test/frame.png"])

    def test_agnes_legacy_does_not_call_relay(self) -> None:
        provider = AgnesVideoProvider("key", "https://agnes.example.test/v1")
        relay = AsyncMock()
        request_json = AsyncMock(return_value={"video_id": "legacy-1", "status": "queued"})
        req = VideoRequest(prompt="text", model="agnes-video-v2.0")
        with (
            patch("angemedia_gateway.adapters.agnes_video.prepare_reference_with_configured_relay", new=relay),
            patch.object(provider, "_request_json", new=request_json),
        ):
            asyncio.run(provider.submit_task(req))
        relay.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

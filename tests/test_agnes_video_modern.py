"""2.5型号共用协议、媒体校验、身份与上游查询合同。"""
from __future__ import annotations

import json
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import load_provider_catalog
from angemedia_gateway.providers.errors import BackendUnavailable, ProviderQueueFull, ProviderTimeout
from angemedia_gateway.providers.reference_delivery import PUBLIC_URL, builtin_reference_delivery_capabilities
from angemedia_gateway.request_hash_builders import build_video_request_hash_payload
from angemedia_gateway.routing import build_route_response
from angemedia_gateway.schemas import RouteRequest, VideoRequest
from angemedia_gateway.services.video_job_admission import _canonical_request, parse_video_job_payload
from angemedia_gateway.video_models import AGNES_VIDEO_FLASH_MODEL, AGNES_VIDEO_V25_MODEL, agnes_video_profile

FLASH = AGNES_VIDEO_FLASH_MODEL
PAID = AGNES_VIDEO_V25_MODEL
AUDIO = "https://cdn.example.com/reference.mp3"
VIDEO = "https://cdn.example.com/reference.mp4"
IMAGE = "https://cdn.example.com/reference.png"


class AgnesModernContractTest(unittest.TestCase):
    def test_all_modes_and_profile_boundaries(self):
        for model, maximum in ((FLASH, 5), (PAID, 8)):
            with self.subTest(model=model):
                self.assertEqual(VideoRequest(prompt="x", model=model).mode, "text")
                self.assertEqual(VideoRequest(prompt="x", model=model, first_frame=IMAGE).mode, "keyframe")
                self.assertEqual(VideoRequest(prompt="x", model=model, audios=[AUDIO]).mode, "reference")
                req = VideoRequest(prompt="x", model=model, images=[IMAGE] * maximum, audios=[AUDIO] * 3)
                self.assertEqual(req.mode, "reference")
                with self.assertRaises(ValidationError):
                    VideoRequest(prompt="x", model=model, image=IMAGE, images=[IMAGE] * maximum)
                for seconds in ("4", "12"):
                    self.assertEqual(VideoRequest(prompt="x", model=model, seconds=seconds).seconds, seconds)

    def test_conflicts_unknown_fields_and_invalid_media_fail_before_submit(self):
        invalid = [
            {"size": "1080P"}, {"seconds": "13"}, {"n": 2}, {"n": True},
            {"videos": [{"url": VIDEO}]}, {"audios": [AUDIO] * 4},
            {"mode": "text", "audios": [AUDIO]}, {"mode": "keyframe", "first_frame": IMAGE, "audios": [AUDIO]},
            {"mode": "reference"}, {"mode": "reference", "first_frame": IMAGE, "audios": [AUDIO]},
            {"height": 720}, {"extra_body": {"audios": [AUDIO]}}, {"audio": AUDIO},
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                VideoRequest(prompt="x", model=FLASH, **fields)
        for url in ("file:///tmp/a", "http://127.0.0.1/x", "/uploads/a.mp3", "https://user:secret@example.com/a"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                VideoRequest(prompt="x", model=FLASH, audios=[url])
        for fields in ({"start_seconds": -1}, {"start_seconds": float("nan")}, {"require_audio": "false"}, {"other": 1}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                VideoRequest(prompt="x", model=PAID, videos=[{"url": VIDEO, **fields}])
        with self.assertRaises(ValidationError):
            VideoRequest(prompt="x", model="agnes-video-v2.0", audios=[AUDIO])
        with self.assertRaises(ValidationError):
            VideoRequest(prompt="x", provider_model="ignored")

    def test_catalog_contract_matches_validation_and_preserves_default(self):
        api = catalog_api_response(load_provider_catalog())
        for model in (FLASH, PAID):
            entry = next(item for item in api["models"] if item["provider_model"] == model)
            profile = agnes_video_profile(model)
            self.assertTrue(entry["selectable"])
            self.assertIsNone(entry["default_chain_order"])
            self.assertEqual(entry["video_contract"], profile.api_contract())
            self.assertEqual(entry["ref_input_spec"]["max_total"], profile.max_images)
            self.assertEqual(entry["param_specs"]["size"]["enum_values"], list(profile.sizes))
            self.assertEqual(builtin_reference_delivery_capabilities("agnes_video", model).methods, (PUBLIC_URL,))
            route = build_route_response(RouteRequest(prompt="视频", media_type="video", requested_model=model))
            self.assertEqual(route["model"], model)
        self.assertEqual(VideoRequest(prompt="x").model, "agnes-video-v2.0")

    def test_media_roles_and_video_options_change_hash_without_exposing_urls(self):
        def hashed(**values):
            return build_video_request_hash_payload(VideoRequest(prompt="x", model=PAID, **values)).payload
        variants = [hashed(images=[IMAGE]), hashed(audios=[IMAGE]), hashed(videos=[{"url": IMAGE}]),
                    hashed(videos=[{"url": IMAGE, "start_seconds": 1}]),
                    hashed(videos=[{"url": IMAGE, "require_audio": True}])]
        self.assertEqual(len({json.dumps(item, sort_keys=True) for item in variants}), len(variants))
        self.assertNotIn(IMAGE, repr(variants))
        for media in ({"audios": [AUDIO + "?token=private"]}, {"videos": [{"url": VIDEO + "?token=private"}]}):
            self.assertIsNone(build_video_request_hash_payload(VideoRequest(prompt="x", model=PAID, **media)).payload)
        legacy = build_video_request_hash_payload(VideoRequest(prompt="x")).payload
        self.assertNotIn("audio_reference_inputs", legacy)
        self.assertNotIn("video_reference_inputs", legacy)

    def test_queue_roundtrip_preserves_media_and_accepts_old_payload(self):
        for model, media in ((FLASH, {"audios": [AUDIO]}), (PAID, {"videos": [{"url": VIDEO, "start_seconds": 2, "require_audio": True}]})):
            req = VideoRequest(prompt="x", model=model, **media)
            payload = _canonical_request(req)
            restored, mode = parse_video_job_payload({"schema_version": 1, "pipeline": "video", "provider": "agnes_video", "input_mode": "reference", "request": payload})
            self.assertEqual(restored.model, model)
            self.assertEqual(restored.audios, req.audios)
            self.assertEqual(restored.videos, req.videos)
            self.assertEqual(mode, "reference")
        old, _ = parse_video_job_payload({"schema_version": 1, "pipeline": "video", "provider": "agnes_video", "request": {"prompt": "old", "model": PAID}})
        self.assertEqual(old.mode, "text")


class AgnesModernTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_deadline_is_shorter_than_video_poll_timeout(self):
        provider = AgnesVideoProvider("test-key", "https://agnes.example.com/v1", timeout=1800)
        provider._read_json = AsyncMock(return_value={})
        await provider._request_json("POST", "/videos", operation="submit")
        await provider._request_json("GET", "/agnesapi", operation="poll")
        self.assertEqual([call.kwargs["timeout"] for call in provider._read_json.await_args_list], [45.0, 1800])

        async def slow_ack(*args, **kwargs):
            await asyncio.sleep(1)

        provider.timeout = .01
        provider._read_json = AsyncMock(side_effect=slow_ack)
        with self.assertRaises(ProviderTimeout):
            await provider._request_json("POST", "/videos", operation="submit")
        self.assertEqual(provider._read_json.await_count, 1)

    async def test_submit_recognizes_only_unambiguous_queue_full_receipts(self):
        provider = AgnesVideoProvider("test-key", "https://agnes.example.com/v1")
        full = {"code": "video_queue_full", "message": "PRIVATE_UPSTREAM_BODY", "data": None}
        for phase, status, body, expected in (
            ("submit", 503, full, ProviderQueueFull),
            ("submit", 503, {"message": "video_queue_full"}, BackendUnavailable),
            ("submit", 503, "video_queue_full", BackendUnavailable),
            ("submit", 503, {**full, "video_id": "existing-task"}, BackendUnavailable),
            ("submit", 503, {**full, "data": {"task_id": "existing-task"}}, BackendUnavailable),
            ("submit", 500, full, BackendUnavailable),
            ("poll", 503, full, BackendUnavailable),
        ):
            with self.subTest(phase=phase, status=status, body=body):
                calls = []
                def reply(request):
                    calls.append(request.method)
                    if isinstance(body, str):
                        return httpx.Response(status, text=body)
                    return httpx.Response(status, json=body)

                client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
                with patch("angemedia_gateway.adapters.agnes_video.provider_client", return_value=client):
                    with self.assertRaises(expected) as caught:
                        if phase == "submit":
                            await provider.submit_task(VideoRequest(prompt="x", model=FLASH))
                        else:
                            await provider.poll_task("existing-task", model_name=FLASH)
                self.assertIs(type(caught.exception), expected)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("PRIVATE_UPSTREAM_BODY", str(caught.exception))
                self.assertEqual(calls, ["POST" if phase == "submit" else "GET"])

    async def test_submit_and_query_keep_the_exact_model_and_video_id(self):
        for model, media in ((FLASH, {"audios": [AUDIO]}), (PAID, {"videos": [{"url": VIDEO, "start_seconds": 2, "require_audio": True}]})):
            provider = AgnesVideoProvider("test-key", "https://agnes.example.com/v1")
            provider._request_json = AsyncMock(side_effect=[
                {"id": "task-internal", "video_id": "public-video-id", "status": "queued"},
                {"status": "completed", "url": "https://cdn.example.com/result.mp4"},
            ])
            req = VideoRequest(prompt="x", model=model, **media)
            submitted = await provider.submit_task(req)
            self.assertEqual(submitted["task_id"], "public-video-id")
            sent = provider._request_json.call_args_list[0].kwargs["json"]
            self.assertEqual(sent["model"], model)
            for name, value in media.items():
                self.assertEqual(sent[name], value)
            result = await provider.poll_task(submitted["task_id"], model_name=model)
            self.assertEqual(provider._request_json.call_args.kwargs["params"], {"video_id": "public-video-id", "model_name": model})
            self.assertEqual(result["video_url"], "https://cdn.example.com/result.mp4")

    async def test_flash_image_relay_reuses_profile_and_does_not_relay_audio(self):
        from angemedia_gateway.providers.reference_delivery import ReferenceDeliveryDecision
        provider = AgnesVideoProvider("test-key", "https://agnes.example.com/v1")
        req = VideoRequest(prompt="x", model=FLASH, images=["/uploads/frame.png"], audios=[AUDIO])
        with patch("angemedia_gateway.adapters.agnes_video.prepare_reference_with_configured_relay", new=AsyncMock(return_value=ReferenceDeliveryDecision(PUBLIC_URL, IMAGE, "relay"))) as relay:
            prepared = await provider._prepare_runtime_request(req)
        relay.assert_awaited_once_with("agnes_video", "/uploads/frame.png", model=FLASH)
        payload = provider.build_payload(prepared)
        self.assertEqual(payload["images"], [IMAGE])
        self.assertEqual(payload["audios"], [AUDIO])


if __name__ == "__main__":
    unittest.main()

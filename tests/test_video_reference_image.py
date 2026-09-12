"""Agnes video reference-image submission contracts."""
from __future__ import annotations

import json
import base64
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import angemedia_gateway.config as C  # noqa: E402
from angemedia_gateway.adapters.agnes_video import AgnesVideoProvider  # noqa: E402
from angemedia_gateway.request_hash_builders import build_video_request_hash_payload  # noqa: E402
from angemedia_gateway.routes import media as media_routes  # noqa: E402
from angemedia_gateway.schemas import VideoRequest  # noqa: E402
from angemedia_gateway.services.video_generation import (  # noqa: E402
    InvalidVideoReference,
    create_video,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"reference-image"


class AgnesVideoReferenceImageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="video-reference-test-"))
        self.upload_dir = self.tmp_dir / "uploads"
        self.output_dir = self.tmp_dir / "generated"
        self.upload_dir.mkdir()
        self.output_dir.mkdir()
        self.original_upload = C.UPLOAD_DIR
        self.original_output = C.OUTPUT_DIR
        C.UPLOAD_DIR = self.upload_dir
        C.OUTPUT_DIR = self.output_dir
        (self.upload_dir / "upload-ref.png").write_bytes(PNG_BYTES)
        (self.output_dir / "asset-ref.png").write_bytes(PNG_BYTES)
        self.provider = AgnesVideoProvider("test-key", "https://agnes.example.test/v1")

    def tearDown(self) -> None:
        C.UPLOAD_DIR = self.original_upload
        C.OUTPUT_DIR = self.original_output
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_text_to_video_payload_remains_without_image(self) -> None:
        payload = self.provider.build_payload(VideoRequest(prompt="text only", model="agnes-video-v2.0"))
        self.assertNotIn("image", payload)
        self.assertEqual(payload["prompt"], "text only")

    def test_upload_and_generated_assets_materialize_to_agnes_image_field(self) -> None:
        for reference in ("/uploads/upload-ref.png", "/generated/asset-ref.png"):
            with self.subTest(reference=reference):
                payload = self.provider.build_payload(VideoRequest(prompt="animate", model="agnes-video-v2.0", image=reference))
                self.assertIn("image", payload)
                self.assertFalse(payload["image"].startswith("data:image/"))
                self.assertEqual(base64.b64decode(payload["image"], validate=True), PNG_BYTES)
                self.assertNotIn(reference, payload["image"])

    def test_arbitrary_reference_sources_are_rejected(self) -> None:
        invalid_sources = (
            "https://cdn.example/ref.png?token=signed-secret",
            "http://127.0.0.1/private.png",
            "D:/private/ref.png",
            "file:///etc/passwd",
            "/uploads/../private.png",
            "/generated/../../private.png",
            "data:image/png;base64,iVBORw0KGgo=",
        )
        for reference in invalid_sources:
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.provider.build_payload(VideoRequest(prompt="animate", model="agnes-video-v2.0", image=reference))

    def test_non_image_and_missing_controlled_files_are_not_materialized(self) -> None:
        (self.upload_dir / "fake.png").write_bytes(b"not an image")
        for reference in ("/uploads/fake.png", "/generated/missing.png"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.provider.build_payload(VideoRequest(prompt="animate", model="agnes-video-v2.0", image=reference))

    def test_request_hash_uses_reference_identity_not_bytes_or_secrets(self) -> None:
        request = VideoRequest(prompt="animate", model="agnes-video-v2.0", image="/uploads/upload-ref.png")
        result = build_video_request_hash_payload(request)
        serialized = json.dumps(result.payload, sort_keys=True)
        self.assertIsNotNone(result.payload)
        self.assertIn("/uploads/upload-ref.png", serialized)
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("reference-image", serialized)
        self.assertNotIn("test-key", serialized)
        self.assertNotIn("token=", serialized)

    def test_v25_text_payload_uses_current_contract_without_legacy_frame_fields(self) -> None:
        request = VideoRequest(prompt="cinematic sunrise", model="agnes-video-2.5")
        payload = self.provider.build_payload(request)
        self.assertEqual(payload, {
            "model": "agnes-video-2.5",
            "prompt": "cinematic sunrise",
            "seconds": "5",
            "mode": "text",
            "size": "720P",
            "aspect_ratio": "16:9",
        })
        for legacy in ("width", "height", "num_frames", "frame_rate", "num_inference_steps"):
            self.assertNotIn(legacy, payload)

    def test_v25_keyframe_and_reference_payload_use_public_urls_only(self) -> None:
        first = "https://cdn.example.test/first.png"
        last = "https://cdn.example.test/last.png"
        refs = ["https://cdn.example.test/ref-1.png", "https://cdn.example.test/ref-2.png"]
        with patch(
            "angemedia_gateway.adapters.agnes_video.validate_public_http_url",
            side_effect=lambda value: value,
        ) as validate_url:
            keyframe = self.provider.build_payload(VideoRequest(
                prompt="keyframe",
                model="agnes-video-2.5",
                mode="keyframe",
                first_frame=first,
                last_frame=last,
                seconds="8",
                size="1080P",
                aspect_ratio="4:3",
            ))
            reference = self.provider.build_payload(VideoRequest(
                prompt="reference",
                model="agnes-video-2.5",
                mode="reference",
                images=refs,
                seed=42,
            ))
        self.assertEqual(keyframe["first_frame"], first)
        self.assertEqual(keyframe["last_frame"], last)
        self.assertNotIn("images", keyframe)
        self.assertEqual(reference["images"], refs)
        self.assertEqual(reference["seed"], 42)
        self.assertNotIn("first_frame", reference)
        self.assertEqual(validate_url.call_count, 4)

    def test_v25_schema_rejects_legacy_video_fields_and_invalid_reference_modes(self) -> None:
        with self.assertRaises(ValueError):
            VideoRequest(prompt="bad", model="agnes-video-2.5", width=1024)
        with self.assertRaises(ValueError):
            VideoRequest(prompt="bad", model="agnes-video-2.5", mode="reference")
        with self.assertRaises(ValueError):
            VideoRequest(
                prompt="bad",
                model="agnes-video-2.5",
                mode="keyframe",
                first_frame="https://cdn.example.test/a.png",
                images=["https://cdn.example.test/b.png"],
            )
        with self.assertRaises(ValueError):
            VideoRequest(prompt="bad", model="agnes-video-2.5", seconds="13")

    def test_submit_normalization_prefers_current_video_id(self) -> None:
        result = self.provider.normalize_submit({
            "id": "legacy-id",
            "task_id": "legacy-task-id",
            "video_id": "current-video-id",
            "status": "queued",
        })
        self.assertEqual(result, {"task_id": "current-video-id", "status": "queued"})

    def test_poll_normalization_accepts_current_metadata_url(self) -> None:
        result = self.provider.normalize_poll({
            "video_id": "current-video-id",
            "status": "completed",
            "metadata": {"url": "https://platform-outputs.agnes-ai.space/videos/result.mp4"},
        }, "current-video-id")
        self.assertEqual(
            result["video_url"],
            "https://platform-outputs.agnes-ai.space/videos/result.mp4",
        )

    def test_poll_normalization_ignores_non_mapping_metadata_and_keeps_legacy_url(self) -> None:
        result = self.provider.normalize_poll({
            "status": "completed",
            "metadata": "unexpected",
            "output_url": "https://legacy.example.test/result.mp4",
        }, "legacy-task")
        self.assertEqual(result["video_url"], "https://legacy.example.test/result.mp4")

    def test_submit_normalization_drops_raw_provider_fields(self) -> None:
        secret = "sk-provider-secret"
        result = self.provider.normalize_submit({
            "task_id": "i2v-task-001",
            "status": "queued",
            "Authorization": f"Bearer {secret}",
            "api_key": secret,
            "raw_provider_body": {"secret": secret},
            "signed_url": "https://cdn.example/result?token=private",
        })
        serialized = json.dumps(result, sort_keys=True)
        self.assertEqual(result, {"task_id": "i2v-task-001", "status": "queued"})
        self.assertNotIn(secret, serialized)
        self.assertNotIn("token=", serialized)


class AgnesVideoPollingEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_poll_uses_recommended_agnesapi_endpoint(self) -> None:
        provider = AgnesVideoProvider("test-key", "https://apihub.agnes-ai.com/v1")
        provider._request_json = AsyncMock(return_value={
            "status": "completed",
            "metadata": {"url": "https://platform-outputs.agnes-ai.space/videos/result.mp4"},
        })
        result = await provider.poll_task("video-current")
        provider._request_json.assert_awaited_once_with(
            "GET",
            "https://apihub.agnes-ai.com/agnesapi",
            operation="poll",
            params={"video_id": "video-current"},
            headers={"Authorization": "Bearer test-key"},
        )
        self.assertIn("video_url", result)

    async def test_v25_poll_includes_model_name_on_recommended_endpoint(self) -> None:
        provider = AgnesVideoProvider("test-key", "https://apihub.agnes-ai.com/v1")
        provider._request_json = AsyncMock(return_value={"status": "running"})
        await provider.poll_task("video-v25-current", model_name="agnes-video-2.5")
        provider._request_json.assert_awaited_once_with(
            "GET",
            "https://apihub.agnes-ai.com/agnesapi",
            operation="poll",
            params={"video_id": "video-v25-current", "model_name": "agnes-video-2.5"},
            headers={"Authorization": "Bearer test-key"},
        )

    async def test_poll_falls_back_to_legacy_endpoint_for_compatible_validation_errors(self) -> None:
        from angemedia_gateway.providers.errors import ProviderValidationError

        provider = AgnesVideoProvider("test-key", "https://apihub.agnes-ai.com/v1")
        provider._request_json = AsyncMock(side_effect=[
            ProviderValidationError("not found", status_code=404),
            {"status": "completed", "output_url": "https://legacy.example.test/result.mp4"},
        ])
        result = await provider.poll_task("video-legacy")
        self.assertEqual(provider._request_json.await_count, 2)
        self.assertEqual(
            provider._request_json.await_args_list[1].args[1],
            "https://apihub.agnes-ai.com/v1/videos/video-legacy",
        )
        self.assertEqual(result["video_url"], "https://legacy.example.test/result.mp4")

    async def test_opaque_video_id_never_falls_back_to_legacy_path(self) -> None:
        from angemedia_gateway.providers.errors import ProviderValidationError

        opaque_id = "video/current:opaque.id"
        provider = AgnesVideoProvider("test-key", "https://apihub.agnes-ai.com/v1")
        provider._request_json = AsyncMock(
            side_effect=ProviderValidationError("not found", status_code=404)
        )
        with self.assertRaises(ProviderValidationError):
            await provider.poll_task(opaque_id)
        provider._request_json.assert_awaited_once_with(
            "GET",
            "https://apihub.agnes-ai.com/agnesapi",
            operation="poll",
            params={"video_id": opaque_id},
            headers={"Authorization": "Bearer test-key"},
        )


class VideoExecutionExternalIdTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_and_poll_accept_opaque_provider_id(self) -> None:
        from angemedia_gateway.services.video_execution import VideoExecutionService

        opaque_id = "video/current:opaque.id"
        provider = AsyncMock()
        provider.submit_task.return_value = {"task_id": opaque_id, "status": "queued"}
        provider.poll_task.return_value = {"status": "running"}
        executor = VideoExecutionService(
            provider=provider,
            provider_enabled_func=lambda _provider: True,
        )

        submitted = await executor.submit(VideoRequest(prompt="opaque id"))
        self.assertEqual(submitted.task_id, opaque_id)
        polled = await executor.poll(opaque_id)
        self.assertEqual(polled.task_id, opaque_id)
        provider.poll_task.assert_awaited_once_with(opaque_id)

    async def test_submit_rejects_control_characters_in_provider_id(self) -> None:
        from angemedia_gateway.services.video_execution import VideoExecutionService

        provider = AsyncMock()
        provider.submit_task.return_value = {"task_id": "bad\nid", "status": "queued"}
        executor = VideoExecutionService(
            provider=provider,
            provider_enabled_func=lambda _provider: True,
        )
        with self.assertRaises(ValueError):
            await executor.submit(VideoRequest(prompt="invalid id"))


class VideoReferenceServiceBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_reference_stops_before_provider_submit(self) -> None:
        provider = AsyncMock()
        with self.assertRaises(InvalidVideoReference):
            await create_video(
                VideoRequest(prompt="animate", image="http://127.0.0.1/private.png"),
                agnes_video_provider=provider,
                builtin_provider_enabled_func=lambda _provider_id: True,
            )
        provider.submit_task.assert_not_awaited()
        provider.generate_video.assert_not_awaited()

    async def test_route_maps_invalid_reference_without_echoing_signed_url(self) -> None:
        signed_url = "https://cdn.example/ref.png?token=do-not-echo"
        error = InvalidVideoReference("reference image must be an uploaded or generated image asset")
        with patch.object(media_routes.media_service, "create_video", AsyncMock(side_effect=error)):
            with self.assertRaises(HTTPException) as caught:
                await media_routes._create_video_response(VideoRequest(prompt="animate", image=signed_url))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertNotIn(signed_url, json.dumps(caught.exception.detail))
        self.assertNotIn("do-not-echo", json.dumps(caught.exception.detail))

if __name__ == "__main__":
    unittest.main()

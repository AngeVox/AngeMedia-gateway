"""request_hash media payload builder tests."""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.request_hash import compute_request_hash  # noqa: E402
from angemedia_gateway.request_hash_builders import (  # noqa: E402
    build_image_request_hash_payload,
    build_video_request_hash_payload,
)
from angemedia_gateway.schemas import ImageRequest, VideoRequest  # noqa: E402


def _payload_hash(payload: dict) -> str:
    return compute_request_hash(payload)


def _image_payload(req: ImageRequest, **kwargs) -> dict:
    result = build_image_request_hash_payload(
        req,
        provider_mode=kwargs.pop("provider_mode", "builtin"),
        resolved_chain=kwargs.pop("resolved_chain", [{"provider": "siliconflow", "model": "kolors"}]),
        **kwargs,
    )
    assert result.payload is not None, result.unsupported_reason
    return result.payload


def _video_payload(req: VideoRequest) -> dict:
    result = build_video_request_hash_payload(req)
    assert result.payload is not None, result.unsupported_reason
    return result.payload


class ImageRequestHashBuilderTest(unittest.TestCase):
    def test_same_logical_input_has_stable_payload_and_hash(self) -> None:
        req_a = ImageRequest(prompt="cat", model="agnes-image", extra_body={"steps": 20, "sampler": "euler"})
        req_b = ImageRequest(prompt="cat", model="agnes-image", extra_body={"sampler": "euler", "steps": 20})

        payload_a = _image_payload(req_a)
        payload_b = _image_payload(req_b)

        self.assertEqual(payload_a, payload_b)
        self.assertEqual(_payload_hash(payload_a), _payload_hash(payload_b))

    def test_image_core_field_differences_change_hash(self) -> None:
        base = ImageRequest(
            prompt="cat",
            model="agnes-image",
            size="1024x1024",
            response_format="url",
            quality="standard",
            safe=True,
            negative_prompt="blur",
            seed=1,
        )
        base_hash = _payload_hash(_image_payload(base))
        variants = [
            ImageRequest(prompt="dog", model="agnes-image", size="1024x1024", quality="standard", safe=True, negative_prompt="blur", seed=1),
            ImageRequest(prompt="cat", model="agnes-2.1", size="1024x1024", quality="standard", safe=True, negative_prompt="blur", seed=1),
            ImageRequest(prompt="cat", model="agnes-image", size="960x1280", quality="standard", safe=True, negative_prompt="blur", seed=1),
            ImageRequest(prompt="cat", model="agnes-image", size="1024x1024", quality="hd", safe=True, negative_prompt="blur", seed=1),
            ImageRequest(prompt="cat", model="agnes-image", size="1024x1024", quality="standard", safe=False, negative_prompt="blur", seed=1),
            ImageRequest(prompt="cat", model="agnes-image", size="1024x1024", quality="standard", safe=True, negative_prompt="noise", seed=1),
            ImageRequest(prompt="cat", model="agnes-image", size="1024x1024", quality="standard", safe=True, negative_prompt="blur", seed=2),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(base_hash, _payload_hash(_image_payload(variant)))

    def test_builtin_fallback_chain_order_affects_hash(self) -> None:
        req = ImageRequest(prompt="cat")
        first = _image_payload(
            req,
            resolved_chain=[
                {"provider": "siliconflow", "model": "kolors"},
                {"provider": "pollinations", "model": "zimage"},
            ],
        )
        second = _image_payload(
            req,
            resolved_chain=[
                {"provider": "pollinations", "model": "zimage"},
                {"provider": "siliconflow", "model": "kolors"},
            ],
        )
        self.assertNotEqual(_payload_hash(first), _payload_hash(second))

    def test_custom_provider_identity_affects_hash_and_omits_secret_config(self) -> None:
        req = ImageRequest(prompt="cat", model="custom:abc")
        first = _image_payload(
            req,
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="abc",
            custom_default_model="image-a",
        )
        second = _image_payload(
            req,
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="def",
            custom_default_model="image-a",
        )
        third = _image_payload(
            req,
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="abc",
            custom_default_model="image-b",
        )
        rendered = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("base_url", rendered)
        self.assertNotIn("api_key", rendered)
        self.assertNotIn("status_url", rendered)
        self.assertNotIn("quota_url", rendered)
        self.assertNotEqual(_payload_hash(first), _payload_hash(second))
        self.assertNotEqual(_payload_hash(first), _payload_hash(third))

    def test_custom_provider_model_override_affects_hash_and_payload(self) -> None:
        """provider_model selects the real custom upstream model and must affect dedupe."""
        first = _image_payload(
            ImageRequest(prompt="cat", model="custom:abc", provider_model="image-a"),
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="abc",
            custom_default_model="default-image",
        )
        second = _image_payload(
            ImageRequest(prompt="cat", model="custom:abc", provider_model="image-b"),
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="abc",
            custom_default_model="default-image",
        )

        self.assertEqual(first["provider_model"], "image-a")
        self.assertEqual(second["provider_model"], "image-b")
        self.assertNotEqual(_payload_hash(first), _payload_hash(second))

        rendered = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("api_key", rendered)
        self.assertNotIn("authorization", rendered.lower())
        self.assertNotIn("secret", rendered.lower())
        self.assertNotIn("raw body", rendered.lower())

    def test_empty_custom_provider_model_override_is_stable_like_missing(self) -> None:
        """Missing and empty provider_model should both mean use the stored custom default_model."""
        missing = _image_payload(
            ImageRequest(prompt="cat", model="custom:abc"),
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="abc",
            custom_default_model="default-image",
        )
        empty = _image_payload(
            ImageRequest(prompt="cat", model="custom:abc", provider_model=""),
            provider_mode="custom",
            resolved_chain=None,
            custom_provider_id="abc",
            custom_default_model="default-image",
        )

        self.assertEqual(missing, empty)
        self.assertNotIn("provider_model", missing)
        self.assertEqual(_payload_hash(missing), _payload_hash(empty))

    def test_unknown_extra_field_is_not_included(self) -> None:
        payload = _image_payload(ImageRequest(prompt="cat", whimsical="ignored"))
        self.assertNotIn("whimsical", json.dumps(payload))

    def test_operation_fields_affect_hash_as_core_image_semantics(self) -> None:
        base = ImageRequest(prompt="cat", model="kolors", size="1024x1024", steps=20, guidance=7.5, seed=1)
        variants = [
            ImageRequest(prompt="cat", model="kolors", size="1024x1024", steps=21, guidance=7.5, seed=1),
            ImageRequest(prompt="cat", model="kolors", size="1024x1024", steps=20, guidance=8.5, seed=1),
            ImageRequest(prompt="cat", model="kolors", size="1024x1024", steps=20, guidance=7.5, seed=2),
        ]
        base_payload = _image_payload(base)
        self.assertEqual(base_payload["steps"], 20)
        self.assertEqual(base_payload["guidance"], 7.5)
        self.assertEqual(base_payload["seed"], 1)
        self.assertNotIn("extra", base_payload)
        base_hash = _payload_hash(base_payload)
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(base_hash, _payload_hash(_image_payload(variant)))

    def test_safe_reference_list_order_affects_hash(self) -> None:
        first = _image_payload(ImageRequest(prompt="cat", images=["/uploads/a.png", "/uploads/b.png"]))
        second = _image_payload(ImageRequest(prompt="cat", images=["/uploads/b.png", "/uploads/a.png"]))
        self.assertNotEqual(_payload_hash(first), _payload_hash(second))

    def test_same_origin_generated_and_upload_paths_are_accepted(self) -> None:
        payload = _image_payload(ImageRequest(prompt="cat", images=["/uploads/a.png", "/generated/b.png"]))
        self.assertEqual(
            payload["reference_inputs"],
            [
                {"type": "path", "path": "/uploads/a.png"},
                {"type": "path", "path": "/generated/b.png"},
            ],
        )

    def test_image_reference_changes_hash_and_remote_url_is_digest_only(self) -> None:
        first = _image_payload(ImageRequest(prompt="cat", image="https://example.com/a.png"))
        second = _image_payload(ImageRequest(prompt="cat", image="https://example.com/b.png"))

        self.assertNotEqual(_payload_hash(first), _payload_hash(second))
        self.assertEqual(first["reference_inputs"][0]["type"], "url_sha256")
        rendered = json.dumps(first)
        self.assertNotIn("https://example.com/a.png", rendered)
        self.assertIn("sha256:", rendered)

    def test_data_url_becomes_digest_not_full_base64(self) -> None:
        content = b"abc"
        encoded = base64.b64encode(content).decode("ascii")
        payload = _image_payload(ImageRequest(prompt="cat", image=f"data:image/png;base64,{encoded}"))
        rendered = json.dumps(payload)
        digest = hashlib.sha256(content).hexdigest()
        self.assertIn(f"sha256:{digest}", rendered)
        self.assertNotIn(encoded, rendered)

    def test_remote_url_with_query_is_unsupported(self) -> None:
        result = build_image_request_hash_payload(
            ImageRequest(prompt="cat", image="https://example.com/a.png?token=secret"),
            provider_mode="builtin",
            resolved_chain=[{"provider": "siliconflow", "model": "kolors"}],
        )
        self.assertIsNone(result.payload)
        self.assertEqual(result.unsupported_reason, "unsupported_reference_identity")

    def test_raw_local_path_is_unsupported(self) -> None:
        result = build_image_request_hash_payload(
            ImageRequest(prompt="cat", image="D:/tmp/a.png"),
            provider_mode="builtin",
            resolved_chain=[{"provider": "siliconflow", "model": "kolors"}],
        )
        self.assertIsNone(result.payload)
        self.assertEqual(result.unsupported_reason, "unsupported_reference_identity")

    def test_secret_like_extra_field_fails_fast(self) -> None:
        with self.assertRaises(ValueError):
            build_image_request_hash_payload(
                ImageRequest(prompt="cat", providerToken="secret"),
                provider_mode="builtin",
                resolved_chain=[{"provider": "siliconflow", "model": "kolors"}],
            )


class VideoRequestHashBuilderTest(unittest.TestCase):
    def test_video_excludes_wait_for_completion(self) -> None:
        first = _video_payload(VideoRequest(prompt="cat", wait_for_completion=False))
        second = _video_payload(VideoRequest(prompt="cat", wait_for_completion=True))
        self.assertNotIn("wait_for_completion", first)
        self.assertEqual(_payload_hash(first), _payload_hash(second))

    def test_video_core_field_differences_change_hash(self) -> None:
        base = VideoRequest(
            prompt="cat",
            model="agnes-video-v2.0",
            mode="keyframes",
            height=768,
            width=1152,
            num_frames=121,
            frame_rate=24,
            negative_prompt="blur",
            seed=1,
            num_inference_steps=20,
        )
        base_hash = _payload_hash(_video_payload(base))
        variants = [
            VideoRequest(prompt="dog", model="agnes-video-v2.0", mode="keyframes", seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.1", mode="keyframes", seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", mode="motion", seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", height=512, seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", width=768, seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", num_frames=81, seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", frame_rate=30, seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", negative_prompt="noise", seed=1),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", seed=2),
            VideoRequest(prompt="cat", model="agnes-video-v2.0", num_inference_steps=30, seed=1),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(base_hash, _payload_hash(_video_payload(variant)))

    def test_video_task_and_job_ids_are_not_included(self) -> None:
        payload = _video_payload(VideoRequest(prompt="cat"))
        rendered = json.dumps(payload)
        self.assertNotIn("task_id", rendered)
        self.assertNotIn("job_id", rendered)

    def test_video_unsupported_extra_body_returns_none(self) -> None:
        result = build_video_request_hash_payload(VideoRequest(prompt="cat", extra_body={"motion": "pan"}))
        self.assertIsNone(result.payload)
        self.assertEqual(result.unsupported_reason, "unsupported_video_extra_body")

    def test_video_safe_reference_path_is_accepted(self) -> None:
        payload = _video_payload(VideoRequest(prompt="cat", images=["/uploads/a.png", "/generated/b.png"]))
        self.assertEqual(
            payload["reference_inputs"],
            [
                {"type": "path", "path": "/uploads/a.png"},
                {"type": "path", "path": "/generated/b.png"},
            ],
        )

    def test_video_hash_ignores_image_provider_model_override_field(self) -> None:
        """provider_model is image/custom-only and must not change video request hashing."""
        base = _video_payload(VideoRequest(prompt="cat", model="agnes-video-v2.0"))
        with_extra = _video_payload(VideoRequest(prompt="cat", model="agnes-video-v2.0", provider_model="image-only"))

        self.assertEqual(base, with_extra)
        self.assertEqual(_payload_hash(base), _payload_hash(with_extra))


class RequestHashBuilderPurityTest(unittest.TestCase):
    def test_builder_module_has_no_runtime_provider_env_db_or_time_dependency(self) -> None:
        import angemedia_gateway.request_hash_builders as builders

        source = inspect.getsource(builders)
        forbidden_snippets = [
            "os.environ",
            "db_connect",
            "httpx",
            "time.",
            "random.",
            "uuid.",
            "PROVIDERS",
        ]
        for snippet in forbidden_snippets:
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.reference_delivery import (  # noqa: E402
    BASE64,
    DATA_URL,
    MULTIPART,
    PUBLIC_URL,
    RELAY_REQUIRED,
    ReferenceDeliveryCapabilities,
    builtin_reference_delivery_capabilities,
    decide_reference_delivery,
)


PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


class ReferenceDeliveryPolicyTest(unittest.TestCase):
    def test_builtin_profiles_match_current_provider_contracts(self) -> None:
        self.assertEqual(builtin_reference_delivery_capabilities("openai_image").methods, (MULTIPART,))
        self.assertEqual(
            builtin_reference_delivery_capabilities("modelscope").methods,
            (DATA_URL, PUBLIC_URL),
        )
        self.assertEqual(
            builtin_reference_delivery_capabilities("siliconflow").methods,
            (DATA_URL, PUBLIC_URL),
        )
        self.assertEqual(
            builtin_reference_delivery_capabilities("agnes_image").methods,
            (DATA_URL, PUBLIC_URL),
        )
        self.assertEqual(
            builtin_reference_delivery_capabilities("pollinations").methods,
            (DATA_URL, PUBLIC_URL),
        )
        self.assertEqual(builtin_reference_delivery_capabilities("bytedance").methods, (PUBLIC_URL,))
        self.assertEqual(builtin_reference_delivery_capabilities("agnes_video").methods, (BASE64,))
        self.assertEqual(
            builtin_reference_delivery_capabilities("agnes_video", "agnes-video-2.5").methods,
            (PUBLIC_URL,),
        )
        self.assertEqual(
            builtin_reference_delivery_capabilities("agnes_video", "agnes-video-v2.5").methods,
            (PUBLIC_URL,),
        )

    def test_gateway_asset_prefers_multipart_without_public_url(self) -> None:
        decision = decide_reference_delivery(
            "/uploads/example.png",
            ReferenceDeliveryCapabilities.from_methods([MULTIPART, PUBLIC_URL]),
        )
        self.assertEqual(decision.kind, MULTIPART)
        self.assertEqual(decision.source, "gateway_asset")
        self.assertEqual(decision.value, "/uploads/example.png")

    def test_gateway_asset_materializes_to_data_url_when_supported(self) -> None:
        with patch(
            "angemedia_gateway.providers.reference_delivery.materialize_gateway_image_reference",
            return_value=PNG_DATA_URL,
        ):
            decision = decide_reference_delivery(
                "/uploads/example.png",
                ReferenceDeliveryCapabilities.from_methods([DATA_URL, PUBLIC_URL]),
            )
        self.assertEqual(decision.kind, DATA_URL)
        self.assertEqual(decision.value, PNG_DATA_URL)

    def test_gateway_asset_can_become_bare_base64_for_agnes_video_style_payload(self) -> None:
        with patch(
            "angemedia_gateway.providers.reference_delivery.materialize_gateway_image_reference",
            return_value=PNG_DATA_URL,
        ):
            decision = decide_reference_delivery(
                "/uploads/example.png",
                ReferenceDeliveryCapabilities.from_methods([BASE64]),
            )
        self.assertEqual(decision.kind, BASE64)
        self.assertEqual(decision.value, "iVBORw0KGgo=")

    def test_public_url_only_provider_marks_gateway_asset_as_relay_required(self) -> None:
        decision = decide_reference_delivery(
            "/uploads/example.png",
            ReferenceDeliveryCapabilities.from_methods([PUBLIC_URL]),
        )
        self.assertEqual(decision.kind, RELAY_REQUIRED)
        self.assertEqual(decision.source, "gateway_asset")

    def test_data_url_for_public_url_only_provider_also_requires_relay(self) -> None:
        decision = decide_reference_delivery(
            PNG_DATA_URL,
            ReferenceDeliveryCapabilities.from_methods([PUBLIC_URL]),
        )
        self.assertEqual(decision.kind, RELAY_REQUIRED)
        self.assertEqual(decision.source, "data_url")

    def test_remote_url_is_preserved_only_when_provider_accepts_public_url(self) -> None:
        remote = "https://images.example.test/ref.png"
        with patch(
            "angemedia_gateway.providers.reference_delivery.validate_provider_reference_url",
            return_value=remote,
        ) as validator:
            decision = decide_reference_delivery(
                remote,
                ReferenceDeliveryCapabilities.from_methods([PUBLIC_URL]),
            )
        validator.assert_called_once_with(remote)
        self.assertEqual(decision.kind, PUBLIC_URL)
        self.assertEqual(decision.value, remote)
        self.assertEqual(decision.source, "remote_url")

    def test_remote_url_is_not_downloaded_or_converted_for_data_url_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not accept remote"):
            decide_reference_delivery(
                "https://images.example.test/ref.png",
                ReferenceDeliveryCapabilities.from_methods([DATA_URL]),
            )

    def test_unsupported_profile_or_method_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            builtin_reference_delivery_capabilities("unknown-provider")
        with self.assertRaises(ValueError):
            ReferenceDeliveryCapabilities.from_methods(["magic_upload"])
        with self.assertRaises(ValueError):
            ReferenceDeliveryCapabilities.from_methods([])


if __name__ == "__main__":
    unittest.main()

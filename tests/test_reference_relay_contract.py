from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.reference_delivery import (  # noqa: E402
    PUBLIC_URL,
    RELAY_REQUIRED,
    ReferenceDeliveryDecision,
)
from angemedia_gateway.providers.reference_relay import (  # noqa: E402
    DEFAULT_RELAY_TTL_SECONDS,
    RelayPublication,
    ReferenceRelayUnavailable,
    fulfill_relay_decision,
)


class ReferenceRelayContractTest(unittest.TestCase):
    def test_non_relay_decision_is_returned_without_backend_call(self) -> None:
        decision = ReferenceDeliveryDecision(
            kind=PUBLIC_URL,
            value="https://images.example.test/ref.png",
            source="remote_url",
        )
        backend = AsyncMock()
        result = asyncio.run(fulfill_relay_decision(decision, backend=backend))
        self.assertIs(result, decision)
        backend.publish_reference.assert_not_called()

    def test_relay_required_without_backend_fails_safely(self) -> None:
        decision = ReferenceDeliveryDecision(
            kind=RELAY_REQUIRED,
            value="/uploads/private.png",
            source="gateway_asset",
        )
        with self.assertRaisesRegex(ReferenceRelayUnavailable, "configured reference-image relay"):
            asyncio.run(fulfill_relay_decision(decision, backend=None))

    def test_backend_receives_gateway_reference_and_bounded_ttl(self) -> None:
        decision = ReferenceDeliveryDecision(
            kind=RELAY_REQUIRED,
            value="/uploads/private.png",
            source="gateway_asset",
        )
        backend = AsyncMock()
        backend.publish_reference.return_value = RelayPublication(
            url="https://relay.example.test/opaque-token",
            expires_at="2026-09-12T12:00:00Z",
        )
        with patch(
            "angemedia_gateway.providers.reference_relay.validate_provider_reference_url",
            return_value="https://relay.example.test/opaque-token",
        ) as validator:
            result = asyncio.run(fulfill_relay_decision(decision, backend=backend))
        backend.publish_reference.assert_awaited_once_with(
            "/uploads/private.png",
            ttl_seconds=DEFAULT_RELAY_TTL_SECONDS,
        )
        validator.assert_called_once_with("https://relay.example.test/opaque-token")
        self.assertEqual(result.kind, PUBLIC_URL)
        self.assertEqual(result.source, "relay")
        self.assertEqual(result.value, "https://relay.example.test/opaque-token")

    def test_invalid_or_private_relay_output_is_rejected(self) -> None:
        decision = ReferenceDeliveryDecision(
            kind=RELAY_REQUIRED,
            value="/uploads/private.png",
            source="gateway_asset",
        )
        backend = AsyncMock()
        backend.publish_reference.return_value = RelayPublication(url="http://127.0.0.1:9890/uploads/private.png")
        with patch(
            "angemedia_gateway.providers.reference_relay.validate_provider_reference_url",
            side_effect=ValueError("private target"),
        ):
            with self.assertRaisesRegex(ReferenceRelayUnavailable, "usable public URL"):
                asyncio.run(fulfill_relay_decision(decision, backend=backend))

    def test_ttl_must_stay_bounded(self) -> None:
        decision = ReferenceDeliveryDecision(
            kind=RELAY_REQUIRED,
            value="/uploads/private.png",
            source="gateway_asset",
        )
        backend = AsyncMock()
        for ttl in (0, 59, 3601, 999999):
            with self.subTest(ttl=ttl):
                with self.assertRaisesRegex(ValueError, "TTL"):
                    asyncio.run(fulfill_relay_decision(decision, backend=backend, ttl_seconds=ttl))
        backend.publish_reference.assert_not_called()

if __name__ == "__main__":
    unittest.main()

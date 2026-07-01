"""Video channel expansion guardrails."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.catalog.api import catalog_api_response  # noqa: E402
from angemedia_gateway.providers.catalog.loader import load_provider_catalog  # noqa: E402


class VideoChannelExpansionContractTest(unittest.TestCase):
    candidates = {"runway", "kling", "vidu", "minimax", "google_video", "google"}

    def test_candidate_channels_are_not_selectable_without_adapters(self) -> None:
        catalog = catalog_api_response(load_provider_catalog())
        providers = {item["id"]: item for item in catalog["providers"]}
        models = catalog["models"]
        for candidate in self.candidates:
            provider = providers.get(candidate)
            if provider:
                self.assertFalse(provider.get("enabled_default"), candidate)
            selectable = [
                item for item in models
                if item.get("provider_id") == candidate and item.get("selectable") is True
            ]
            self.assertEqual(selectable, [], candidate)

    def test_adapter_contract_documents_required_safety_work(self) -> None:
        contract = (ROOT / "docs" / "video_channels" / "ADAPTER_CONTRACT.md").read_text(encoding="utf-8")
        for phrase in (
            "real adapter",
            "runtime configuration schema",
            "safe connection test",
            "request hash",
            "provider error mapping",
            "controlled download",
            "Queue worker tests",
            "non-selectable",
        ):
            self.assertIn(phrase, contract)


if __name__ == "__main__":
    unittest.main()

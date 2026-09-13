from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.catalog.loader import load_provider_catalog  # noqa: E402
from angemedia_gateway.providers.upstream_audit import audit_upstream_models  # noqa: E402


class UpstreamModelAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_provider_catalog()

    def test_openai_audit_filters_non_image_models_and_never_returns_secret(self) -> None:
        secret = "sk-audit-secret-should-never-render"

        def fetch(spec, headers):
            self.assertEqual(spec.provider_id, "openai_image")
            self.assertEqual(headers["Authorization"], f"Bearer {secret}")
            return {
                "object": "list",
                "data": [
                    {"id": "gpt-image-2.5-sunburst"},
                    {"id": "gpt-image-2.5-flare"},
                    {"id": "gpt-image-2"},
                    {"id": "gpt-5.6"},
                    {"id": "gpt-image-future"},
                ],
            }

        report = audit_upstream_models(
            catalog=self.catalog,
            environ={"OPENAI_IMAGE_API_KEY": secret},
            fetch_json=fetch,
            provider_ids=["openai_image"],
        )
        provider = report["providers"]["openai_image"]
        self.assertEqual(provider["status"], "ok")
        self.assertEqual(provider["missing_upstream"], [])
        self.assertEqual(provider["upstream_candidates"], ["gpt-image-future"])
        self.assertNotIn(secret, repr(report))
        self.assertNotIn("Authorization", repr(report))

    def test_siliconflow_audit_skips_without_api_key(self) -> None:
        report = audit_upstream_models(
            catalog=self.catalog,
            environ={},
            provider_ids=["siliconflow"],
        )
        provider = report["providers"]["siliconflow"]
        self.assertEqual(provider["status"], "skipped_missing_key")
        self.assertIn("Kwai-Kolors/Kolors", provider["local_models"])

    def test_pollinations_matches_stable_aliases_and_filters_video(self) -> None:
        payload = [
            {
                "name": "tongyi-mai/z-image-turbo",
                "aliases": ["z-image", "zimage"],
                "output_modalities": ["image"],
            },
            {
                "name": "community/p-image-edit",
                "aliases": ["p-image-edit"],
                "output_modalities": ["image"],
            },
            {
                "name": "future/image-model",
                "aliases": ["future-image"],
                "output_modalities": ["image"],
            },
            {
                "name": "video-only",
                "aliases": ["video"],
                "output_modalities": ["video"],
            },
        ]

        report = audit_upstream_models(
            catalog=self.catalog,
            environ={},
            fetch_json=lambda spec, headers: payload,
            provider_ids=["pollinations"],
        )
        provider = report["providers"]["pollinations"]
        self.assertEqual(provider["status"], "ok")
        self.assertEqual(provider["missing_upstream"], [])
        self.assertEqual(provider["matched"]["zimage"], "tongyi-mai/z-image-turbo")
        self.assertIn("future/image-model", provider["upstream_candidates"])
        self.assertNotIn("video-only", provider["upstream_candidates"])

    def test_manual_review_providers_do_not_fetch(self) -> None:
        def fail_fetch(spec, headers):
            raise AssertionError("manual review providers must not perform HTTP requests")

        report = audit_upstream_models(
            catalog=self.catalog,
            environ={},
            fetch_json=fail_fetch,
            provider_ids=["modelscope", "bytedance", "agnes_image", "agnes_video"],
        )
        self.assertTrue(all(item["status"] == "manual_review" for item in report["providers"].values()))

    def test_unknown_provider_is_rejected_before_fetch(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown audit provider"):
            audit_upstream_models(catalog=self.catalog, provider_ids=["not-real"])


if __name__ == "__main__":
    unittest.main()

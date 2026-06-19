"""Assets card UI source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO_ROOT = ROOT / "app" / "www" / "assets" / "studio"


def read(relative_path: str) -> str:
    return (STUDIO_ROOT / relative_path).read_text(encoding="utf-8")


class WebStudioAssetsCardContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page_source = read("features/assets/page.js")
        cls.pages_css = read("styles/pages.css")
        cls.theme_css = read("styles/theme.css")
        cls.responsive_css = read("styles/responsive.css")

    def test_asset_actions_remain_available_without_unsafe_empty_open(self) -> None:
        for action in ("common.download", "common.open", "common.delete"):
            with self.subTest(action=action):
                self.assertIn(action, self.page_source)
        self.assertRegex(
            self.page_source,
            r"(?s)if \(href\) \{.*common\.open.*\} else \{.*common\.download",
        )

    def test_long_title_prompt_and_provider_model_have_overflow_contracts(self) -> None:
        for class_name in (
            "asset-card-title",
            "asset-prompt",
            "asset-provider-tag",
            "asset-model-tag",
        ):
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, self.page_source)
        self.assertRegex(self.pages_css, r"\.asset-card \.card-title\s*\{[^}]*-webkit-line-clamp:\s*2")
        self.assertRegex(self.pages_css, r"\.asset-card \.asset-prompt\s*\{[^}]*-webkit-line-clamp:\s*3")
        self.assertRegex(self.pages_css, r"\.asset-tags\s*\{[^}]*grid-template-columns:\s*repeat\(2")
        self.assertIn("overflow-wrap: anywhere", self.pages_css)

    def test_empty_and_broken_preview_use_compact_fallback(self) -> None:
        self.assertIn("asset-thumb-unavailable", self.page_source)
        self.assertIn("asset-preview-fallback", self.page_source)
        self.assertIn("onerror", self.page_source)
        self.assertNotIn("emptyState(t('assets.unavailable'))", self.page_source)

    def test_390px_layout_keeps_single_column_and_three_actions(self) -> None:
        mobile = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*)\}\s*@media", self.responsive_css, re.S)
        self.assertIsNotNone(mobile)
        body = mobile.group("body")
        self.assertIn(".asset-grid", body)
        self.assertIn("grid-template-columns: minmax(0, 1fr) !important", body)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", body)
        self.assertIn("min-width: 0", body)

    def test_light_theme_has_thumbnail_and_fallback_contrast(self) -> None:
        self.assertIn('html[data-theme="light"] .asset-thumb', self.theme_css)
        self.assertIn('html[data-theme="light"] .asset-preview-fallback', self.theme_css)


if __name__ == "__main__":
    unittest.main()

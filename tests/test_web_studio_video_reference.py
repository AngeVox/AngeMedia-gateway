"""Generate Video reference-image UI source contracts."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"
VIDEO_PAGE = STUDIO / "features" / "generate-video" / "page.js"


class WebStudioVideoReferenceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = VIDEO_PAGE.read_text(encoding="utf-8")

    def test_reuses_upload_and_asset_reference_helpers(self) -> None:
        self.assertIn("createReferenceUpload", self.source)
        self.assertIn("loadImageReferenceAssets", self.source)
        self.assertIn("referenceUpload.prepare()", self.source)
        self.assertIn("payload.image", self.source)

    def test_has_compact_asset_select_preview_and_clear_control(self) -> None:
        self.assertIn("referenceAssetSelect", self.source)
        self.assertIn("referencePreview", self.source)
        self.assertIn("referenceClear", self.source)
        self.assertNotIn("asset-card", self.source)

    def test_submit_remains_manual_without_hidden_polling(self) -> None:
        self.assertIn("wait_for_completion: false", self.source)
        self.assertIn("navigate('#/jobs')", self.source)
        self.assertNotIn("setInterval", self.source)
        self.assertNotIn("setTimeout", self.source)


if __name__ == "__main__":
    unittest.main()

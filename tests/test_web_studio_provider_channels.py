"""Web Studio provider channel UI contracts."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioProviderChannelsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builtin = read(STUDIO / "features" / "providers" / "builtin-config.js")
        cls.i18n = read(STUDIO / "i18n.js")

    def test_builtin_channels_have_image_video_filters(self) -> None:
        self.assertIn("builtinMediaFilter", self.builtin)
        self.assertIn("providers.imageChannels", self.builtin)
        self.assertIn("providers.videoChannels", self.builtin)
        self.assertIn("mediaTypes.includes('video')", self.builtin)
        self.assertIn("mediaTypes.includes('image')", self.builtin)

    def test_builtin_type_media_uses_user_facing_labels(self) -> None:
        self.assertIn("providerMediaLabel", self.builtin)
        self.assertIn("providers.builtinImageChannel", self.i18n)
        self.assertIn("providers.builtinVideoChannel", self.i18n)
        self.assertNotIn("provider_type || '-' } ·", self.builtin)


if __name__ == "__main__":
    unittest.main()

"""Web Studio Prompt Copilot source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"
IMAGE_PAGE = STUDIO / "features" / "generate-image" / "page.js"
VIDEO_PAGE = STUDIO / "features" / "generate-video" / "page.js"
COPILOT = STUDIO / "components" / "prompt-copilot.js"
I18N = STUDIO / "i18n.js"
PAGES_CSS = STUDIO / "styles" / "pages.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioPromptCopilotContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image = read(IMAGE_PAGE)
        cls.video = read(VIDEO_PAGE)
        cls.copilot = read(COPILOT)
        cls.i18n = read(I18N)
        cls.css = read(PAGES_CSS)

    def _function_body(self, source: str, name: str) -> str:
        start = source.index(f"async function {name}")
        next_function = source.find("\n  function ", start + 1)
        next_async = source.find("\n  async function ", start + 1)
        candidates = [idx for idx in [next_function, next_async] if idx > start]
        end = min(candidates) if candidates else len(source)
        return source[start:end]

    def test_generate_image_prompt_copilot_is_real_entry_not_wip(self) -> None:
        self.assertIn("openPromptCopilot", self.image)
        self.assertIn("mediaType: 'image'", self.image)
        self.assertNotIn("showWipFeature({ title: t('wip.promptCopilotTitle')", self.image)
        self.assertIn("'generateImage.promptCopilotAction': '提示词助手'", self.i18n)
        self.assertIn("'generateImage.promptCopilotAction': 'Prompt Copilot'", self.i18n)

    def test_generate_video_prompt_copilot_is_real_entry_not_wip(self) -> None:
        self.assertIn("openPromptCopilot", self.video)
        self.assertIn("mediaType: 'video'", self.video)
        self.assertNotIn("showWipFeature({ title: t('wip.promptCopilotTitle')", self.video)
        self.assertIn("generateVideo.promptCopilotAction", self.video)

    def test_copilot_calls_only_prompt_enhance_api(self) -> None:
        self.assertIn("api.post('/prompt/enhance'", self.copilot)
        self.assertNotIn("/assistant/plan", self.copilot)
        self.assertNotIn("/assistant/generate", self.copilot)
        self.assertNotIn("fetch(", self.copilot)
        self.assertNotIn("setInterval", self.copilot)

    def test_generate_submit_does_not_auto_run_prompt_enhance(self) -> None:
        image_submit = self._function_body(self.image, "submitGeneration")
        video_submit = self._function_body(self.video, "submitVideo")
        for body in (image_submit, video_submit):
            with self.subTest(body=body[:40]):
                self.assertNotIn("/prompt/enhance", body)
                self.assertNotIn("openPromptCopilot", body)
                self.assertNotIn("model_prompt_en", body)

    def test_preview_apply_keep_cancel_semantics(self) -> None:
        for token in (
            "originalPrompt",
            "user_display_prompt_zh",
            "notes_zh",
            "model_prompt_en",
            "promptCopilot.applyEnglish",
            "promptCopilot.copyEnglish",
            "promptCopilot.keepOriginal",
            "promptCopilot.close",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.copilot + self.i18n)
        self.assertRegex(
            self.copilot,
            r"promptInput\.value\s*=\s*result\.model_prompt_en",
            "Prompt textarea should only be overwritten by the apply action.",
        )

    def test_no_raw_secret_or_provider_fields_rendered(self) -> None:
        for source in (self.copilot, self.image, self.video):
            with self.subTest(source=source[:30]):
                self.assertNotIn("request_hash", source)
                self.assertNotIn("input_json", source)
                self.assertNotIn("output_json", source)
                self.assertNotIn("provider_raw_body", source)
                self.assertNotIn("Authorization", source)

    def test_390px_layout_contract_for_copilot(self) -> None:
        self.assertIn(".prompt-copilot-modal", self.css)
        self.assertIn(".prompt-copilot-grid", self.css)
        mobile = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(mobile)
        body = mobile.group("body")
        self.assertIn(".prompt-copilot-modal", body)
        self.assertIn(".prompt-copilot-grid", body)


if __name__ == "__main__":
    unittest.main()

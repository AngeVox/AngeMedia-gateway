"""Web Studio Assistant Plan source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"
LAYOUT = STUDIO / "layout.js"
ASSISTANT = STUDIO / "components" / "assistant-planner.js"
ASSISTANT_SETTINGS = STUDIO / "components" / "assistant-settings.js"
IMAGE_PAGE = STUDIO / "features" / "generate-image" / "page.js"
VIDEO_PAGE = STUDIO / "features" / "generate-video" / "page.js"
I18N = STUDIO / "i18n.js"
PAGES_CSS = STUDIO / "styles" / "pages.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioAssistantPlanContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = read(LAYOUT)
        cls.assistant = read(ASSISTANT)
        cls.assistant_settings = read(ASSISTANT_SETTINGS)
        cls.image = read(IMAGE_PAGE)
        cls.video = read(VIDEO_PAGE)
        cls.i18n = read(I18N)
        cls.css = read(PAGES_CSS)

    def test_topbar_assistant_is_real_panel_not_wip(self) -> None:
        assistant_block = self.layout[
            self.layout.index("const assistantButton"):
            self.layout.index("const diagnosticsButton")
        ]
        self.assertIn("openAssistantChat", assistant_block)
        self.assertNotIn("showWipFeature", assistant_block)
        self.assertNotIn("wip: true", assistant_block)
        self.assertNotIn("assistantWip", assistant_block)

    def test_assistant_panel_calls_plan_only_on_explicit_click(self) -> None:
        self.assertIn("api.post('/assistant/plan'", self.assistant)
        self.assertIn("submit.addEventListener('click', requestPlan)", self.assistant)
        self.assertNotIn("/assistant/generate", self.assistant)
        self.assertNotIn("/admin/jobs/images", self.assistant)
        self.assertNotIn("/admin/jobs/videos", self.assistant)
        self.assertNotIn("setInterval", self.assistant)
        self.assertNotIn("setTimeout", self.assistant)

    def test_assistant_settings_uses_formal_config_models_and_test_contracts(self) -> None:
        self.assertIn("openAssistantSettings", self.assistant)
        api_source = read(STUDIO / "components" / "assistant-settings-api.js")
        self.assertIn("api.get('/admin/config')", api_source)
        self.assertIn("api.post('/admin/config'", api_source)
        self.assertIn("api.post('/admin/assistant/models'", api_source)
        self.assertIn("api.post('/admin/assistant/test'", api_source)
        self.assertIn("if (key) payload.api_key = key", api_source)
        self.assertIn("if (useEmptyApiKey) payload.use_empty_api_key = true", api_source)
        self.assertNotIn("api_key: keyInput.value.trim()", self.assistant_settings)
        self.assertIn("assistant.enabled", self.assistant_settings)
        self.assertIn("assistant.llm_base_url", self.assistant_settings)
        self.assertIn("assistant.llm_model", self.assistant_settings)
        self.assertIn("models.includes(modelInput.value.trim())", self.assistant_settings)
        self.assertNotIn("modelInput.value = assistant.llm_model || 'gpt-4o-mini'", self.assistant_settings)
        self.assertIn("saveAssistantSettings", self.assistant_settings)
        self.assertIn("assistantSettings.noApiKey", self.assistant_settings)
        self.assertNotIn("/admin/jobs/images", self.assistant_settings)
        self.assertNotIn("/admin/jobs/videos", self.assistant_settings)
        self.assertNotIn("Authorization", self.assistant_settings)

    def test_assistant_apply_writes_model_prompt_only_after_action(self) -> None:
        self.assertIn("result.prompt?.model_prompt_en", self.assistant)
        self.assertRegex(
            self.assistant,
            r"directInput\.value\s*=\s*prompt",
            "Assistant plan should only write the textarea from its explicit apply action.",
        )
        self.assertIn("currentPagePromptInput", self.assistant)
        self.assertIn("window.dispatchEvent(new HashChangeEvent('hashchange'))", self.assistant)
        self.assertIn("sessionStorage.setItem('studio_assistant_plan_apply'", self.assistant)
        self.assertIn("applyAssistantPlanPrefill", self.image)
        self.assertIn("applyAssistantPlanPrefill", self.video)

    def test_route_advice_entry_uses_assistant_plan_not_wip(self) -> None:
        self.assertIn("openAssistantPlanner", self.image)
        self.assertIn("generateImage.routeAdviceAction", self.image)
        self.assertNotIn("wip.planningTitle", self.image)
        self.assertNotIn("showWipFeature", self.image)
        self.assertIn("'generateImage.routeAdviceAction': '智能规划'", self.i18n)
        self.assertIn("'generateImage.routeAdviceAction': 'Plan with Assistant'", self.i18n)

    def test_i18n_and_language_contract(self) -> None:
        for key in (
            "assistantPlan.title",
            "assistantPlan.request",
            "assistantPlan.settings",
            "assistantPlan.applyToImage",
            "assistantPlan.applyToVideo",
            "assistantPlan.copyEnglish",
            "assistantPlan.noAutoGenerate",
            "assistantPlan.localMode",
            "assistantPlan.localModeLlmAvailable",
            "assistantPlan.configErrorMode",
        ):
            with self.subTest(key=key):
                self.assertIn(key, self.i18n)
        for key in ("assistantSettings.title", "assistantSettings.fetchModels", "assistantSettings.test", "assistantSettings.save", "assistantSettings.noApiKey"):
            with self.subTest(key=key):
                self.assertIn(key, self.i18n)
        self.assertIn("result.assistant_status", self.assistant)
        self.assertIn("getLanguage().startsWith('zh') ? 'zh' : 'en'", self.assistant)
        self.assertIn("target_prompt_language: 'en'", self.assistant)

    def test_no_raw_secret_or_provider_fields_rendered(self) -> None:
        for source in (self.layout, self.assistant, self.assistant_settings, self.image, self.video):
            with self.subTest(source=source[:30]):
                self.assertNotIn("request_hash", source)
                self.assertNotIn("input_json", source)
                self.assertNotIn("output_json", source)
                self.assertNotIn("provider_raw_body", source)
                self.assertNotIn("Authorization", source)

    def test_390px_layout_contract_for_assistant_plan(self) -> None:
        for class_name in (".assistant-plan-modal", ".assistant-plan-grid", ".assistant-plan-actions", ".assistant-settings-modal", ".assistant-settings-actions"):
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, self.css)
        mobile = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.css, re.S)
        self.assertIsNotNone(mobile)
        body = mobile.group("body")
        self.assertIn(".assistant-plan-modal", body)
        self.assertIn(".assistant-plan-grid", body)


if __name__ == "__main__":
    unittest.main()

"""Web Studio Generate Image provider handoff source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATE_IMAGE_DIR = ROOT / "app" / "www" / "assets" / "studio" / "features" / "generate-image"
CAPABILITIES_JS = ROOT / "app" / "www" / "assets" / "studio" / "lib" / "capabilities.js"
SAFE_ERROR_JS = ROOT / "app" / "www" / "assets" / "studio" / "lib" / "safe-error.js"
I18N_JS = ROOT / "app" / "www" / "assets" / "studio" / "i18n.js"


def read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_feature_source(path: Path) -> str:
    return "\n".join(read_source(item) for item in sorted(path.glob("*.js")))


def compact(source: str) -> str:
    return re.sub(r"\s+", " ", source)


class WebStudioGenerateImageHandoffSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = read_feature_source(GENERATE_IMAGE_DIR)
        cls.compact_source = compact(cls.source)
        cls.capabilities_source = read_source(CAPABILITIES_JS)
        cls.safe_error_source = read_source(SAFE_ERROR_JS)
        cls.i18n_source = read_source(I18N_JS)

    def test_loads_catalog_for_image_capabilities_and_safe_provider_summaries(self) -> None:
        """Generate Image should load catalog capabilities plus custom provider safe summaries."""
        self.assertRegex(
            self.source,
            r"api\.get\(\s*['\"]\/admin\/catalog['\"]\s*\)",
            "Generate Image must use GET /v1/admin/catalog for builtin/catalog image capabilities.",
        )
        self.assertRegex(
            self.source,
            r"api\.get\(\s*['\"]\/admin\/providers['\"]\s*\)",
            "Generate Image may still use GET /v1/admin/providers for custom provider safe summaries.",
        )

    def test_does_not_call_deferred_provider_admin_apis(self) -> None:
        """Provider status/test/delete/sort/fallback remain out of the Generate Image handoff."""
        forbidden_endpoints = [
            "provider-status",
            "provider-templates",
            "/test",
            "/sort",
            "/fallback",
            "fallback-chain",
        ]
        for endpoint in forbidden_endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertNotIn(endpoint, self.source)

    def test_enabled_openai_image_providers_are_the_only_custom_options(self) -> None:
        """Only enabled OpenAI-compatible image providers should be selectable."""
        self.assertIn("provider_type", self.capabilities_source)
        self.assertIn("openai_image", self.capabilities_source)
        self.assertRegex(self.capabilities_source, r"\benabled\b")
        self.assertIn("providerOptions", self.source)
        self.assertIn("providerSelect", self.source)

    def test_catalog_image_models_are_filtered_by_media_type_and_selectable(self) -> None:
        """Catalog-backed Generate Image options should only include selectable image models."""
        self.assertIn("selectableImageModels", self.capabilities_source)
        self.assertIn("imageProvidersForModels", self.capabilities_source)
        self.assertIn("item.media_type === 'image'", self.capabilities_source)
        self.assertIn("item.selectable === true", self.capabilities_source)
        self.assertIn("selectableImageModels", self.source)
        self.assertIn("imageProvidersForModels", self.source)

    def test_custom_provider_submit_uses_custom_model_id_without_secret_config(self) -> None:
        """Selecting a custom provider should route with custom:<id> and submit provider_model."""
        self.assertIn("custom:", self.source)
        self.assertTrue(
            re.search(r"\bmodel\s*:", self.source) or re.search(r"\.model\s*=", self.source),
            "The image generation payload should include model when a custom provider is selected.",
        )
        self.assertIn("provider_model", self.source)
        self.assertNotIn("modelInput.readOnly = true", self.source)
        self.assertIn("generateImage.providerModelOverride", self.source)
        self.assertIn("Upstream model / Provider model override", self.i18n_source)
        self.assertIn("上游模型 / Provider model override", self.i18n_source)

    def test_generate_image_source_does_not_reference_secret_provider_fields(self) -> None:
        """Generate Image handoff must not display or submit provider secret/config fields."""
        forbidden_patterns = [
            r"['\"]api_key['\"]",
            r"\.api_key\b",
            r"\bapi_key\s*:",
            r"\bbase_url\b",
            r"\bstatus_url\b",
            r"\bquota_url\b",
            r"\blast_error\b",
            r"\braw_response\b",
            r"\braw\b",
        ]
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(self.source, pattern)

    def test_default_builtin_payload_remains_available(self) -> None:
        """Built-in/default generation should keep the existing minimal OpenAI-compatible payload."""
        self.assertRegex(
            self.source,
            r"api\.post\(\s*['\"]\/images\/generations['\"]",
            "Generate Image should still submit to /v1/images/generations.",
        )
        self.assertRegex(self.source, r"\bprompt\b")
        self.assertRegex(self.source, r"response_format\s*:\s*['\"]url['\"]")

    def test_size_presets_are_catalog_driven_and_custom_size_is_preserved(self) -> None:
        """Generate Image should use catalog size_presets instead of one fixed preset list."""
        self.assertNotIn("IMAGE_SIZE_PRESETS", self.source)
        self.assertIn("size_presets", self.source)
        self.assertIn("sizeOptionsForModel", self.source)
        self.assertIn("generateImage.sizeCapabilityUnknown", self.source)
        self.assertIn("generateImage.sizeCapabilityUnknown", self.i18n_source)
        self.assertIn("generateImage.sizeCapabilityCatalogUnknown", self.source)
        self.assertIn("generateImage.sizeCapabilityCustomUnknown", self.source)
        self.assertIn("generateImage.sizeCapabilityDefaultHint", self.source)
        self.assertIn("该模型未声明固定尺寸预设", self.i18n_source)
        self.assertIn("该自定义服务商未声明尺寸预设", self.i18n_source)
        self.assertIn("请选择模型，或使用自定义尺寸", self.i18n_source)
        self.assertRegex(
            self.source,
            r"\bsize\s*:",
            "The image generation payload should include size.",
        )

    def test_operation_controls_are_catalog_driven_and_payload_filtered(self) -> None:
        self.assertIn("getTextToImageOperation", self.source)
        self.assertIn("operationParams(model)", self.source)
        self.assertIn("operationRefs(model)", self.source)
        self.assertIn("buildOperationPayload", self.source)
        self.assertIn("operationValues", self.source)
        self.assertIn("createOperationControls", self.source)
        self.assertIn("syncOperationControls", self.source)
        self.assertIn("generateImage.param.negative_prompt", self.i18n_source)
        self.assertIn("generateImage.param.steps", self.i18n_source)
        self.assertIn("generateImage.param.guidance", self.i18n_source)

    def test_custom_size_validation_is_present(self) -> None:
        """Generate Image should support validated WIDTHxHEIGHT custom sizes."""
        self.assertIn("validateCustomSize", self.source)
        self.assertRegex(self.capabilities_source, r"\^\(\[1-9\]\\d\{1,3\}\)x\(\[1-9\]\\d\{1,3\}\)")
        self.assertIn("generateImage.sizeInvalidFormat", self.i18n_source)
        self.assertIn("generateImage.sizeInvalidRange", self.i18n_source)

    def test_submit_payload_contract_keeps_model_routing_without_provider_field(self) -> None:
        """The backend contract keeps model as route selector and provider_model custom-only."""
        self.assertIn("api.post('/images/generations'", self.source)
        self.assertRegex(self.source, r"\.model\s*=")
        self.assertNotIn("payload.provider", self.source)
        self.assertIn("provider_model", self.source)

    def test_button_and_model_labels_avoid_queue_and_duplicate_model_copy(self) -> None:
        self.assertNotIn("加入队列", self.i18n_source)
        self.assertNotIn("Add to Queue", self.i18n_source)
        self.assertIn("'generateImage.submit': '开始生成'", self.i18n_source)
        self.assertIn("'generateImage.submit': 'Generate'", self.i18n_source)
        self.assertIn("generateImage.routeModel", self.source)
        self.assertIn("Route model (optional)", self.i18n_source)
        self.assertIn("路由模型名（可选）", self.i18n_source)

    def test_duplicate_conflict_uses_safe_short_message(self) -> None:
        """HTTP 409 duplicate responses should use a sanitized Generate Image message."""
        handles_duplicate = (
            "duplicate_in_flight_job" in self.source
            or "err.status === 409" in self.compact_source
            or "err.status===409" in self.compact_source
            or re.search(r"status\s*===\s*409", self.source)
        )
        self.assertTrue(handles_duplicate, "Generate Image should handle duplicate 409 responses explicitly.")
        self.assertIn("generateImage.duplicate", self.i18n_source)

        forbidden_terms = [
            "request_hash",
            "request_hash_version",
            "input_json",
            "output_json",
            "raw_response",
        ]
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, self.source)



    def test_generate_image_source_handles_human_hint_from_error_response(self) -> None:
        """Generate Image should prefer API human_hint and keep a safe fallback."""
        self.assertIn("generateImage.error", self.source)
        self.assertIn("safeErrorMessage", self.source)
        self.assertIn("human_hint", self.safe_error_source)

if __name__ == "__main__":
    unittest.main()

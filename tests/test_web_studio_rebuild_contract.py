"""WEB-REBUILD-1 frontend source contracts."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO_ROOT = ROOT / "app" / "www" / "assets" / "studio"
LAYOUT_JS = STUDIO_ROOT / "layout.js"
APP_JS = STUDIO_ROOT / "app.js"
I18N_JS = STUDIO_ROOT / "i18n.js"
THEME_CSS = STUDIO_ROOT / "styles" / "theme.css"
ASSETS_PAGE_JS = STUDIO_ROOT / "features" / "assets" / "page.js"
JOBS_PAGE_JS = STUDIO_ROOT / "features" / "jobs" / "page.js"
PROVIDERS_PAGE_JS = STUDIO_ROOT / "features" / "providers" / "page.js"
KEYS_PAGE_JS = STUDIO_ROOT / "features" / "gateway-keys" / "page.js"
GENERATE_VIDEO_PAGE_JS = STUDIO_ROOT / "features" / "generate-video" / "page.js"
GENERATE_VIDEO_SHIM_JS = STUDIO_ROOT / "pages" / "generate-video.js"
WIP_PAGE_JS = STUDIO_ROOT / "features" / "wip" / "page.js"
CAPABILITIES_JS = STUDIO_ROOT / "lib" / "capabilities.js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def studio_sources() -> dict[Path, str]:
    return {path: read(path) for path in STUDIO_ROOT.rglob("*.js")}


class WebStudioRebuildSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout_source = read(LAYOUT_JS)
        cls.app_source = read(APP_JS)
        cls.i18n_source = read(I18N_JS)
        cls.theme_source = read(THEME_CSS)
        cls.assets_source = read(ASSETS_PAGE_JS)
        cls.jobs_source = read(JOBS_PAGE_JS)
        cls.providers_source = read(PROVIDERS_PAGE_JS)
        cls.keys_source = read(KEYS_PAGE_JS)
        cls.generate_video_source = read(GENERATE_VIDEO_PAGE_JS)
        cls.generate_video_shim_source = read(GENERATE_VIDEO_SHIM_JS)
        cls.wip_source = read(WIP_PAGE_JS)
        cls.capabilities_source = read(CAPABILITIES_JS)

    def test_formal_nav_contains_only_product_rc_entries(self) -> None:
        nav_routes = set(re.findall(r"hash:\s*['\"]([^'\"]+)['\"]", self.layout_source))
        self.assertEqual(
            nav_routes,
            {
                "#/dashboard",
                "#/generate/image",
                "#/generate/video",
                "#/jobs",
                "#/assets",
                "#/providers",
                "#/gateway-keys",
            },
        )

    def test_wip_routes_are_registered_and_render_unavailable_message(self) -> None:
        for route in ("#/diagnostics", "#/jobs/:id", "#/assets/:id"):
            with self.subTest(route=route):
                self.assertIn(f"router.register('{route}'", self.app_source)
        self.assertIn("renderUnavailable", self.wip_source)
        self.assertIn("wip.message", self.wip_source)
        self.assertIn("当前版本暂未开放", self.i18n_source)

    def test_generate_video_page_is_catalog_aware_and_not_wip(self) -> None:
        self.assertIn("features/generate-video/page.js", self.generate_video_shim_source)
        self.assertIn("router.register('#/generate/video'", self.app_source)
        self.assertIn("api.get('/admin/catalog')", self.generate_video_source)
        self.assertIn("selectableVideoModels", self.generate_video_source)
        self.assertIn("videoProvidersForModels", self.generate_video_source)
        self.assertIn("item.media_type === 'video'", self.capabilities_source)
        self.assertIn("item.selectable === true", self.capabilities_source)
        self.assertIn("size_presets", self.generate_video_source)
        self.assertIn("params", self.generate_video_source)
        self.assertIn("ref_inputs", self.generate_video_source)
        self.assertIn("capabilities", self.generate_video_source)
        self.assertIn("api.post('/videos'", self.generate_video_source)
        self.assertIn("safeErrorMessage", self.generate_video_source)
        self.assertIn("job_id", self.generate_video_source)
        self.assertIn("task_id", self.generate_video_source)
        self.assertIn("navigate('#/jobs')", self.generate_video_source)
        self.assertIn("navigate('#/assets')", self.generate_video_source)
        self.assertNotIn("renderUnavailable", self.generate_video_source)
        self.assertNotIn("wip.generateVideoTitle", self.generate_video_source)
        for provider_term in ("Agnes", "agnes_video", "agnes-video"):
            with self.subTest(provider_term=provider_term):
                self.assertNotIn(provider_term, self.generate_video_source)

    def test_no_saas_concepts_or_frontend_frameworks(self) -> None:
        forbidden = ("team", "billing", "workspace", "organization", "subscription", "React", "Vue", "Svelte")
        for path, source in studio_sources().items():
            with self.subTest(path=str(path.relative_to(ROOT))):
                for term in forbidden:
                    self.assertNotIn(term, source)

    def test_assets_use_real_download_delete_and_no_filesystem_path_display(self) -> None:
        self.assertIn("buildAssetDownloadName", self.assets_source)
        self.assertIn("api.delete(`/assets/${encodeURIComponent(asset.id)}`)", self.assets_source)
        self.assertIn("assets.editUnavailable", self.assets_source)
        self.assertNotIn("local_path", self.assets_source)
        self.assertIn("assetDisplayName", self.assets_source)

    def test_gateway_keys_hide_revoked_by_default_and_never_list_full_secret(self) -> None:
        self.assertIn("let showRevoked = false", self.keys_source)
        self.assertIn("showRevoked || !item.revoked_at", self.keys_source)
        self.assertIn("oneTimeSecret", self.keys_source)
        self.assertNotRegex(self.keys_source, r"item\.key\b")
        self.assertIn("key_hash", self.keys_source)
        self.assertIn("CREATE_FORBIDDEN_FIELDS", self.keys_source)

    def test_jobs_consume_structured_diagnostics(self) -> None:
        for field in ("human_hint", "error_category", "retryable", "gateway_stage"):
            with self.subTest(field=field):
                self.assertIn(field, self.jobs_source)
        self.assertIn("safeText(job.error_message", self.jobs_source)

    def test_providers_expose_only_minimal_custom_edit_test_actions(self) -> None:
        """Provider RC contract: custom providers get Edit/Test, platform actions stay hidden."""
        for term in ("Edit", "Test", "/test", "api.patch(`/admin/providers/"):
            with self.subTest(term=term):
                self.assertIn(term, self.providers_source)
        for term in ("Sort", "Fallback", "/sort", "/fallback"):
            with self.subTest(term=term):
                self.assertNotIn(term, self.providers_source)
        self.assertIn("/enabled", self.providers_source)
        self.assertIn("type: 'password'", self.providers_source)
        self.assertNotIn("status_url", self.providers_source)
        self.assertNotIn("quota_url", self.providers_source)

    def test_providers_support_custom_delete(self) -> None:
        """v0.2.0 合同: custom provider delete 必须存在。"""
        self.assertIn("common.delete", self.providers_source)
        self.assertIn("/admin/providers/", self.providers_source)
        self.assertIn("confirmRemoveProvider", self.providers_source)

    def test_providers_edit_test_and_read_only_sections_contract(self) -> None:
        """Provider RC contract: custom is editable/testable; builtin/catalog/reserved are read-only."""
        for term in (
            "editingProvider",
            "editSubmit",
            "editSecretPlaceholder",
            "testProvider",
            "builtinProviders",
            "catalogProviders",
            "reservedProviders",
            "readOnly",
            "test_not_supported",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.providers_source)

    def test_provider_create_form_uses_base_url_copy_and_validation(self) -> None:
        self.assertIn("'providers.endpoint': 'Base URL'", self.i18n_source)
        self.assertIn("OpenAI-compatible Base URL", self.i18n_source)
        self.assertIn("Do not include /images/generations", self.i18n_source)
        self.assertIn("不要填写 /images/generations", self.i18n_source)
        self.assertIn("validateProviderBaseUrl", self.providers_source)
        self.assertIn("new URL", self.providers_source)
        self.assertIn("providers.baseUrlMissingProtocol", self.providers_source)
        self.assertIn("providers.baseUrlNoEndpoint", self.providers_source)
        self.assertIn("providers.baseUrlHelp", self.providers_source)

    def test_provider_error_message_keeps_ssrf_detail_and_dns_hint(self) -> None:
        self.assertIn("SSRF", self.i18n_source)
        self.assertIn("DNS", self.i18n_source)
        self.assertIn("hosts", self.i18n_source)
        self.assertIn("providers.errorDetailPrefix", self.providers_source)
        self.assertIn("safeText(detail", self.providers_source)
        self.assertRegex(self.providers_source, r"127\\.0\\.0\\.1|::1")

    def test_light_theme_uses_neutral_background_without_light_glare(self) -> None:
        self.assertIn("--bg: #f4f6f8", self.theme_source)
        for selector in (
            r'html\[data-theme="light"\] body',
            r'html\[data-theme="light"\] #content',
            r'html\[data-theme="light"\] \.login-page',
        ):
            with self.subTest(selector=selector):
                match = re.search(selector + r"\s*\{(?P<body>[^}]*)\}", self.theme_source, re.S)
                self.assertIsNotNone(match)
                self.assertNotIn("radial-gradient", match.group("body"))

    def test_generate_image_custom_size_contract(self) -> None:
        self.assertIn("validateCustomSize", self.capabilities_source)
        self.assertIn("1024x1024", self.capabilities_source)
        self.assertIn("custom", self.capabilities_source)


if __name__ == "__main__":
    unittest.main()

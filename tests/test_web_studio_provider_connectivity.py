"""Web Studio Provider connectivity and relay UI contracts."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "app" / "www" / "assets" / "studio"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class WebStudioProviderConnectivityContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page = read(STUDIO / "features" / "providers" / "page.js")
        cls.api = read(STUDIO / "features" / "providers" / "provider-api.js")
        cls.global_settings = read(STUDIO / "features" / "providers" / "connectivity-settings.js")
        cls.provider_transport = read(STUDIO / "features" / "providers" / "provider-transport.js")
        cls.builtin = read(STUDIO / "features" / "providers" / "builtin-config.js")
        cls.custom = read(STUDIO / "features" / "providers" / "provider-form.js")
        cls.validation = read(STUDIO / "features" / "providers" / "provider-validation.js")
        cls.i18n = read(STUDIO / "i18n.js")

    def test_global_transport_and_reference_relay_are_available_on_providers_page(self) -> None:
        self.assertIn("renderConnectivitySettingsPanel", self.page)
        self.assertIn("/admin/provider-transport", self.api)
        self.assertIn("/admin/reference-relay", self.api)
        self.assertIn("external_http", self.global_settings)
        self.assertIn("explicit_proxy", self.global_settings)

    def test_per_provider_transport_is_available_for_builtin_and_custom_edit(self) -> None:
        self.assertIn("providerTransportSection(providerId)", self.builtin)
        self.assertIn("providerTransportSection(detail.id)", self.custom)
        self.assertIn("transportInherit", self.provider_transport)
        self.assertIn("explicit_proxy", self.provider_transport)


    def test_per_provider_transport_api_includes_encoded_provider_id(self) -> None:
        self.assertIn("'/admin/provider-transport/' + encodeURIComponent(providerId)", self.api)
        self.assertNotIn("api.get(`/admin/provider-transport/`)", self.api)
        self.assertNotIn("api.post(`/admin/provider-transport/`, payload)", self.api)

    def test_sensitive_connection_values_are_write_only_in_ui(self) -> None:
        self.assertIn("type: 'password'", self.global_settings)
        self.assertIn("type: 'password'", self.provider_transport)
        self.assertNotIn("data.proxy_url", self.global_settings)
        self.assertNotIn("data.proxy_url", self.provider_transport)
        self.assertNotRegex(self.global_settings, r"\bdata\.upload_url\b")
        self.assertNotRegex(self.global_settings, r"\bdata\.token\b")

    def test_safe_connection_summaries_render_real_fields_not_empty_placeholders(self) -> None:
        for source in (self.global_settings, self.provider_transport):
            self.assertIn("effective_mode", source)
            self.assertIn("effective_source", source)
            self.assertIn("proxy_configured", source)
        self.assertIn("upload_url_configured", self.global_settings)
        self.assertIn("token_configured", self.global_settings)
        for broken in ("`:  · : `", "`:  · :  · : "):
            self.assertNotIn(broken, self.global_settings)
            self.assertNotIn(broken, self.provider_transport)

    def test_local_provider_endpoints_are_not_described_as_ssrf_failures(self) -> None:
        self.assertNotIn("privateUrlPolicy", self.validation)
        self.assertNotIn("privateUrlPolicy", self.i18n)
        self.assertIn("endpointPolicyRejected", self.validation)
        self.assertIn("metadata", self.validation)
        self.assertNotIn("localhost", self.validation)

    def test_user_facing_copy_keeps_proxy_and_relay_optional(self) -> None:
        self.assertIn("默认直连", self.i18n)
        self.assertIn("URL-only", self.i18n)
        self.assertIn("Direct is the default", self.i18n)


if __name__ == "__main__":
    unittest.main()

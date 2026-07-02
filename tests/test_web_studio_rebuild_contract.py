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
PAGES_CSS = STUDIO_ROOT / "styles" / "pages.css"
COMPONENTS_CSS = STUDIO_ROOT / "styles" / "components.css"
ASSETS_PAGE_JS = STUDIO_ROOT / "features" / "assets" / "page.js"
Dashboard_PAGE_JS = STUDIO_ROOT / "features" / "dashboard" / "page.js"
JOBS_PAGE_JS = STUDIO_ROOT / "features" / "jobs" / "page.js"
JOB_DISPLAY_JS = STUDIO_ROOT / "lib" / "job-display.js"
PROVIDERS_DIR = STUDIO_ROOT / "features" / "providers"
KEYS_PAGE_JS = STUDIO_ROOT / "features" / "gateway-keys" / "page.js"
GENERATE_VIDEO_PAGE_JS = STUDIO_ROOT / "features" / "generate-video" / "page.js"
GENERATE_VIDEO_SHIM_JS = STUDIO_ROOT / "pages" / "generate-video.js"
GENERATE_IMAGE_PAGE_JS = STUDIO_ROOT / "features" / "generate-image" / "page.js"
GENERATE_IMAGE_RESULT_JS = STUDIO_ROOT / "features" / "generate-image" / "result-preview.js"
JOB_RESULT_TRACKER_JS = STUDIO_ROOT / "components" / "job-result-tracker.js"
MODAL_JS = STUDIO_ROOT / "components" / "modal.js"
DIAGNOSTICS_PAGE_JS = STUDIO_ROOT / "features" / "diagnostics" / "page.js"
DIAGNOSTICS_SHIM_JS = STUDIO_ROOT / "pages" / "diagnostics.js"
JOBS_DETAIL_PAGE_JS = STUDIO_ROOT / "pages" / "jobs-detail.js"
ASSETS_DETAIL_PAGE_JS = STUDIO_ROOT / "pages" / "assets-detail.js"
WIP_PAGE_JS = STUDIO_ROOT / "features" / "wip" / "page.js"
CAPABILITIES_JS = STUDIO_ROOT / "lib" / "capabilities.js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_feature(path: Path) -> str:
    return "\n".join(read(item) for item in sorted(path.glob("*.js")))


def studio_sources() -> dict[Path, str]:
    return {path: read(path) for path in STUDIO_ROOT.rglob("*.js")}


class WebStudioRebuildSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout_source = read(LAYOUT_JS)
        cls.app_source = read(APP_JS)
        cls.i18n_source = read(I18N_JS)
        cls.theme_source = read(THEME_CSS)
        cls.pages_source = read(PAGES_CSS)
        cls.components_source = read(COMPONENTS_CSS)
        cls.dashboard_source = read(Dashboard_PAGE_JS)
        cls.assets_source = read(ASSETS_PAGE_JS)
        cls.jobs_source = read(JOBS_PAGE_JS)
        cls.job_display_source = read(JOB_DISPLAY_JS)
        cls.providers_source = read_feature(PROVIDERS_DIR)
        cls.keys_source = read(KEYS_PAGE_JS)
        cls.generate_video_source = read(GENERATE_VIDEO_PAGE_JS)
        cls.generate_video_shim_source = read(GENERATE_VIDEO_SHIM_JS)
        cls.generate_image_source = read(GENERATE_IMAGE_PAGE_JS)
        cls.generate_image_result_source = read(GENERATE_IMAGE_RESULT_JS)
        cls.job_tracker_source = read(JOB_RESULT_TRACKER_JS) if JOB_RESULT_TRACKER_JS.exists() else ""
        cls.modal_source = read(MODAL_JS)
        cls.diagnostics_source = read(DIAGNOSTICS_PAGE_JS)
        cls.diagnostics_shim_source = read(DIAGNOSTICS_SHIM_JS)
        cls.jobs_detail_source = read(JOBS_DETAIL_PAGE_JS)
        cls.assets_detail_source = read(ASSETS_DETAIL_PAGE_JS)
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

    def test_detail_and_diagnostics_routes_are_registered_and_functional(self) -> None:
        for route in ("#/diagnostics", "#/jobs/:id", "#/assets/:id"):
            with self.subTest(route=route):
                self.assertIn(f"router.register('{route}'", self.app_source)
        self.assertIn("features/diagnostics/page.js", self.diagnostics_shim_source)
        self.assertIn("api.get('/admin/diagnostics/summary')", self.diagnostics_source)
        self.assertIn("api.get(`/admin/jobs/${encodeURIComponent(params.id)}`)", self.jobs_detail_source)
        self.assertIn("api.get(`/assets/${encodeURIComponent(params.id)}`)", self.assets_detail_source)
        self.assertIn("studio_open_job_id", self.assets_detail_source)
        for source in (self.diagnostics_source, self.jobs_detail_source, self.assets_detail_source):
            with self.subTest(source=source[:40]):
                self.assertNotIn("renderUnavailable", source)
                self.assertNotIn("wip.", source)
                self.assertNotIn("input_json", source)
                self.assertNotIn("output_json", source)
                self.assertNotIn("request_hash", source)
                self.assertNotIn("local_path", source)
                self.assertNotIn("setInterval", source)

    def test_diagnostics_topbar_entry_is_not_wip(self) -> None:
        self.assertIn("diagnosticsButton", self.layout_source)
        diagnostics_block = self.layout_source[
            self.layout_source.index("const diagnosticsButton"):
            self.layout_source.index("const logoutButton")
        ]
        self.assertIn("navigate('#/diagnostics')", diagnostics_block)
        self.assertNotIn("diagnosticsWip", diagnostics_block)
        self.assertNotIn("wip: true", diagnostics_block)

    def test_diagnostics_page_uses_safe_server_side_summary(self) -> None:
        for term in (
            "summary.health",
            "summary.queue",
            "summary.providers",
            "summary.recent_failed_jobs",
            "summary.dispatches",
            "summary.maintenance",
            "api.post('/admin/maintenance/retention/clean'",
            "doubleConfirmModal",
            "navigate('#/jobs')",
            "navigate('#/providers')",
            "safeText",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.diagnostics_source)
        for term in ("api.get('/jobs", "api.get('/assets?limit=100", "JSON.stringify"):
            with self.subTest(term=term):
                self.assertNotIn(term, self.diagnostics_source)

    def test_detail_pages_have_bounded_mobile_layout_contract(self) -> None:
        for class_name in (
            ".diagnostics-grid",
            ".diagnostics-section",
            ".diagnostics-list",
            ".detail-page",
            ".detail-grid",
            ".detail-preview",
        ):
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, self.pages_source)
        mobile_match = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.pages_source, re.S)
        self.assertIsNotNone(mobile_match)
        mobile = mobile_match.group("body")
        self.assertIn(".diagnostics-grid", mobile)
        self.assertIn(".detail-grid", mobile)

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
        self.assertIn("api.post('/admin/jobs/videos'", self.generate_video_source)
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
        self.assertNotIn("relative_path", self.assets_source)
        self.assertNotIn("storage_area", self.assets_source)
        self.assertIn("assetDisplayName", self.assets_source)

    def test_dashboard_uses_server_side_queue_summary(self) -> None:
        self.assertIn("api.get('/admin/dashboard/summary')", self.dashboard_source)
        self.assertIn("summary.queue", self.dashboard_source)
        self.assertIn("summary.storage", self.dashboard_source)
        self.assertIn("formatBytes", self.dashboard_source)
        self.assertIn("recent_failed_jobs", self.dashboard_source)
        self.assertIn("recent_assets", self.dashboard_source)
        self.assertNotIn("api.get('/jobs?limit=8&offset=0')", self.dashboard_source)
        self.assertNotIn("api.get('/assets?limit=100&offset=0')", self.dashboard_source)
        self.assertNotRegex(self.dashboard_source, r"\.reduce\(\(acc,\s*job\)")
        self.assertNotIn("setInterval", self.dashboard_source)

    def test_dashboard_and_jobs_support_local_display_clearing_without_deleting_records(self) -> None:
        self.assertIn("local-display-filters", self.dashboard_source)
        self.assertIn("studio_dashboard_recent_jobs_hidden_since", self.dashboard_source)
        self.assertIn("dashboard.clearRecent", self.dashboard_source)
        self.assertIn("dashboard.restoreHidden", self.dashboard_source)
        self.assertIn("local-display-filters", self.jobs_source)
        self.assertIn("studio_jobs_hidden_ids", self.jobs_source)
        self.assertIn("jobs.hideCurrentPage", self.jobs_source)
        self.assertIn("jobs.restoreHidden", self.jobs_source)
        self.assertNotIn("api.delete(`/admin/jobs", self.jobs_source)

    def test_jobs_cleanup_uses_formal_endpoint_and_double_confirmation(self) -> None:
        self.assertIn("doubleConfirmModal", self.modal_source)
        self.assertIn("doubleConfirmModal", self.jobs_source)
        self.assertIn("doubleConfirmModal", self.dashboard_source)
        self.assertIn("api.post('/admin/jobs/cleanup'", self.jobs_source)
        self.assertIn("api.post('/admin/jobs/cleanup'", self.dashboard_source)
        self.assertIn("jobs.cleanupConfirmText", self.jobs_source)
        self.assertIn("jobs.cleanupConfirmText", self.dashboard_source)
        self.assertNotIn("api.delete(`/admin/jobs", self.jobs_source)

    def test_jobs_can_recover_stuck_queued_outbox_without_fake_retry(self) -> None:
        self.assertIn("api.post('/admin/jobs/requeue-stale'", self.jobs_source)
        self.assertIn("jobs.requeueStaleAction", self.jobs_source)
        self.assertIn("queuedJobs", self.jobs_source)
        self.assertNotIn("/admin/jobs/retry", self.jobs_source)

    def test_provider_runtime_timeouts_are_global_not_per_channel(self) -> None:
        self.assertIn("runtime-settings.js", self.providers_source)
        self.assertIn("IMAGE_PROVIDER_TIMEOUT", self.providers_source)
        self.assertIn("VIDEO_PROVIDER_TIMEOUT", self.providers_source)
        self.assertIn("api.post('/admin/config'", self.providers_source)
        self.assertIn(".provider-runtime-grid", self.pages_source)
        self.assertIn("providers.runtimeTimeoutTitle", self.i18n_source)

    def test_dashboard_storage_panel_is_host_disk_not_asset_count(self) -> None:
        storage_panel = self.dashboard_source[
            self.dashboard_source.index("function storagePanel"):
            self.dashboard_source.index("function recentFailuresPanel")
        ]
        self.assertIn("summary.storage", storage_panel)
        self.assertIn("storage.media_volume", storage_panel)
        self.assertIn("disk.used_percent", storage_panel)
        self.assertIn("storage.volumes", storage_panel)
        self.assertIn("media.generated_bytes", storage_panel)
        self.assertNotIn("summary?.assets", storage_panel)
        self.assertNotIn("assets.total", storage_panel)

    def test_assets_render_job_links_and_safe_queue_metadata(self) -> None:
        self.assertIn("asset.job", self.assets_source)
        self.assertIn("job.status", self.assets_source)
        self.assertIn("generation", self.assets_source)
        self.assertIn("openJobFromAsset", self.assets_source)
        self.assertIn("studio_open_job_id", self.assets_source)
        self.assertIn("provider", self.assets_source)
        self.assertIn("model", self.assets_source)
        self.assertNotIn("input_json", self.assets_source)
        self.assertNotIn("output_json", self.assets_source)
        self.assertNotIn("request_hash", self.assets_source)
        self.assertNotIn("setInterval", self.assets_source)

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
        self.assertIn("displayJobEventType", self.jobs_source)
        self.assertIn("displayJobSummaryKey", self.jobs_source)
        self.assertIn("worker_attempt_succeeded", self.job_display_source)
        self.assertIn("image_generate", self.job_display_source)
        self.assertIn("生成记录 ID", self.job_display_source)

    def test_dashboard_compact_status_uses_lights_not_text_badges(self) -> None:
        self.assertIn("statusLight(job.status", self.dashboard_source)
        self.assertIn(".status-light", self.components_source)
        self.assertIn("status-light-pulse", self.components_source)

    def test_jobs_task_center_uses_server_side_filters_and_detail_drawer(self) -> None:
        self.assertIn("api.get(`/admin/jobs?", self.jobs_source)
        self.assertIn("URLSearchParams", self.jobs_source)
        for param in ("status", "kind", "provider", "model", "limit", "offset", "sort"):
            with self.subTest(param=param):
                self.assertIn(param, self.jobs_source)
        self.assertIn("api.get(`/admin/jobs/${encodeURIComponent(jobId)}`)", self.jobs_source)
        self.assertIn("job-detail-drawer", self.jobs_source)
        self.assertIn("job-detail-section", self.jobs_source)
        self.assertIn("input_summary", self.jobs_source)
        self.assertIn("output_summary", self.jobs_source)
        self.assertIn("events", self.jobs_source)
        self.assertIn("attempts", self.jobs_source)
        self.assertIn("assets", self.jobs_source)
        self.assertIn("generation", self.jobs_source)
        self.assertNotIn("api.get('/jobs?limit=100&offset=0')", self.jobs_source)
        self.assertNotIn("allJobs", self.jobs_source)
        self.assertNotIn("pageSlice", self.jobs_source)
        self.assertNotIn("input_json", self.jobs_source)
        self.assertNotIn("output_json", self.jobs_source)
        self.assertNotIn("request_hash", self.jobs_source)

    def test_video_jobs_have_explicit_on_demand_refresh(self) -> None:
        self.assertIn("/admin/jobs/${encodeURIComponent(job.id)}/refresh", self.jobs_source)
        self.assertIn("jobs.refreshStatus", self.jobs_source)
        self.assertIn("job.provider_status", self.jobs_source)

    def test_generate_pages_track_queued_job_results_without_interval_polling(self) -> None:
        self.assertIn("startJobResultTracker", self.generate_image_result_source)
        self.assertIn("startJobResultTracker", self.generate_video_source)
        self.assertIn("new EventSource", self.job_tracker_source)
        self.assertIn("/admin/jobs/${encodeURIComponent(jobId)}/stream", self.job_tracker_source)
        self.assertIn("result-video", self.job_tracker_source)
        self.assertIn("controls: true", self.job_tracker_source)
        self.assertIn("result-image", self.job_tracker_source)
        for source in (self.generate_image_source, self.generate_image_result_source, self.generate_video_source, self.job_tracker_source):
            with self.subTest(source=source[:40]):
                self.assertNotIn("setInterval", source)
                self.assertNotIn("input_json", source)
                self.assertNotIn("output_json", source)
                self.assertNotIn("request_hash", source)
                self.assertNotIn("provider_raw_body", source)
        self.assertIn("navigate('#/assets')", self.jobs_source)
        self.assertNotIn("button(t('jobs.cancel')", self.jobs_source)
        self.assertNotIn("button(t('jobs.retry')", self.jobs_source)
        self.assertIn("jobs.controlsUnavailable", self.jobs_source)
        self.assertIn("flatMap(([key, value])", self.jobs_source)
        self.assertNotIn("setInterval", self.jobs_source)
        self.assertNotIn("setTimeout", self.jobs_source)
        self.assertIn("studio_asset_filter_job_id", self.jobs_source)
        self.assertIn("generateVideo.asyncJobHelp", self.generate_video_source)
        self.assertIn("generateVideo.workerRequiredHelp", self.generate_video_source)
        self.assertIn("generateImage.workerRequiredHelp", read(STUDIO_ROOT / "features" / "generate-image" / "result-preview.js"))
        for key in (
            "jobs.refreshPolled",
            "jobs.refreshCompleted",
            "jobs.refreshDownloadPending",
            "jobs.refreshFailed",
            "jobs.refreshThrottled",
        ):
            with self.subTest(key=key):
                self.assertIn(key, self.i18n_source)
        self.assertIn(".job-actions", self.pages_source)
        self.assertIn("@media (max-width: 400px)", self.pages_source)

    def test_jobs_task_center_has_bounded_mobile_layout_contract(self) -> None:
        for class_name in (
            ".jobs-filter-grid",
            ".job-detail-drawer-layer",
            ".job-detail-drawer-body",
            ".job-event-list",
            ".job-summary-grid",
        ):
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, self.pages_source)
        mobile_match = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.pages_source, re.S)
        self.assertIsNotNone(mobile_match)
        mobile = mobile_match.group("body")
        self.assertIn(".job-row", mobile)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", mobile)
        self.assertIn(".jobs-filter-grid", mobile)

    def test_dashboard_and_assets_have_bounded_mobile_layout_contract(self) -> None:
        for class_name in (
            ".dashboard-asset-list",
            ".dashboard-status-grid",
            ".asset-job-strip",
            ".asset-card-actions",
        ):
            with self.subTest(class_name=class_name):
                self.assertIn(class_name, self.pages_source)
        mobile_match = re.search(r"@media \(max-width: 400px\)\s*\{(?P<body>.*?)\n\}", self.pages_source, re.S)
        self.assertIsNotNone(mobile_match)
        mobile = mobile_match.group("body")
        self.assertIn(".asset-job-strip", mobile)
        self.assertIn(".dashboard-status-grid", mobile)

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
            "openEditProvider",
            "openCreateProvider",
            "openProviderDrawer",
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
        self.assertIn("provider-readonly-section", self.providers_source)
        self.assertIn("provider-readonly-summary", self.providers_source)
        self.assertIn("items.length", self.providers_source)
        self.assertIn("环境变量", self.i18n_source)
        self.assertIn("environment variables", self.i18n_source)
        self.assertIn("provider-readonly-experimental", self.providers_source)
        self.assertIn("provider-readonly-disabled", self.providers_source)

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

    def test_generate_image_catalog_capability_contract(self) -> None:
        """Generate Image should follow the same minimal catalog-aware pattern as Generate Video."""
        generate_image_source = read_feature(STUDIO_ROOT / "features" / "generate-image")
        self.assertIn("api.get('/admin/catalog')", generate_image_source)
        self.assertIn("selectableImageModels", generate_image_source)
        self.assertIn("imageProvidersForModels", generate_image_source)
        self.assertIn("item.media_type === 'image'", self.capabilities_source)
        self.assertIn("item.selectable === true", self.capabilities_source)
        self.assertIn("size_presets", generate_image_source)
        self.assertIn("provider_model", generate_image_source)
        self.assertNotIn("IMAGE_SIZE_PRESETS", generate_image_source)

    def test_generate_image_custom_size_contract(self) -> None:
        self.assertIn("validateCustomSize", self.capabilities_source)
        self.assertIn("custom", self.capabilities_source)

    def test_topbar_account_modal_supports_username_and_password_changes(self) -> None:
        self.assertIn("topbar.account", self.layout_source)
        self.assertIn("openAccountModal", self.layout_source)
        self.assertIn("api.get('/admin/account')", self.layout_source)
        self.assertIn("api.patch('/admin/account'", self.layout_source)
        self.assertNotIn("api.post('/admin/username'", self.layout_source)
        self.assertNotIn("api.post('/admin/password'", self.layout_source)
        self.assertIn("current_password", self.layout_source)
        self.assertIn("new_username", self.layout_source)
        self.assertIn("new_password", self.layout_source)
        self.assertIn("confirm_new_password", self.layout_source)
        self.assertEqual(self.layout_source.count("autocomplete: 'current-password'"), 1)
        self.assertIn("account.combinedSection", self.i18n_source)
        self.assertIn(".account-modal", self.pages_source)
        self.assertIn("clearSession", self.layout_source)
        self.assertIn("account.currentUsername", self.i18n_source)
        self.assertIn("account.saveAccount", self.i18n_source)

    def test_asset_thumbnail_and_preview_object_fit_contract(self) -> None:
        self.assertRegex(self.pages_source, r"\.result-image\s*\{[^}]*object-fit:\s*contain")
        self.assertRegex(self.pages_source, r"\.asset-thumb img\s*\{[^}]*object-fit:\s*contain")
        self.assertRegex(self.pages_source, r"\.asset-thumb video\s*\{[^}]*object-fit:\s*contain")


if __name__ == "__main__":
    unittest.main()

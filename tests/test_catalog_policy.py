"""验证政策来源、时效、默认型号一致性及账户证据隔离。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import yaml
from studio_test_client import TestClient
from test_web_studio_generate_image_operation_controls import run_studio_module_script
from test_web_studio_provider_channels import DOM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import angemedia_gateway.config as C
from angemedia_gateway.providers.catalog.api import catalog_api_response
from angemedia_gateway.providers.catalog.loader import CatalogValidationError, load_provider_catalog
from angemedia_gateway.providers.catalog.policy import parse_policy, policy_api_response
from angemedia_gateway.schemas import VideoRequest
from angemedia_gateway.server import app
from angemedia_gateway.services.provider_runtime_config import ProviderRuntimeConfigService
from angemedia_gateway.state import ensure_default_admin_user, init_db


POLICY = {
    "sources": ["https://example.com/public-policy"], "reviewed_at": "2026-10-26", "review_after": "2026-11-02",
    "lifecycle": "documented", "billing": "promotional", "account_requirements": ["credential", "model_permission"], "notes": "限时优惠，以账户和平台公告为准。",
}


class CatalogPolicyTest(unittest.TestCase):
    def test_review_boundary_does_not_extend_promotional_billing_or_prove_access(self):
        policy = parse_policy("policy", POLICY)
        before = policy_api_response(policy, as_of=date(2026, 10, 25))
        reviewed = policy_api_response(policy, as_of=date(2026, 10, 26))
        expired = policy_api_response(policy, as_of=date(2026, 11, 2))
        self.assertEqual(reviewed["billing"], "promotional")
        self.assertEqual(reviewed["review_status"], "current")
        for item in (before, expired):
            self.assertEqual(item["review_status"], "review_due")
            self.assertEqual(item["billing"], "unknown")
            self.assertEqual(item["reviewed_billing"], "promotional")
            self.assertEqual(item["lifecycle"], "unknown")
        for item in (before, reviewed, expired):
            self.assertFalse(item["account_access_verified"])
            self.assertFalse(item["generation_verified"])
            self.assertEqual(item["evidence_scope"], "public_documentation")

    def test_missing_policy_stays_unknown_and_retirement_does_not_reactivate(self):
        self.assertEqual(policy_api_response(None)["review_status"], "unknown")
        retired = parse_policy("policy", {**POLICY, "lifecycle": "retired"})
        self.assertEqual(policy_api_response(retired, as_of=date(2027, 1, 1))["lifecycle"], "retired")

    def test_invalid_dates_sources_and_requirement_codes_are_rejected(self):
        cases = [
            {"reviewed_at": "2026-02-30"}, {"reviewed_at": "20261026"}, {"review_after": "2026-10-26"},
            {"sources": []}, {"sources": ["javascript:alert(1)"]},
            {"sources": ["https://user:PRIVATE@example.com/docs"]}, {"sources": ["https://example.com/docs?token=PRIVATE"]},
            {"billing": "free_forever"}, {"lifecycle": "available_to_every_account"}, {"account_requirements": ["unknown"]},
            {"unchecked_extra": True},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(CatalogValidationError):
                parse_policy("policy", {**POLICY, **change})

    def test_yaml_dates_and_optional_policy_remain_backward_compatible(self):
        native = {**POLICY, "reviewed_at": date(2026, 10, 26), "review_after": date(2026, 11, 2)}
        self.assertEqual(parse_policy("policy", native).reviewed_at, "2026-10-26")
        catalog = load_provider_catalog()
        self.assertIsNone(catalog.models_by_id["kolors"].policy)
        self.assertEqual(catalog.providers_by_id["modelscope"].policy.billing, "usage_credits")
        projected = catalog_api_response(catalog)
        legacy = next(model for model in projected["models"] if model["id"] == "agnes-video-v2-0")
        self.assertEqual(legacy["policy"]["reviewed_lifecycle"], "legacy_unverified")
        self.assertNotIn("stable_default", legacy["tags"])
        for forbidden in ("api_key", "token", "password", "secret"):
            self.assertNotIn(forbidden, json.dumps(projected).lower())

    def test_invalid_embedded_policy_is_rejected_by_the_actual_loader(self):
        with tempfile.TemporaryDirectory(prefix="catalog-policy-") as folder:
            target = Path(folder)
            for name in ("providers.yaml", "models.yaml"):
                shutil.copyfile(ROOT / "scripts/angemedia_gateway/providers/catalog" / name, target / name)
            values = yaml.safe_load((target / "models.yaml").read_text(encoding="utf-8"))
            values["models"][0]["policy"] = {**POLICY, "review_after": "2026-01-01"}
            (target / "models.yaml").write_text(yaml.safe_dump(values, allow_unicode=True, sort_keys=False), encoding="utf-8")
            with self.assertRaises(CatalogValidationError):
                load_provider_catalog(target)


class VideoDefaultPolicyApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="video-default-policy-")
        self.addCleanup(tmp.cleanup)
        for setting in (patch.object(C, "DB_FILE", Path(tmp.name) / "test.db"), patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_DEFAULT_PASSWORD": "test-password-only"})):
            setting.start()
            self.addCleanup(setting.stop)
        init_db()
        ensure_default_admin_user()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.assertEqual(self.client.post("/v1/admin/login", json={"username": "admin", "password": "test-password-only"}).status_code, 200)

    def test_management_default_matches_request_schema_and_route_preview(self):
        response = self.client.get("/v1/admin/provider-configs")
        video = next(item for item in response.json()["data"] if item["provider_id"] == "agnes_video")
        route = self.client.post("/v1/media/route", json={"prompt": "测试默认型号", "media_type": "video"})
        self.assertEqual(route.status_code, 200, route.text)
        self.assertEqual(video["default_model"], VideoRequest(prompt="测试默认型号").model)
        self.assertEqual(video["default_model"], route.json()["model"])
        self.assertEqual(video["default_model_policy"]["reviewed_lifecycle"], "legacy_unverified")
        self.assertFalse(video["default_model_policy"]["generation_verified"])

    def test_catalog_reordering_and_promotional_records_do_not_switch_defaults(self):
        catalog = load_provider_catalog()
        service = ProviderRuntimeConfigService()
        reordered = replace(catalog, models=tuple(reversed(catalog.models)))
        self.assertEqual(service._default_model("agnes_video", reordered), VideoRequest(prompt="x").model)


@unittest.skipUnless(shutil.which("node"), "需要Node执行前端模块")
class ModelPolicyPresentationTest(unittest.TestCase):
    def test_stale_or_unknown_policy_never_looks_like_a_current_promotion(self):
        result = run_studio_module_script(DOM + r"""
const {renderModelPolicy}=await import('./studio/components/model-policy.js');
const {setLanguage,t}=await import('./studio/i18n.js');
for(const locale of ['zh-CN','en-US']){
 setLanguage(locale);
 for(const status of ['current','review_due','unknown']){
  const view=renderModelPolicy({review_status:status,billing:'promotional'});
  assert.equal(view.textContent,t('modelPolicy.source'));
 }
 assert.equal(renderModelPolicy(undefined).textContent,t('modelPolicy.source'));
 const legacy=renderModelPolicy({review_status:'review_due',reviewed_lifecycle:'legacy_unverified'});
 assert.ok(legacy.textContent.includes(t('modelPolicy.legacyWarning')));
}
console.log(JSON.stringify({ok:true}));
""", {})
        self.assertTrue(result["ok"])

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("IMAGE_PROXY_STATE_DIR", tempfile.mkdtemp(prefix="angemedia-catalog-contract-"))
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.setdefault("AUTO_DOWNLOAD_GENERATED", "false")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_DEFAULT_PASSWORD", "admin123456")

from fastapi.testclient import TestClient  # noqa: E402

from angemedia_gateway.providers.catalog.api import catalog_api_response  # noqa: E402
from angemedia_gateway.providers.catalog.loader import (  # noqa: E402
    CATALOG_DIR,
    CatalogValidationError,
    load_provider_catalog,
)
from angemedia_gateway.providers.catalog.schema import (  # noqa: E402
    VALID_MEDIA_TYPES,
    VALID_MODEL_STATUSES,
    VALID_PROVIDER_STATUSES,
)
from angemedia_gateway.request_hash_builders import build_image_request_hash_payload  # noqa: E402
from angemedia_gateway.routing import DEFAULT_CHAIN, MODEL_ALIASES, resolve_chain  # noqa: E402
from angemedia_gateway.schemas import ImageRequest  # noqa: E402
from angemedia_gateway.server import app  # noqa: E402


@contextmanager
def catalog_copy():
    tmp = Path(tempfile.mkdtemp(prefix="angemedia-catalog-contract-copy-"))
    try:
        shutil.copy(CATALOG_DIR / "providers.yaml", tmp / "providers.yaml")
        shutil.copy(CATALOG_DIR / "models.yaml", tmp / "models.yaml")
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace(old, new, 1)
    if updated == text:
        raise AssertionError(f"expected fixture text not found in {path.name}: {old!r}")
    path.write_text(updated, encoding="utf-8")


class CatalogYamlContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_provider_catalog()

    def test_model_catalog_source_fields_are_complete_and_unique(self) -> None:
        model_ids = [model.id for model in self.catalog.models]
        self.assertEqual(len(model_ids), len(set(model_ids)))
        providers = self.catalog.providers_by_id

        for model in self.catalog.models:
            with self.subTest(model=model.id):
                self.assertTrue(model.id)
                self.assertTrue(model.provider)
                self.assertTrue(model.provider_model)
                self.assertTrue(model.display_name)
                self.assertIn(model.media_type, VALID_MEDIA_TYPES)
                self.assertIn(model.status, VALID_MODEL_STATUSES)
                self.assertIn(model.provider, providers)
                self.assertIn(model.media_type, providers[model.provider].media_types)
                self.assertIsInstance(model.capabilities, dict)
                self.assertIsInstance(model.params, dict)
                self.assertIsInstance(model.ref_inputs, dict)
                self.assertIsInstance(model.size_presets, tuple)
                self.assertTrue(all(isinstance(item, str) and item for item in model.size_presets))
                self.assertTrue(all(isinstance(item, str) and item for item in model.aliases))
                self.assertTrue(all(isinstance(item, str) and item for item in model.extra_allowlist))
                self.assertTrue(all(isinstance(item, str) and item for item in model.tags))
                json.dumps(model.params)
                json.dumps(model.ref_inputs)

    def test_provider_catalog_source_fields_are_complete_unique_and_not_custom(self) -> None:
        provider_ids = [provider.id for provider in self.catalog.providers]
        self.assertEqual(len(provider_ids), len(set(provider_ids)))

        for provider in self.catalog.providers:
            with self.subTest(provider=provider.id):
                self.assertTrue(provider.id)
                self.assertTrue(provider.display_name)
                self.assertIn(provider.status, VALID_PROVIDER_STATUSES)
                self.assertTrue(provider.media_types)
                self.assertTrue(set(provider.media_types).issubset(VALID_MEDIA_TYPES))
                self.assertTrue(provider.adapter_id)
                self.assertTrue(provider.ui_group)
                self.assertNotEqual(provider.ui_group, "custom")
                self.assertFalse(provider.id.startswith("custom:"))
                if provider.status == "reserved":
                    self.assertFalse(provider.enabled_default)

    def test_pollinations_contract_is_experimental_disabled_and_not_default_chain(self) -> None:
        provider = self.catalog.providers_by_id["pollinations"]
        model = self.catalog.models_by_id["pollinations"]
        self.assertEqual(provider.status, "experimental")
        self.assertFalse(provider.enabled_default)
        self.assertEqual(model.status, "experimental")
        self.assertIsNone(model.default_chain_order)
        self.assertNotIn("pollinations", [item.id for item in self.catalog.default_image_chain()])

    def test_loader_rejects_obviously_bad_catalog_data(self) -> None:
        cases = [
            (
                "missing_provider_model",
                "models.yaml",
                "    provider_model: Kwai-Kolors/Kolors\n",
                "",
                "missing key: provider_model",
            ),
            (
                "unknown_capability",
                "models.yaml",
                "      text_to_image: true",
                "      text_to_audio: true",
                "unknown capability",
            ),
            (
                "provider_unknown_key",
                "providers.yaml",
                "    notes: Default image chain entry for Kolors.",
                "    python_import: unsafe\n    notes: Default image chain entry for Kolors.",
                "unknown key",
            ),
        ]

        for name, filename, old, new, pattern in cases:
            with self.subTest(name=name), catalog_copy() as copied:
                replace_once(copied / filename, old, new)
                with self.assertRaisesRegex(CatalogValidationError, pattern):
                    load_provider_catalog(copied)


class CatalogApiProjectionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def login_admin(self) -> None:
        response = self.client.post(
            "/v1/admin/login",
            json={"username": "admin", "password": "admin123456"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_admin_catalog_api_projection_is_safe_and_stable_for_readonly_ui(self) -> None:
        self.login_admin()
        response = self.client.get("/v1/admin/catalog")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["object"], "provider_catalog")
        self.assertIsInstance(body["models"], list)
        self.assertIsInstance(body["providers"], list)

        provider = {item["id"]: item for item in body["providers"]}["agnes_video"]
        self.assertEqual(provider["media_type"], "video")
        self.assertIn("video", provider["media_types"])
        for field in (
            "display_name",
            "status",
            "enabled_default",
            "requires_key",
            "adapter_id",
            "ui_group",
            "selectable",
            "default_chain_order",
        ):
            self.assertIn(field, provider)

        model = {item["id"]: item for item in body["models"]}["agnes-video-v2-0"]
        for field in (
            "provider_id",
            "provider_model",
            "media_type",
            "size_presets",
            "params",
            "ref_inputs",
            "capabilities",
            "extra_allowlist",
        ):
            self.assertIn(field, model)
        self.assertEqual(model["provider_id"], "agnes_video")
        self.assertEqual(model["provider_model"], "agnes-video-v2.0")
        self.assertEqual(model["media_type"], "video")
        self.assertIsInstance(model["size_presets"], list)
        self.assertIsInstance(model["params"], dict)
        self.assertIsInstance(model["ref_inputs"], dict)

        rendered = json.dumps(body, ensure_ascii=False).lower()
        for forbidden in ("api_key", "credential_keys", "password", "secret", "token"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_public_models_are_compatible_with_admin_catalog_without_promoting_pollinations(self) -> None:
        self.login_admin()
        public_response = self.client.get("/v1/models")
        self.assertEqual(public_response.status_code, 200, public_response.text)
        public_models = public_response.json()["data"]
        admin_catalog = catalog_api_response(load_provider_catalog())
        admin_providers = {item["id"]: item for item in admin_catalog["providers"]}
        admin_models = {item["id"]: item for item in admin_catalog["models"]}
        aliases_to_model_id = {
            alias: model_id
            for model_id, model in admin_models.items()
            for alias in model["aliases"]
        }

        public_ids = {item["id"] for item in public_models}
        self.assertNotIn("pollinations", public_ids)
        self.assertNotIn("custom:pollinations", public_ids)

        for item in public_models:
            with self.subTest(model=item["id"]):
                if item["id"].startswith("custom:"):
                    self.assertEqual(item["owned_by"], "custom_provider")
                    continue
                self.assertIn(item["owned_by"], admin_providers)
                if item["id"] in aliases_to_model_id:
                    catalog_model = admin_models[aliases_to_model_id[item["id"]]]
                    self.assertEqual(item["owned_by"], catalog_model["provider_id"])


class RoutingCompatibilityContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_provider_catalog()

    def test_default_image_route_current_chain_matches_release_catalog_order(self) -> None:
        catalog_chain = [
            (model.provider, model.provider_model)
            for model in self.catalog.default_image_chain()
        ]
        runtime_chain = [(target.provider, target.model) for target in DEFAULT_CHAIN]
        self.assertEqual(runtime_chain, catalog_chain)

        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            resolved = [(target.provider, target.model) for target in resolve_chain(None)]
        self.assertEqual(resolved, catalog_chain)
        self.assertNotIn(("pollinations", "zimage"), resolved)

    def test_catalog_aliases_resolve_to_provider_and_provider_model(self) -> None:
        alias_targets = {
            alias: (model.provider, model.provider_model)
            for model in self.catalog.models
            for alias in model.aliases
        }
        documented_legacy_aliases = {
            "qwen-image",
            "flux-krea",
            "z-image-turbo",
            "gpt-image-2",
            "agnes-2.1",
            "agnes-2.0",
        }
        self.assertTrue(documented_legacy_aliases.issubset(alias_targets))

        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            for alias, expected in alias_targets.items():
                with self.subTest(alias=alias):
                    if alias not in MODEL_ALIASES:
                        continue
                    chain = resolve_chain(alias)
                    self.assertEqual(len(chain), 1)
                    self.assertEqual((chain[0].provider, chain[0].model), expected)

    def test_unknown_image_model_keeps_current_modelscope_fallback_behavior(self) -> None:
        with patch("angemedia_gateway.routing.builtin_provider_enabled", return_value=True):
            chain = resolve_chain("not-a-catalog-model")
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].provider, "modelscope")
        self.assertEqual(chain[0].model, "not-a-catalog-model")

    def test_custom_provider_model_override_is_not_a_catalog_route_selector(self) -> None:
        req = ImageRequest(prompt="cat", model="custom:abc", provider_model="kolors")
        result = build_image_request_hash_payload(
            req,
            provider_mode="custom",
            custom_provider_id="abc",
            custom_default_model="default-upstream",
        )
        self.assertIsNotNone(result.payload)
        payload = result.payload or {}
        self.assertEqual(payload["requested_model"], "custom:abc")
        self.assertEqual(payload["provider_model"], "kolors")
        self.assertEqual(payload["custom_provider_id"], "abc")
        self.assertNotIn("resolved_chain", payload)

    def test_known_capability_gaps_are_recorded_without_blocking_current_contract(self) -> None:
        # Known v0.2.1 cleanup targets:
        # - ModelScope size presets are mostly empty, so Studio falls back to custom size.
        # - routing.py still duplicates catalog aliases/default chain/provider_model values.
        # - ref_inputs are catalog data but not first-class Generate Image UI controls yet.
        # - Generate Video renders ref_inputs as disabled placeholders.
        # - catalog-driven validation is not wired into every submit path.
        gap_names = {
            "modelscope_size_presets_incomplete",
            "routing_catalog_duplication",
            "ref_inputs_not_first_class_ui",
            "video_ref_inputs_placeholder",
            "catalog_validation_not_global",
        }
        self.assertIn("routing_catalog_duplication", gap_names)


if __name__ == "__main__":
    unittest.main()

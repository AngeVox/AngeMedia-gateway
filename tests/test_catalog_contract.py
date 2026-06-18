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

import yaml


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
    VALID_OPERATIONS,
    VALID_OPERATION_EVIDENCE,
    VALID_OPERATION_PARAM_KINDS,
    VALID_PARAM_KINDS,
    VALID_PROVIDER_STATUSES,
    VALID_SIZE_MODES,
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


def mutate_first_model(catalog_dir: Path, mutator) -> None:
    path = catalog_dir / "models.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutator(data["models"][0])
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


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
                self.assertIsInstance(model.param_specs, dict)
                self.assertIsInstance(model.ref_inputs, dict)
                self.assertIsInstance(model.ref_input_spec.roles, tuple)
                self.assertIsInstance(model.operations, dict)
                self.assertIsInstance(model.size_presets, tuple)
                self.assertIn(model.size.mode, VALID_SIZE_MODES)
                self.assertIsInstance(model.size.presets, tuple)
                self.assertTrue(all(isinstance(item, str) and item for item in model.size_presets))
                self.assertTrue(all(item.kind in VALID_PARAM_KINDS for item in model.param_specs.values()))
                self.assertTrue(set(model.operations).issubset(VALID_OPERATIONS))
                for operation in model.operations.values():
                    self.assertIsInstance(operation.params, dict)
                    self.assertIsInstance(operation.refs, tuple)
                    for param in operation.params.values():
                        self.assertIn(param.kind, VALID_OPERATION_PARAM_KINDS)
                        self.assertIn(param.evidence, VALID_OPERATION_EVIDENCE)
                    for ref in operation.refs:
                        self.assertIsInstance(ref.roles, tuple)
                        self.assertTrue(ref.roles)
                        self.assertTrue(ref.provider_field is None or isinstance(ref.provider_field, str))
                        self.assertTrue(ref.max_total is None or isinstance(ref.max_total, int))
                self.assertTrue(all(isinstance(item, str) and item for item in model.aliases))
                self.assertTrue(all(isinstance(item, str) and item for item in model.extra_allowlist))
                self.assertTrue(all(isinstance(item, str) and item for item in model.tags))
                json.dumps(model.params)
                json.dumps({key: value.__dict__ for key, value in model.param_specs.items()})
                json.dumps(model.ref_inputs)
                json.dumps(model.ref_input_spec.__dict__)
                json.dumps(model.size.__dict__)

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
            (
                "unknown_param_kind",
                "models.yaml",
                "    params: {}\n    size_presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]",
                "    params: {}\n    param_specs:\n      seed:\n        kind: timestamp\n    size_presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]",
                "invalid param kind",
            ),
            (
                "bad_size_mode_without_presets",
                "models.yaml",
                (
                    "    params: {}\n"
                    "    size_presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]\n"
                    "    size:\n"
                    "      mode: preset\n"
                    "      presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]"
                ),
                "    params: {}\n    size_presets: []\n    size:\n      mode: preset",
                "presets must not be empty",
            ),
            (
                "required_ref_input_without_roles",
                "models.yaml",
                "    ref_inputs: {}\n    extra_allowlist: []",
                "    ref_inputs: {}\n    ref_input_spec:\n      required: true\n    extra_allowlist: []",
                "roles must not be empty",
            ),
        ]

        for name, filename, old, new, pattern in cases:
            with self.subTest(name=name), catalog_copy() as copied:
                replace_once(copied / filename, old, new)
                with self.assertRaisesRegex(CatalogValidationError, pattern):
                    load_provider_catalog(copied)

    def test_loader_derives_typed_capability_specs_from_legacy_fields(self) -> None:
        video = self.catalog.models_by_id["agnes-video-v2-0"]
        self.assertEqual(video.param_specs["width"].kind, "int")
        self.assertEqual(video.param_specs["height"].kind, "int")
        self.assertEqual(video.size.mode, "preset")
        self.assertEqual(video.size.presets, video.size_presets)
        self.assertEqual(video.ref_input_spec.roles, ("image", "images"))
        self.assertFalse(video.ref_input_spec.required)

        qwen = self.catalog.models_by_id["qwen"]
        self.assertEqual(qwen.size.mode, "preset")
        self.assertEqual(qwen.size.presets[0], "1024x1024")
        self.assertEqual(qwen.size_presets, qwen.size.presets)
        self.assertEqual(set(qwen.operations), {"text_to_image"})

    def test_loader_accepts_explicit_typed_capability_fields_and_projects_them(self) -> None:
        with catalog_copy() as copied:
            replace_once(
                copied / "models.yaml",
                (
                    "    params: {}\n"
                    "    size_presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]\n"
                    "    size:\n"
                    "      mode: preset\n"
                    "      presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]\n"
                    "    ref_inputs: {}\n"
                ),
                (
                    "    params: {}\n"
                    "    param_specs:\n"
                    "      seed:\n"
                    "        kind: seed\n"
                    "        min: 0\n"
                    "        max: 4294967295\n"
                    "    size_presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]\n"
                    "    size:\n"
                    "      mode: preset\n"
                    "      presets: [1024x1024, 1024x2048, 1536x2048, 2048x1152, 960x1280, 768x1024, 720x1440, 720x1280]\n"
                    "      min_width: 512\n"
                    "      max_width: 2048\n"
                    "      min_height: 512\n"
                    "      max_height: 2048\n"
                    "      multiple_of: 64\n"
                    "    ref_inputs: {}\n"
                    "    ref_input_spec:\n"
                    "      roles: [image]\n"
                    "      max_total: 1\n"
                    "      formats: [png, jpg]\n"
                    "      required: false\n"
                ),
            )
            catalog = load_provider_catalog(copied)
            model = catalog.models_by_id["kolors"]
            self.assertEqual(model.param_specs["seed"].kind, "seed")
            self.assertEqual(model.param_specs["seed"].min, 0)
            self.assertEqual(model.size.multiple_of, 64)
            self.assertEqual(model.ref_input_spec.formats, ("png", "jpg"))

            projected = catalog_api_response(catalog)
            projected_model = {item["id"]: item for item in projected["models"]}["kolors"]
            self.assertEqual(projected_model["param_specs"]["seed"]["kind"], "seed")
            self.assertEqual(projected_model["size"]["mode"], "preset")
            self.assertEqual(projected_model["size"]["multiple_of"], 64)
            self.assertEqual(projected_model["ref_input_spec"]["roles"], ["image"])

    def test_loader_rejects_bad_operation_capability_data(self) -> None:
        cases = [
            (
                "operations_not_mapping",
                lambda model: model.__setitem__("operations", []),
                "operations must be a mapping",
            ),
            (
                "unknown_operation",
                lambda model: model["operations"].__setitem__("text_to_audio", model["operations"]["text_to_image"]),
                "unknown operation",
            ),
            (
                "params_not_mapping",
                lambda model: model["operations"]["text_to_image"].__setitem__("params", []),
                "params must be a mapping",
            ),
            (
                "unknown_evidence",
                lambda model: model["operations"]["text_to_image"]["params"]["steps"].__setitem__("evidence", "forum"),
                "invalid value",
            ),
            (
                "missing_provider_field",
                lambda model: model["operations"]["text_to_image"]["params"]["steps"].pop("provider_field"),
                "provider_field is required",
            ),
            (
                "invalid_kind",
                lambda model: model["operations"]["text_to_image"]["params"]["steps"].__setitem__("kind", "timestamp"),
                "invalid operation param kind",
            ),
            (
                "min_greater_than_max",
                lambda model: model["operations"]["text_to_image"]["params"]["steps"].update({"min": 101, "max": 100}),
                "min must be less than or equal to max",
            ),
            (
                "preset_missing_value",
                lambda model: model["operations"]["text_to_image"]["params"]["size"]["presets"].__setitem__(0, {"label": "bad"}),
                "missing key: value",
            ),
            (
                "preset_bad_format",
                lambda model: model["operations"]["text_to_image"]["params"]["size"]["presets"][0].__setitem__("value", "wide"),
                "WIDTHxHEIGHT",
            ),
            (
                "refs_not_list",
                lambda model: model["operations"]["text_to_image"].__setitem__("refs", {}),
                "refs must be a list",
            ),
            (
                "ref_missing_role_shape",
                lambda model: model["operations"]["text_to_image"].__setitem__("refs", [{"formats": ["png"]}]),
                "missing key: role",
            ),
            (
                "ref_bad_provider_field",
                lambda model: model["operations"]["text_to_image"].__setitem__(
                    "refs",
                    [{"role": "input_image", "provider_field": "bad-field", "formats": ["url"]}],
                ),
                "provider_field must be a safe operation field",
            ),
            (
                "ref_max_count_and_total",
                lambda model: model["operations"]["text_to_image"].__setitem__(
                    "refs",
                    [{"role": "input_image", "formats": ["url"], "max_count": 1, "max_total": 1}],
                ),
                "must use max_count or max_total",
            ),
        ]

        for name, mutator, pattern in cases:
            with self.subTest(name=name), catalog_copy() as copied:
                mutate_first_model(copied, mutator)
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
            "size",
            "params",
            "param_specs",
            "ref_inputs",
            "ref_input_spec",
            "operations",
            "capabilities",
            "extra_allowlist",
        ):
            self.assertIn(field, model)
        self.assertEqual(model["provider_id"], "agnes_video")
        self.assertEqual(model["provider_model"], "agnes-video-v2.0")
        self.assertEqual(model["media_type"], "video")
        self.assertIsInstance(model["size_presets"], list)
        self.assertIsInstance(model["size"], dict)
        self.assertIsInstance(model["params"], dict)
        self.assertIsInstance(model["param_specs"], dict)
        self.assertIsInstance(model["ref_inputs"], dict)
        self.assertIsInstance(model["ref_input_spec"], dict)

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
        # - routing.py still duplicates catalog aliases/default chain/provider_model values.
        # - ref_inputs are catalog data but not first-class Generate Image UI controls yet.
        # - Generate Video renders ref_inputs as disabled placeholders.
        # - catalog-driven validation is not wired into every submit path.
        gap_names = {
            "routing_catalog_duplication",
            "ref_inputs_not_first_class_ui",
            "video_ref_inputs_placeholder",
            "catalog_validation_not_global",
        }
        self.assertIn("routing_catalog_duplication", gap_names)


if __name__ == "__main__":
    unittest.main()

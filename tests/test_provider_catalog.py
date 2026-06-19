from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.catalog.api import catalog_api_response  # noqa: E402
from angemedia_gateway.providers.catalog.loader import (  # noqa: E402
    CATALOG_DIR,
    CatalogValidationError,
    load_provider_catalog,
)


class ProviderCatalogTest(unittest.TestCase):
    def test_default_catalog_loads(self) -> None:
        catalog = load_provider_catalog()
        self.assertIn("siliconflow", catalog.providers_by_id)
        self.assertIn("modelscope", catalog.providers_by_id)
        self.assertIn("agnes_video", catalog.providers_by_id)

    def test_default_image_chain_matches_runtime_contract(self) -> None:
        catalog = load_provider_catalog()
        self.assertEqual(
            [model.id for model in catalog.default_image_chain()],
            ["kolors", "qwen", "flux", "z-image", "z-turbo"],
        )

    def test_pollinations_is_experimental_default_disabled_and_not_in_chain(self) -> None:
        catalog = load_provider_catalog()
        provider = catalog.providers_by_id["pollinations"]
        model = catalog.models_by_id["pollinations"]
        self.assertEqual(provider.status, "experimental")
        self.assertFalse(provider.enabled_default)
        self.assertIsNone(model.default_chain_order)
        self.assertNotIn("pollinations", [item.id for item in catalog.default_image_chain()])

    def test_bytedance_seedream_pilot_is_experimental_and_not_in_default_chain(self) -> None:
        catalog = load_provider_catalog()
        provider = catalog.providers_by_id["bytedance"]
        model = catalog.models_by_id["seedream-3"]
        self.assertEqual(provider.status, "experimental")
        self.assertFalse(provider.enabled_default)
        self.assertEqual(provider.adapter_id, "bytedance")
        self.assertTrue(model.selectable)
        self.assertEqual(model.status, "experimental")
        self.assertIsNone(model.default_chain_order)
        self.assertNotIn("seedream-3", [item.id for item in catalog.default_image_chain()])

    def test_agnes_video_is_release_path_video_provider(self) -> None:
        catalog = load_provider_catalog()
        provider = catalog.providers_by_id["agnes_video"]
        model = catalog.models_by_id["agnes-video-v2-0"]
        self.assertEqual(provider.status, "release")
        self.assertIn("video", provider.media_types)
        self.assertEqual(model.media_type, "video")
        self.assertIn("release_path", model.tags)

    def test_catalog_api_response_projects_safe_capability_fields(self) -> None:
        response = catalog_api_response(load_provider_catalog())
        providers = {item["id"]: item for item in response["providers"]}
        models = {item["id"]: item for item in response["models"]}

        self.assertEqual(response["object"], "provider_catalog")
        self.assertEqual(providers["agnes_video"]["media_type"], "video")
        self.assertIn("video", providers["agnes_video"]["media_types"])
        self.assertEqual(providers["pollinations"]["status"], "experimental")
        self.assertFalse(providers["pollinations"]["enabled_default"])
        self.assertEqual(models["agnes-video-v2-0"]["media_type"], "video")
        self.assertIn("params", models["agnes-video-v2-0"])
        self.assertIn("param_specs", models["agnes-video-v2-0"])
        self.assertIn("ref_inputs", models["agnes-video-v2-0"])
        self.assertIn("ref_input_spec", models["agnes-video-v2-0"])
        self.assertIn("size_presets", models["agnes-video-v2-0"])
        self.assertIn("size", models["agnes-video-v2-0"])
        self.assertIn("operations", models["agnes-video-v2-0"])
        self.assertIn("capabilities", models["agnes-video-v2-0"])
        self.assertEqual(models["agnes-video-v2-0"]["param_specs"]["width"]["kind"], "int")
        self.assertEqual(models["agnes-video-v2-0"]["size"]["mode"], "preset")
        self.assertEqual(models["agnes-video-v2-0"]["ref_input_spec"]["roles"], ["image", "images"])
        self.assertEqual(models["kolors"]["operations"]["text_to_image"]["params"]["size"]["provider_field"], "image_size")
        self.assertEqual(models["kolors"]["operations"]["image_to_image"]["refs"][0]["provider_field"], "image")
        self.assertEqual(models["kolors"]["operations"]["image_to_image"]["refs"][0]["max_count"], 1)
        self.assertEqual(models["qwen"]["operations"]["text_to_image"]["params"]["size"]["provider_field"], "size")
        self.assertEqual(models["agnes-2-0"]["size"]["mode"], "freeform")
        self.assertIn("1280x720", models["agnes-2-0"]["size_presets"])
        self.assertIn("720x1280", models["agnes-2-1"]["size_presets"])
        self.assertIn("seed", models["agnes-2-0"]["operations"]["text_to_image"]["params"])
        self.assertNotIn("seed", models["agnes-2-1"]["operations"]["text_to_image"]["params"])
        self.assertEqual(
            models["agnes-2-1"]["operations"]["image_to_image"]["refs"][0]["provider_format"],
            "data_url",
        )

        response_text = str(response).lower()
        for forbidden in ("credential_keys", "api_key", "token", "password", "secret"):
            self.assertNotIn(forbidden, response_text)

    def test_loader_uses_safe_load(self) -> None:
        import yaml

        with patch("angemedia_gateway.providers.catalog.loader.yaml.safe_load", wraps=yaml.safe_load) as safe_load:
            load_provider_catalog()
        self.assertGreaterEqual(safe_load.call_count, 2)

    def test_unknown_provider_key_is_rejected(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._replace_text(catalog_dir / "providers.yaml", "notes: Default", "python_import: bad\n    notes: Default")
            with self.assertRaisesRegex(CatalogValidationError, "unknown key"):
                load_provider_catalog(catalog_dir)

    def test_duplicate_provider_id_is_rejected(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._append_provider(catalog_dir, "siliconflow")
            with self.assertRaisesRegex(CatalogValidationError, "duplicate provider id"):
                load_provider_catalog(catalog_dir)

    def test_duplicate_model_id_is_rejected(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._append_model(catalog_dir, "kolors", provider="siliconflow")
            with self.assertRaisesRegex(CatalogValidationError, "duplicate model id"):
                load_provider_catalog(catalog_dir)

    def test_invalid_status_is_rejected(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._replace_text(catalog_dir / "providers.yaml", "status: release", "status: beta", count=1)
            with self.assertRaisesRegex(CatalogValidationError, "invalid value"):
                load_provider_catalog(catalog_dir)

    def test_invalid_media_type_is_rejected(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._replace_text(catalog_dir / "models.yaml", "media_type: image", "media_type: audio", count=1)
            with self.assertRaisesRegex(CatalogValidationError, "invalid value"):
                load_provider_catalog(catalog_dir)

    def test_model_referencing_unknown_provider_is_rejected(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._replace_text(catalog_dir / "models.yaml", "provider: siliconflow", "provider: missing_provider", count=1)
            with self.assertRaisesRegex(CatalogValidationError, "unknown provider"):
                load_provider_catalog(catalog_dir)

    def test_reserved_model_cannot_enter_default_chain(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._replace_text(catalog_dir / "models.yaml", "status: release", "status: reserved", count=1)
            with self.assertRaisesRegex(CatalogValidationError, "cannot enter default chain"):
                load_provider_catalog(catalog_dir)

    def test_adapter_id_cannot_be_python_import_path(self) -> None:
        with self._catalog_copy() as catalog_dir:
            self._replace_text(catalog_dir / "providers.yaml", "adapter_id: siliconflow", "adapter_id: providers.image.siliconflow", count=1)
            with self.assertRaisesRegex(CatalogValidationError, "safe registry id"):
                load_provider_catalog(catalog_dir)

    def _catalog_copy(self):
        return _CatalogCopy()

    def _replace_text(self, path: Path, old: str, new: str, *, count: int = -1) -> None:
        text = path.read_text(encoding="utf-8")
        updated = text.replace(old, new, count)
        self.assertNotEqual(text, updated)
        path.write_text(updated, encoding="utf-8")

    def _append_provider(self, catalog_dir: Path, provider_id: str) -> None:
        with (catalog_dir / "providers.yaml").open("a", encoding="utf-8") as fh:
            fh.write(
                "\n"
                f"  - id: {provider_id}\n"
                "    display_name: Duplicate\n"
                "    media_types: [image]\n"
                "    status: release\n"
                "    enabled_default: true\n"
                "    config_enabled_key: null\n"
                "    requires_key: false\n"
                "    credential_keys: []\n"
                "    adapter_id: duplicate\n"
                "    ui_group: test\n"
                "    notes: duplicate\n"
            )

    def _append_model(self, catalog_dir: Path, model_id: str, *, provider: str) -> None:
        with (catalog_dir / "models.yaml").open("a", encoding="utf-8") as fh:
            fh.write(
                "\n"
                f"  - id: {model_id}\n"
                f"    provider: {provider}\n"
                "    provider_model: duplicate-model\n"
                "    media_type: image\n"
                "    display_name: Duplicate\n"
                "    aliases: []\n"
                "    status: release\n"
                "    selectable: true\n"
                "    default_chain_order: null\n"
                "    capabilities:\n"
                "      text_to_image: true\n"
                "    params: {}\n"
                "    size_presets: []\n"
                "    ref_inputs: {}\n"
                "    extra_allowlist: []\n"
                "    tags: []\n"
            )


class _CatalogCopy:
    def __enter__(self) -> Path:
        self._tmp = Path(tempfile.mkdtemp(prefix="angemedia-catalog-test-"))
        shutil.copy(CATALOG_DIR / "providers.yaml", self._tmp / "providers.yaml")
        shutil.copy(CATALOG_DIR / "models.yaml", self._tmp / "models.yaml")
        return self._tmp

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

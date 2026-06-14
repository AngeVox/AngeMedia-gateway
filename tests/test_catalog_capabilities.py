from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway import config as C  # noqa: E402
from angemedia_gateway.providers.catalog.api import catalog_api_response  # noqa: E402
from angemedia_gateway.providers.catalog.loader import load_provider_catalog  # noqa: E402


CAPABILITIES_JS = ROOT / "app" / "www" / "assets" / "studio" / "lib" / "capabilities.js"
SIZE_CONTROLS_JS = ROOT / "app" / "www" / "assets" / "studio" / "features" / "generate-image" / "size-controls.js"


class CatalogCapabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_provider_catalog()
        cls.api_models = {
            item["id"]: item
            for item in catalog_api_response(cls.catalog)["models"]
        }

    def test_modelscope_default_chain_models_do_not_claim_verified_size_control(self) -> None:
        for model_id in ("qwen", "flux", "z-image", "z-turbo"):
            with self.subTest(model=model_id):
                model = self.catalog.models_by_id[model_id]
                self.assertEqual(model.provider, "modelscope")
                self.assertEqual(model.media_type, "image")
                self.assertEqual(model.size.mode, "freeform")
                self.assertEqual(model.size.presets, ())
                self.assertEqual(model.size_presets, ())
                self.assertIsNone(model.size.multiple_of)
                self.assertIsNone(model.size.min_width)
                self.assertIsNone(model.size.min_height)

                projected = self.api_models[model_id]
                self.assertEqual(projected["size"]["mode"], "freeform")
                self.assertEqual(projected["size"]["presets"], projected["size_presets"])
                self.assertEqual(projected["size_presets"], [])

    def test_release_image_models_without_verified_size_control_are_explicitly_limited(self) -> None:
        unverified_size_models = [
            model.id
            for model in self.catalog.models
            if model.media_type == "image"
            and model.status == "release"
            and model.selectable
            and not model.size_presets
        ]
        self.assertEqual(unverified_size_models, ["qwen", "flux", "z-image", "z-turbo"])

    def test_kolors_catalog_presets_match_runtime_adapter_allowlist(self) -> None:
        kolors = self.catalog.models_by_id["kolors"]
        self.assertEqual(kolors.provider, "siliconflow")
        self.assertEqual(set(kolors.size_presets), C.KOLORS_SIZES)
        self.assertEqual(kolors.size.mode, "preset")
        self.assertEqual(kolors.size.presets, kolors.size_presets)

    def test_generate_image_size_options_remain_catalog_driven_with_custom_override(self) -> None:
        capabilities_source = CAPABILITIES_JS.read_text(encoding="utf-8")
        size_controls_source = SIZE_CONTROLS_JS.read_text(encoding="utf-8")
        self.assertIn("imageSizeOptions(model)", size_controls_source)
        self.assertIn("model?.size_presets", capabilities_source)
        self.assertIn("{ value: 'custom', label: 'Custom' }", capabilities_source)
        self.assertIn("validateCustomSize", size_controls_source)

    def test_default_image_submit_contract_remains_model_and_size_based(self) -> None:
        qwen = self.api_models["qwen"]
        self.assertEqual(qwen["provider_id"], "modelscope")
        self.assertEqual(qwen["provider_model"], "Qwen/Qwen-Image-2512")
        self.assertEqual(qwen["size_presets"], [])
        self.assertEqual(qwen["size"]["presets"], [])
        self.assertNotIn("provider", qwen)


if __name__ == "__main__":
    unittest.main()

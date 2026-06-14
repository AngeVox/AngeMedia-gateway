from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway import config as C  # noqa: E402
from angemedia_gateway.providers.catalog.api import catalog_api_response  # noqa: E402
from angemedia_gateway.providers.catalog.loader import load_provider_catalog  # noqa: E402
from angemedia_gateway.providers.catalog.validation import (  # noqa: E402
    CatalogOperationValidationError,
    operation_provider_field_map,
    validate_operation_params,
)
from angemedia_gateway.schemas import ImageRequest  # noqa: E402


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

    def test_kolors_text_to_image_operation_metadata_is_projected_safely(self) -> None:
        kolors = self.catalog.models_by_id["kolors"]
        operation = kolors.operations["text_to_image"]

        self.assertTrue(operation.supported)
        self.assertEqual(operation.refs, ())
        self.assertEqual(operation.params["prompt"].provider_field, "prompt")
        self.assertEqual(operation.params["size"].provider_field, "image_size")
        self.assertEqual(operation.params["negative_prompt"].provider_field, "negative_prompt")
        self.assertEqual(operation.params["seed"].provider_field, "seed")
        self.assertEqual(operation.params["seed"].min, 0)
        self.assertEqual(operation.params["seed"].max, 9999999999)
        self.assertEqual(operation.params["steps"].provider_field, "num_inference_steps")
        self.assertEqual(operation.params["steps"].min, 1)
        self.assertEqual(operation.params["steps"].max, 100)
        self.assertEqual(operation.params["steps"].default, 20)
        self.assertEqual(operation.params["guidance"].provider_field, "guidance_scale")
        self.assertEqual(operation.params["guidance"].min, 0)
        self.assertEqual(operation.params["guidance"].max, 20)
        self.assertEqual(operation.params["guidance"].default, 7.5)
        self.assertEqual(
            [preset.value for preset in operation.params["size"].presets],
            list(kolors.size_presets),
        )

        projected = self.api_models["kolors"]["operations"]["text_to_image"]
        self.assertEqual(projected["params"]["size"]["presets"][0], {"value": "1024x1024", "label": "1:1"})
        self.assertEqual(projected["params"]["steps"]["provider_field"], "num_inference_steps")
        self.assertEqual(projected["params"]["guidance"]["provider_field"], "guidance_scale")
        self.assertEqual(projected["refs"], [])
        rendered = str(projected).lower()
        for forbidden in ("api_key", "credential", "secret", "token"):
            self.assertNotIn(forbidden, rendered)

    def test_non_kolors_models_have_no_operation_metadata_yet(self) -> None:
        for model_id in ("qwen", "flux", "z-image", "z-turbo", "agnes-2-1"):
            with self.subTest(model=model_id):
                self.assertEqual(self.catalog.models_by_id[model_id].operations, {})
                self.assertEqual(self.api_models[model_id]["operations"], {})

    def test_kolors_operation_validator_accepts_valid_and_rejects_out_of_range_params(self) -> None:
        kolors = self.catalog.models_by_id["kolors"]
        validate_operation_params(
            ImageRequest(
                prompt="cat",
                model="kolors",
                size="1024x1024",
                negative_prompt="blur",
                seed=123,
                steps=20,
                guidance=7.5,
            ),
            kolors,
            "text_to_image",
        )

        invalid_cases = [
            {"steps": 200},
            {"guidance": 999},
            {"seed": -1},
            {"size": "1536x1024"},
        ]
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                payload = {"prompt": "cat", "model": "kolors", "size": "1024x1024"}
                payload.update(overrides)
                with self.assertRaises(CatalogOperationValidationError):
                    validate_operation_params(ImageRequest(**payload), kolors, "text_to_image")

    def test_kolors_operation_provider_field_map_matches_siliconflow_payload(self) -> None:
        kolors = self.catalog.models_by_id["kolors"]
        self.assertEqual(
            operation_provider_field_map(kolors, "text_to_image"),
            {
                "prompt": "prompt",
                "size": "image_size",
                "negative_prompt": "negative_prompt",
                "seed": "seed",
                "steps": "num_inference_steps",
                "guidance": "guidance_scale",
            },
        )

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

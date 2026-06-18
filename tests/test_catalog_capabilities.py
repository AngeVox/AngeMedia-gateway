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

    def test_modelscope_default_chain_models_project_verified_size_controls(self) -> None:
        expected = {
            "qwen": ("preset", ("1024x1024", "1328x1328", "1664x928", "928x1664", "1472x1104", "1104x1472", "1584x1056", "1056x1584")),
            "flux": ("preset", ("1024x1024",)),
            "z-image": ("freeform", ("1024x1024", "1280x720", "720x1280")),
            "z-turbo": ("preset", ("1024x1024",)),
        }
        for model_id, (mode, presets) in expected.items():
            with self.subTest(model=model_id):
                model = self.catalog.models_by_id[model_id]
                self.assertEqual(model.provider, "modelscope")
                self.assertEqual(model.media_type, "image")
                self.assertEqual(model.size.mode, mode)
                self.assertEqual(model.size.presets, presets)
                self.assertEqual(model.size_presets, presets)

                projected = self.api_models[model_id]
                self.assertEqual(projected["size"]["mode"], mode)
                self.assertEqual(projected["size"]["presets"], projected["size_presets"])
                self.assertEqual(projected["size_presets"], list(presets))

        z_image = self.catalog.models_by_id["z-image"]
        self.assertEqual(z_image.size.min_width, 64)
        self.assertEqual(z_image.size.max_width, 2048)
        self.assertEqual(z_image.size.min_height, 64)
        self.assertEqual(z_image.size.max_height, 2048)
        self.assertEqual(z_image.size.min_pixels, 512 * 512)
        self.assertEqual(z_image.size.max_pixels, 2048 * 2048)

    def test_release_image_models_have_verified_size_controls(self) -> None:
        unverified_size_models = [
            model.id
            for model in self.catalog.models
            if model.media_type == "image"
            and model.status == "release"
            and model.selectable
            and not model.size_presets
        ]
        self.assertEqual(unverified_size_models, [])

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

    def test_kolors_image_to_image_operation_declares_single_url_reference(self) -> None:
        kolors = self.catalog.models_by_id["kolors"]
        operation = kolors.operations["image_to_image"]

        self.assertTrue(kolors.capabilities["image_to_image"])
        self.assertTrue(operation.supported)
        self.assertEqual(
            [preset.value for preset in operation.params["size"].presets],
            list(kolors.size_presets),
        )
        self.assertEqual(len(operation.refs), 1)
        ref = operation.refs[0]
        self.assertEqual(ref.roles, ("input_image",))
        self.assertEqual(ref.provider_field, "image")
        self.assertEqual(ref.formats, ("url",))
        self.assertTrue(ref.required)
        self.assertEqual(ref.max_total, 1)

        projected = self.api_models["kolors"]["operations"]["image_to_image"]
        self.assertEqual(projected["refs"][0]["roles"], ["input_image"])
        self.assertEqual(projected["refs"][0]["provider_field"], "image")
        self.assertEqual(projected["refs"][0]["max_count"], 1)
        self.assertEqual(projected["refs"][0]["formats"], ["url"])
        self.assertTrue(projected["refs"][0]["required"])
        rendered = str(projected).lower()
        for forbidden in ("api_key", "credential", "secret", "token"):
            self.assertNotIn(forbidden, rendered)

    def test_modelscope_operations_declare_only_supported_submit_fields(self) -> None:
        for model_id in ("qwen", "flux", "z-image", "z-turbo"):
            with self.subTest(model=model_id):
                operation = self.catalog.models_by_id[model_id].operations["text_to_image"]
                self.assertEqual(set(operation.params), {"prompt", "size"})
                self.assertEqual(operation.params["prompt"].provider_field, "prompt")
                self.assertEqual(operation.params["size"].provider_field, "size")
                self.assertEqual(operation.refs, ())
                self.assertEqual(set(self.api_models[model_id]["operations"]), {"text_to_image"})

    def test_agnes_image_operations_match_documented_capabilities(self) -> None:
        expected_sizes = {
            "agnes-2-0": ("1024x768", "1024x1024", "768x1024", "1280x720", "2048x1536"),
            "agnes-2-1": (
                "1024x768", "1024x1024", "768x1024", "1280x720",
                "720x1280", "1536x1024", "1024x1536",
            ),
        }
        expected_bounds = {
            "agnes-2-0": (512, 2048, 3_145_728),
            "agnes-2-1": (512, 2560, 4_194_304),
        }
        for model_id, presets in expected_sizes.items():
            with self.subTest(model=model_id):
                model = self.catalog.models_by_id[model_id]
                self.assertEqual(model.provider, "agnes_image")
                self.assertEqual(model.media_type, "image")
                self.assertEqual(model.size.mode, "freeform")
                self.assertEqual(model.size_presets, presets)
                minimum, maximum, max_pixels = expected_bounds[model_id]
                self.assertEqual((model.size.min_width, model.size.min_height), (minimum, minimum))
                self.assertEqual((model.size.max_width, model.size.max_height), (maximum, maximum))
                self.assertEqual(model.size.min_pixels, 512 * 512)
                self.assertEqual(model.size.max_pixels, max_pixels)
                self.assertEqual(set(model.operations), {"text_to_image", "image_to_image"})
                for operation_name in ("text_to_image", "image_to_image"):
                    operation = model.operations[operation_name]
                    self.assertTrue(operation.supported)
                    self.assertEqual(operation.params["prompt"].provider_field, "prompt")
                    self.assertEqual(operation.params["size"].provider_field, "size")
                    self.assertEqual(
                        tuple(preset.value for preset in operation.params["size"].presets),
                        presets,
                    )
                    self.assertEqual(operation.params["size"].mode, "freeform")

                ref = model.operations["image_to_image"].refs[0]
                self.assertEqual(ref.provider_field, "image")
                self.assertEqual(ref.roles, ("images",))
                self.assertEqual(ref.formats, ("url", "data_url"))
                self.assertEqual(ref.provider_format, "data_url")
                self.assertEqual(ref.max_total, 4)
                self.assertTrue(ref.required)

        agnes_20 = self.catalog.models_by_id["agnes-2-0"]
        agnes_21 = self.catalog.models_by_id["agnes-2-1"]
        self.assertIn("seed", agnes_20.operations["text_to_image"].params)
        self.assertIn("seed", agnes_20.operations["image_to_image"].params)
        self.assertNotIn("seed", agnes_21.operations["text_to_image"].params)
        self.assertNotIn("seed", agnes_21.operations["image_to_image"].params)

        projected_20 = self.api_models["agnes-2-0"]["operations"]
        projected_21 = self.api_models["agnes-2-1"]["operations"]
        self.assertIn("seed", projected_20["text_to_image"]["params"])
        self.assertNotIn("seed", projected_21["text_to_image"]["params"])
        self.assertEqual(projected_20["image_to_image"]["refs"][0]["provider_format"], "data_url")

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

    def test_modelscope_operation_validation_rejects_unverified_params_and_sizes(self) -> None:
        qwen = self.catalog.models_by_id["qwen"]
        validate_operation_params(
            ImageRequest(prompt="cat", model="qwen", size="1664x928"),
            qwen,
            "text_to_image",
        )
        for overrides in ({"size": "512x512"}, {"seed": 42}, {"steps": 20}, {"guidance": 4.0}):
            with self.subTest(model="qwen", overrides=overrides):
                payload = {"prompt": "cat", "model": "qwen", "size": "1024x1024"}
                payload.update(overrides)
                with self.assertRaises(CatalogOperationValidationError):
                    validate_operation_params(ImageRequest(**payload), qwen, "text_to_image")

        z_image = self.catalog.models_by_id["z-image"]
        validate_operation_params(
            ImageRequest(prompt="cat", model="z-image", size="720x1280"),
            z_image,
            "text_to_image",
        )
        for size in ("63x1024", "2049x1024", "256x256"):
            with self.subTest(model="z-image", size=size):
                with self.assertRaises(CatalogOperationValidationError):
                    validate_operation_params(
                        ImageRequest(prompt="cat", model="z-image", size=size),
                        z_image,
                        "text_to_image",
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
        self.assertEqual(qwen["size_presets"][0], "1024x1024")
        self.assertEqual(qwen["size"]["presets"], qwen["size_presets"])
        self.assertNotIn("provider", qwen)


if __name__ == "__main__":
    unittest.main()

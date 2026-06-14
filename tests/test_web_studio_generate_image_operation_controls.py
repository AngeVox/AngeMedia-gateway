from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from angemedia_gateway.providers.catalog.api import catalog_api_response  # noqa: E402
from angemedia_gateway.providers.catalog.loader import load_provider_catalog  # noqa: E402


FEATURE_DIR = ROOT / "app" / "www" / "assets" / "studio" / "features" / "generate-image"


def run_operation_helper_script(script: str, payload: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="angemedia-operation-ui-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        for name in ("operation-capabilities.js", "operation-payload.js"):
            shutil.copy(FEATURE_DIR / name, tmp_dir / name)
        script_path = tmp_dir / "script.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(script_path)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout or "{}")


class GenerateImageOperationHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        catalog = catalog_api_response(load_provider_catalog())
        cls.models = {item["id"]: item for item in catalog["models"]}

    def test_kolors_operation_helpers_use_catalog_size_labels_params_and_refs(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import {
              hasOperationRefs,
              operationRefs,
              sizeOptionsForModel,
              supportedParamNames,
            } from './operation-capabilities.js';

            const { kolors } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const sizeOptions = sizeOptionsForModel(kolors);
            assert.deepEqual(sizeOptions.slice(0, 2), [
              { value: '1024x1024', label: '1:1 - 1024x1024' },
              { value: '960x1280', label: '3:4 - 960x1280' },
            ]);
            assert.equal(sizeOptions.at(-1).value, 'custom');
            const names = supportedParamNames(kolors);
            for (const name of ['negative_prompt', 'seed', 'steps', 'guidance']) {
              assert.ok(names.includes(name), `${name} should be catalog-supported`);
            }
            assert.equal(hasOperationRefs(kolors), false);
            assert.deepEqual(operationRefs(kolors), []);
            console.log(JSON.stringify({ ok: true, count: sizeOptions.length }));
            """
        )
        result = run_operation_helper_script(script, {"kolors": self.models["kolors"]})
        self.assertEqual(result["count"], 6)

    def test_models_without_operations_do_not_expose_kolors_params_or_size_presets(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { sizeOptionsForModel, supportedParamNames } from './operation-capabilities.js';
            import { buildOperationPayload } from './operation-payload.js';

            const { qwen } = JSON.parse(fs.readFileSync(0, 'utf8'));
            assert.deepEqual(supportedParamNames(qwen), []);
            assert.deepEqual(sizeOptionsForModel(qwen), [{ value: 'custom', label: 'Custom' }]);
            assert.deepEqual(buildOperationPayload(qwen, {
              negative_prompt: 'old',
              seed: '3',
              steps: '20',
              guidance: '7.5',
            }), {});
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_operation_helper_script(script, {"qwen": self.models["qwen"]})["ok"])

    def test_operation_payload_filters_by_current_model_and_ignores_default_or_custom_route(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { buildOperationPayload } from './operation-payload.js';

            const { kolors, qwen } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const staleValues = {
              negative_prompt: 'blur',
              seed: '123',
              steps: '30',
              guidance: '8.5',
              unsupported: 'drop-me',
              size: '1024x1024',
            };
            assert.deepEqual(buildOperationPayload(kolors, staleValues), {
              negative_prompt: 'blur',
              seed: 123,
              steps: 30,
              guidance: 8.5,
            });
            assert.deepEqual(buildOperationPayload(qwen, staleValues), {});
            assert.deepEqual(buildOperationPayload(null, staleValues), {});
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_operation_helper_script(script, {
            "kolors": self.models["kolors"],
            "qwen": self.models["qwen"],
        })["ok"])

    def test_generate_image_page_does_not_hardcode_kolors_capabilities(self) -> None:
        source = (FEATURE_DIR / "page.js").read_text(encoding="utf-8").lower()
        self.assertNotIn("kolors", source)
        self.assertNotIn("kwai-kolors", source)


if __name__ == "__main__":
    unittest.main()

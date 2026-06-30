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
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout or "{}")


def run_studio_module_script(script: str, payload: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="angemedia-operation-page-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        shutil.copytree(ROOT / "app" / "www" / "assets" / "studio", tmp_dir / "studio")
        script_path = tmp_dir / "script.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(script_path)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stderr or result.stdout)
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
              imageReferenceSpecs,
              operationRefs,
              sizeOptionsForModel,
              supportsImageReference,
              supportedParamNames,
            } from './operation-capabilities.js';

            const { kolors } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const sizeOptions = sizeOptionsForModel(kolors);
            assert.deepEqual(sizeOptions.slice(0, 2), [
              { value: '1024x1024', label: '1:1 - 1024x1024' },
              { value: '1024x2048', label: '1:2 - 1024x2048' },
            ]);
            assert.equal(sizeOptions.some((item) => item.value === 'custom'), false);
            const names = supportedParamNames(kolors);
            for (const name of ['negative_prompt', 'seed', 'steps', 'guidance']) {
              assert.ok(names.includes(name), `${name} should be catalog-supported`);
            }
            assert.equal(hasOperationRefs(kolors), false);
            assert.deepEqual(operationRefs(kolors), []);
            assert.equal(supportsImageReference(kolors), true);
            assert.deepEqual(imageReferenceSpecs(kolors), [{
              roles: ['input_image'],
              provider_field: 'image',
              max_count: 1,
              max_total: 1,
              formats: ['url'],
              required: true,
            }]);
            console.log(JSON.stringify({ ok: true, count: sizeOptions.length }));
            """
        )
        result = run_operation_helper_script(script, {"kolors": self.models["kolors"]})
        self.assertEqual(result["count"], 8)

    def test_modelscope_operation_helpers_render_catalog_sizes_without_extra_params(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { sizeOptionsForModel, supportedParamNames, supportsImageReference } from './operation-capabilities.js';
            import { buildOperationPayload } from './operation-payload.js';

            const { qwen, zImage } = JSON.parse(fs.readFileSync(0, 'utf8'));
            assert.deepEqual(supportedParamNames(qwen).sort(), ['prompt', 'size']);
            assert.equal(supportsImageReference(qwen), false);
            assert.deepEqual(sizeOptionsForModel(qwen).slice(0, 3), [
              { value: '1024x1024', label: '1:1 - 1024x1024' },
              { value: '1328x1328', label: '1:1 HQ - 1328x1328' },
              { value: '1664x928', label: '16:9 - 1664x928' },
            ]);
            assert.equal(sizeOptionsForModel(qwen).some((item) => item.value === 'custom'), false);
            assert.equal(sizeOptionsForModel(zImage).at(-1).value, 'custom');
            assert.deepEqual(buildOperationPayload(qwen, {
              negative_prompt: 'old',
              seed: '3',
              steps: '20',
              guidance: '7.5',
            }), {});
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_operation_helper_script(script, {
            "qwen": self.models["qwen"],
            "zImage": self.models["z-image"],
        })["ok"])

    def test_agnes_operation_helpers_expose_custom_sizes_and_data_url_refs(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import {
              imageReferenceSpecs,
              sizeOptionsForModel,
              supportedParamNames,
              supportsImageReference,
            } from './operation-capabilities.js';
            import { buildOperationPayload } from './operation-payload.js';

            const { agnes20, agnes21 } = JSON.parse(fs.readFileSync(0, 'utf8'));
            assert.deepEqual(supportedParamNames(agnes20).sort(), ['prompt', 'seed', 'size']);
            assert.deepEqual(supportedParamNames(agnes21).sort(), ['prompt', 'size']);
            assert.deepEqual(sizeOptionsForModel(agnes21).slice(0, 4), [
              { value: '1024x768', label: '4:3 - 1024x768' },
              { value: '1024x1024', label: '1:1 - 1024x1024' },
              { value: '768x1024', label: '3:4 - 768x1024' },
              { value: '1280x720', label: '16:9 - 1280x720' },
            ]);
            assert.equal(sizeOptionsForModel(agnes20).at(-1).value, 'custom');
            assert.equal(sizeOptionsForModel(agnes21).at(-1).value, 'custom');
            assert.equal(supportsImageReference(agnes20), true);
            assert.equal(supportsImageReference(agnes21), true);
            assert.equal(imageReferenceSpecs(agnes21)[0].provider_format, 'data_url');
            assert.deepEqual(imageReferenceSpecs(agnes21)[0].formats, ['url', 'data_url']);
            assert.deepEqual(buildOperationPayload(agnes20, {
              image: 'https://example.com/source.png',
              seed: '42',
              steps: '20',
            }), {
              image: 'https://example.com/source.png',
              seed: 42,
            });
            assert.deepEqual(buildOperationPayload(agnes21, {
              image: 'https://example.com/source.png',
              seed: '42',
            }), { image: 'https://example.com/source.png' });
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_operation_helper_script(script, {
            "agnes20": self.models["agnes-2-0"],
            "agnes21": self.models["agnes-2-1"],
        })["ok"])

    def test_seedream_experimental_model_uses_generic_freeform_size_and_seed_controls(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { selectableImageModels } from './studio/lib/capabilities.js';
            import {
              sizeOptionsForModel,
              supportedParamNames,
              supportsImageReference,
              supportsOperationParam,
            } from './studio/features/generate-image/operation-capabilities.js';
            import { buildOperationPayload } from './studio/features/generate-image/operation-payload.js';

            const { seedream } = JSON.parse(fs.readFileSync(0, 'utf8'));
            assert.deepEqual(selectableImageModels({ models: [seedream] }).map((item) => item.id), ['seedream-3']);
            assert.deepEqual(supportedParamNames(seedream).sort(), ['prompt', 'seed', 'size']);
            assert.equal(sizeOptionsForModel(seedream).at(-1).value, 'custom');
            assert.equal(supportsOperationParam(seedream, 'aspect_ratio'), false);
            assert.equal(supportsImageReference(seedream), false);
            assert.deepEqual(buildOperationPayload(seedream, {
              seed: '42',
              aspect_ratio: '16:9',
            }), { seed: 42 });
            assert.deepEqual(buildOperationPayload(seedream, {
              seed: '42',
              image: 'https://example.test/reference.png',
            }), {});
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {"seedream": self.models["seedream-3"]})["ok"])

    def test_agnes_custom_size_validation_uses_live_probe_bounds(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { selectedSize } from './studio/features/generate-image/size-controls.js';

            const { agnes20, agnes21 } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const select = { value: 'custom' };
            const input = (value) => ({ value });
            assert.deepEqual(selectedSize(select, input('2560x1440'), agnes21), {
              ok: true,
              value: '2560x1440',
            });
            assert.deepEqual(selectedSize(select, input('4096x4096'), agnes21), {
              ok: true,
              value: '4096x4096',
            });
            assert.deepEqual(selectedSize(select, input('2048x1536'), agnes20), {
              ok: true,
              value: '2048x1536',
            });
            assert.equal(selectedSize(select, input('256x256'), agnes21).ok, false);
            assert.equal(selectedSize(select, input('2560x1440'), agnes20).ok, false);
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {
            "agnes20": self.models["agnes-2-0"],
            "agnes21": self.models["agnes-2-1"],
        })["ok"])

    def test_modelscope_freeform_size_validation_uses_catalog_bounds(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { selectedSize } from './studio/features/generate-image/size-controls.js';

            const { zImage } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const select = { value: 'custom' };
            const input = (value) => ({ value });
            assert.deepEqual(selectedSize(select, input('720x1280'), zImage), { ok: true, value: '720x1280' });
            assert.equal(selectedSize(select, input('63x1024'), zImage).ok, false);
            assert.equal(selectedSize(select, input('2049x1024'), zImage).ok, false);
            assert.equal(selectedSize(select, input('256x256'), zImage).ok, false);
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {"zImage": self.models["z-image"]})["ok"])

    def test_custom_size_field_visibility_follows_operation_capability(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { syncSizeFields } from './studio/features/generate-image/size-controls.js';

            const { agnes21, qwen, zImage } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const legacy = { size: { mode: 'preset' }, size_presets: ['1024x1024'], operations: {} };

            function visibility(model, value) {
              const sizeSelect = { value };
              const customSizeInput = { hidden: false };
              const customSizeField = { hidden: false };
              syncSizeFields(sizeSelect, customSizeInput, customSizeField, model);
              return { fieldHidden: customSizeField.hidden, inputHidden: customSizeInput.hidden };
            }

            assert.deepEqual(visibility(qwen, '1024x1024'), { fieldHidden: true, inputHidden: true });
            assert.deepEqual(visibility(agnes21, '1024x768'), { fieldHidden: false, inputHidden: true });
            assert.deepEqual(visibility(agnes21, 'custom'), { fieldHidden: false, inputHidden: false });
            assert.deepEqual(visibility(zImage, 'custom'), { fieldHidden: false, inputHidden: false });
            assert.deepEqual(visibility(legacy, 'custom'), { fieldHidden: false, inputHidden: false });
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {
            "agnes21": self.models["agnes-2-1"],
            "qwen": self.models["qwen"],
            "zImage": self.models["z-image"],
        })["ok"])

    def test_hidden_custom_size_field_overrides_field_grid_display(self) -> None:
        components_css = (ROOT / "app" / "www" / "assets" / "studio" / "styles" / "components.css").read_text(
            encoding="utf-8"
        )
        self.assertRegex(components_css, r"\.field\[hidden\]\s*\{\s*display:\s*none;")

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
              image: 'https://example.com/source.png',
              unsupported: 'drop-me',
              size: '1024x1024',
            };
            assert.deepEqual(buildOperationPayload(kolors, staleValues), {
              negative_prompt: 'blur',
              seed: 123,
              steps: 30,
              guidance: 8.5,
              image: 'https://example.com/source.png',
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

    def test_generation_payload_only_includes_operation_values_for_current_supported_model(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';
            import { buildGenerationPayload } from './studio/features/generate-image/payload.js';

            const { kolors, mock, qwen, seedream } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const input = (value) => ({ value, focus() {} });
            const staleOperationValues = {
              negative_prompt: 'blur',
              seed: '123',
              steps: '30',
              guidance: '8.5',
              image: 'https://example.com/source.png',
            };
            function build({
              catalogProviderId,
              model,
              customProvider = null,
              providerValue = 'catalog:siliconflow',
              modelInputValue = '',
              operationValues = staleOperationValues,
              sizeValue = '1024x1024',
              customSizeValue = '1024x1024',
            }) {
              return buildGenerationPayload({
                promptInput: input('a cat'),
                sizeSelect: { value: sizeValue },
                customSizeInput: input(customSizeValue),
                providerSelect: { value: providerValue },
                modelInput: input(modelInputValue),
                operationValues,
                currentCatalogProviderId: () => catalogProviderId,
                currentCatalogModel: () => model,
                currentCustomProvider: () => customProvider,
              }).payload;
            }

            const kolorsPayload = build({ catalogProviderId: 'siliconflow', model: kolors });
            assert.equal(kolorsPayload.negative_prompt, 'blur');
            assert.equal(kolorsPayload.seed, 123);
            assert.equal(kolorsPayload.steps, 30);
            assert.equal(kolorsPayload.guidance, 8.5);
            assert.equal(kolorsPayload.image, 'https://example.com/source.png');
            assert.equal(Object.hasOwn(kolorsPayload, 'provider_model'), false);

            const staleModelInputPayload = build({
              catalogProviderId: 'siliconflow',
              model: kolors,
              modelInputValue: 'Tongyi-MAI/Z-Image-Turbo',
            });
            assert.equal(Object.hasOwn(staleModelInputPayload, 'provider_model'), false);
            assert.equal(staleModelInputPayload.model, 'kolors');

            const qwenPayload = build({ catalogProviderId: 'modelscope', model: qwen, providerValue: 'catalog:modelscope' });
            for (const name of ['negative_prompt', 'seed', 'steps', 'guidance', 'image']) {
              assert.equal(Object.hasOwn(qwenPayload, name), false, `${name} should not leak to ModelScope`);
            }

            const aspectRatioPayload = build({
              catalogProviderId: 'mock',
              model: mock,
              providerValue: 'catalog:mock',
              operationValues: { aspect_ratio: '16:9' },
              sizeValue: 'custom',
              customSizeValue: 'not-a-size',
            });
            assert.equal(aspectRatioPayload.aspect_ratio, '16:9');
            assert.equal(Object.hasOwn(aspectRatioPayload, 'size'), false);
            assert.equal(Object.hasOwn(aspectRatioPayload, 'output_aspect_ratio'), false);

            const explicitSizePayload = build({
              catalogProviderId: 'mock',
              model: mock,
              providerValue: 'catalog:mock',
              operationValues: { aspect_ratio: '' },
            });
            assert.equal(explicitSizePayload.size, '1024x1024');
            assert.equal(Object.hasOwn(explicitSizePayload, 'aspect_ratio'), false);

            const customPayload = build({
              catalogProviderId: '',
              model: null,
              customProvider: { id: 'local', default_model: 'custom-default' },
              providerValue: 'custom:local',
              modelInputValue: 'override-model',
            });
            assert.equal(customPayload.provider_model, 'override-model');
            for (const name of ['negative_prompt', 'seed', 'steps', 'guidance', 'image']) {
              assert.equal(Object.hasOwn(customPayload, name), false, `${name} should not leak to custom provider`);
            }

            const defaultPayload = build({ catalogProviderId: '', model: null, providerValue: '' });
            for (const name of ['negative_prompt', 'seed', 'steps', 'guidance', 'image']) {
              assert.equal(Object.hasOwn(defaultPayload, name), false, `${name} should not leak to default route`);
            }
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {
            "kolors": self.models["kolors"],
            "mock": self.models["mock"],
            "qwen": self.models["qwen"],
        })["ok"])

    def test_provider_mode_help_keys_are_mode_aware(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import {
              providerHelpKeyForMode,
              providerModeFromSelection,
            } from './studio/features/generate-image/provider-model-controls.js';

            assert.equal(providerModeFromSelection('siliconflow', null), 'catalog');
            assert.equal(providerModeFromSelection('', { id: 'custom' }), 'custom');
            assert.equal(providerModeFromSelection('', null), 'default');
            assert.equal(providerHelpKeyForMode('catalog', true), 'generateImage.providerHelpCatalog');
            assert.equal(providerHelpKeyForMode('custom'), 'generateImage.providerHelpCustom');
            assert.equal(providerHelpKeyForMode('default'), 'generateImage.providerHelpDefault');
            assert.equal(providerHelpKeyForMode('default', true), 'generateImage.providerLoadFailed');
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {})["ok"])

    def test_operation_controls_render_seed_as_number_and_negative_prompt_as_textarea(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';

            class FakeElement {
              constructor(tagName) {
                this.tagName = tagName.toUpperCase();
                this.children = [];
                this.dataset = {};
                this.className = '';
                this.hidden = false;
                this.value = '';
                this.listeners = {};
              }
              appendChild(child) {
                this.children.push(child);
                return child;
              }
              addEventListener(name, fn) {
                this.listeners[name] = fn;
              }
              setAttribute(key, value) {
                this[key] = String(value);
              }
              set textContent(value) {
                this._textContent = String(value);
                if (value === '') this.children = [];
              }
              get textContent() {
                return this._textContent || '';
              }
            }
            class FakeText {
              constructor(text) {
                this.tagName = '#TEXT';
                this.textContent = text;
                this.children = [];
              }
            }
            globalThis.document = {
              createElement: (tagName) => new FakeElement(tagName),
              createTextNode: (text) => new FakeText(text),
            };

            const { createOperationControls } = await import('./studio/features/generate-image/operation-controls.js');
            const { kolors, mock, qwen, seedream } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const target = new FakeElement('div');
            const controls = createOperationControls({ target });
            controls.sync(kolors);

            function walk(node, predicate) {
              if (predicate(node)) return node;
              for (const child of node.children || []) {
                const found = walk(child, predicate);
                if (found) return found;
              }
              return null;
            }

            const seed = walk(target, (node) => node.dataset?.operationParam === 'seed');
            const steps = walk(target, (node) => node.dataset?.operationParam === 'steps');
            const guidance = walk(target, (node) => node.dataset?.operationParam === 'guidance');
            const negative = walk(target, (node) => node.dataset?.operationParam === 'negative_prompt');
            const image = walk(target, (node) => node.dataset?.operationRef === 'image');
            const randomButton = walk(target, (node) => String(node.className || '').includes('operation-seed-random'));

            assert.equal(seed.tagName, 'INPUT');
            assert.equal(seed.type, 'number');
            assert.equal(steps.tagName, 'INPUT');
            assert.equal(steps.type, 'number');
            assert.equal(guidance.tagName, 'INPUT');
            assert.equal(guidance.type, 'number');
            assert.equal(negative.tagName, 'TEXTAREA');
            assert.equal(image.tagName, 'INPUT');
            assert.equal(image.type, 'url');
            assert.ok(randomButton, 'seed random button should render');
            randomButton.listeners.click();
            assert.ok(Number(seed.value) >= 0);
            assert.ok(Number(seed.value) <= 9999999999);

            controls.sync(mock);
            const aspectRatio = walk(target, (node) => node.dataset?.operationParam === 'aspect_ratio');
            assert.equal(aspectRatio.tagName, 'SELECT');
            assert.deepEqual(aspectRatio.children.map((option) => option.value), ['', '1:1', '16:9', '9:16']);
            assert.deepEqual(controls.values(), {});
            aspectRatio.value = '16:9';
            assert.deepEqual(controls.values(), { aspect_ratio: '16:9' });

            controls.sync(seedream);
            const seedreamSeed = walk(target, (node) => node.dataset?.operationParam === 'seed');
            assert.equal(target.hidden, false);
            assert.equal(seedreamSeed.tagName, 'INPUT');
            assert.equal(seedreamSeed.type, 'number');
            assert.equal(walk(target, (node) => node.dataset?.operationParam === 'aspect_ratio'), null);
            assert.equal(walk(target, (node) => node.dataset?.operationRef === 'image'), null);

            controls.sync(qwen);
            assert.equal(target.hidden, true);
            assert.equal(walk(target, (node) => node.dataset?.operationParam === 'aspect_ratio'), null);
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {
            "kolors": self.models["kolors"],
            "mock": self.models["mock"],
            "qwen": self.models["qwen"],
            "seedream": self.models["seedream-3"],
        })["ok"])

    def test_reference_asset_picker_prefers_asset_path_and_clears_for_unsupported_models(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import fs from 'node:fs';

            class FakeElement {
              constructor(tagName) {
                this.tagName = tagName.toUpperCase();
                this.children = [];
                this.dataset = {};
                this.className = '';
                this.hidden = false;
                this.value = '';
                this.listeners = {};
              }
              appendChild(child) {
                this.children.push(child);
                return child;
              }
              addEventListener(name, fn) {
                this.listeners[name] = fn;
              }
              setAttribute(key, value) {
                this[key] = String(value);
              }
              set textContent(value) {
                this._textContent = String(value);
                if (value === '') this.children = [];
              }
              get textContent() {
                return this._textContent || '';
              }
            }
            class FakeText {
              constructor(text) {
                this.tagName = '#TEXT';
                this.textContent = text;
                this.children = [];
              }
            }
            globalThis.document = {
              createElement: (tagName) => new FakeElement(tagName),
              createTextNode: (text) => new FakeText(text),
            };

            const { createOperationControls } = await import('./studio/features/generate-image/operation-controls.js');
            const { agnes21, kolors, qwen } = JSON.parse(fs.readFileSync(0, 'utf8'));
            const target = new FakeElement('div');
            const controls = createOperationControls({
              target,
              referenceAssets: [
                { value: '/generated/ref-a.png', label: 'Generated reference' },
                { value: '/uploads/ref-b.png', label: 'Uploaded reference' },
              ],
            });
            controls.sync(kolors);

            function walk(node, predicate) {
              if (predicate(node)) return node;
              for (const child of node.children || []) {
                const found = walk(child, predicate);
                if (found) return found;
              }
              return null;
            }

            const assetSelect = walk(target, (node) => node.dataset?.operationRefAsset === 'image');
            const urlInput = walk(target, (node) => node.dataset?.operationRef === 'image');
            const operationGrid = walk(target, (node) => node.dataset?.operationControls === 'true');
            const uploadField = operationGrid.children.find((node) => (
              walk(node, (child) => child.className === 'ref-upload-control')
            ));
            assert.equal(assetSelect.tagName, 'SELECT');
            assert.equal(assetSelect.children.length, 3);
            assert.equal(urlInput.tagName, 'INPUT');
            assert.equal(urlInput.type, 'url');
            assert.ok(uploadField.className.includes('span-2'));

            assetSelect.value = '/generated/ref-a.png';
            urlInput.value = 'https://example.com/fallback.png';
            assert.deepEqual(controls.values().image, '/generated/ref-a.png');

            assetSelect.value = '';
            assert.deepEqual(controls.values().image, 'https://example.com/fallback.png');

            controls.sync(agnes21);
            const agnesAssetSelect = walk(target, (node) => node.dataset?.operationRefAsset === 'image');
            const agnesUrlInput = walk(target, (node) => node.dataset?.operationRef === 'image');
            const agnesUpload = walk(target, (node) => node.className === 'ref-upload-control');
            assert.equal(agnesAssetSelect.tagName, 'SELECT');
            assert.notEqual(agnesUpload, null);
            assert.equal(agnesUrlInput.type, 'url');
            agnesAssetSelect.value = '/uploads/ref-b.png';
            assert.equal(controls.values().image, '/uploads/ref-b.png');
            agnesAssetSelect.value = '';
            agnesUrlInput.value = 'https://example.com/agnes.png';
            assert.equal(controls.values().image, 'https://example.com/agnes.png');

            controls.sync(qwen);
            assert.equal(target.hidden, true);
            assert.deepEqual(controls.values(), {});
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {
            "agnes21": self.models["agnes-2-1"],
            "kolors": self.models["kolors"],
            "qwen": self.models["qwen"],
        })["ok"])

    def test_reference_upload_rejects_invalid_reselection_without_stale_file(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';

            class FakeElement {
              constructor(tagName) {
                this.tagName = tagName.toUpperCase();
                this.children = [];
                this.dataset = {};
                this.className = '';
                this.hidden = false;
                this.value = '';
                this.files = [];
                this.listeners = {};
              }
              appendChild(child) {
                this.children.push(child);
                return child;
              }
              addEventListener(name, fn) {
                this.listeners[name] = fn;
              }
              setAttribute(key, value) {
                this[key] = String(value);
              }
              set textContent(value) {
                this._textContent = String(value);
                if (value === '') this.children = [];
              }
              get textContent() {
                return this._textContent || '';
              }
            }
            class FakeText {
              constructor(text) {
                this.tagName = '#TEXT';
                this.textContent = text;
                this.children = [];
              }
            }
            globalThis.document = {
              createElement: (tagName) => new FakeElement(tagName),
              createTextNode: (text) => new FakeText(text),
            };
            globalThis.URL = {
              createObjectURL: () => 'blob:test',
              revokeObjectURL: () => {},
            };

            const { createReferenceUpload } = await import('./studio/features/generate-image/reference-upload.js');
            const target = new FakeElement('div');
            const upload = createReferenceUpload({ target });

            function walk(node, predicate) {
              if (predicate(node)) return node;
              for (const child of node.children || []) {
                const found = walk(child, predicate);
                if (found) return found;
              }
              return null;
            }

            const input = walk(target, (node) => node.type === 'file');
            const preview = walk(target, (node) => node.className === 'ref-upload-preview');
            const status = walk(target, (node) => String(node.className).includes('ref-upload-status'));
            const valid = { name: 'valid.png', type: 'image/png', size: 100 };

            input.files = [valid];
            input.listeners.change();
            assert.equal(upload.hasPendingFile(), true);
            assert.equal(preview.hidden, false);

            input.files = [{ name: 'huge.png', type: 'image/png', size: 21 * 1024 * 1024 }];
            input.listeners.change();
            assert.equal(upload.hasPendingFile(), false);
            assert.equal(preview.hidden, true);
            assert.ok(status.textContent);

            input.files = [valid];
            input.listeners.change();
            input.files = [{ name: 'notes.txt', type: 'text/plain', size: 100 }];
            input.listeners.change();
            assert.equal(upload.hasPendingFile(), false);
            assert.equal(preview.hidden, true);
            assert.ok(status.textContent);
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {})["ok"])

    def test_reference_asset_module_filters_to_safe_image_assets(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';
            import { imageReferenceAssets } from './studio/features/generate-image/reference-assets.js';

            globalThis.window = { location: { origin: 'https://gateway.example' } };
            const refs = imageReferenceAssets([
              { id: 'a', media_type: 'image', url_path: '/generated/a.png', prompt: 'first cat' },
              { id: 'b', media_type: 'video', url_path: '/generated/b.mp4' },
              { id: 'c', media_type: 'image', url_path: 'https://gateway.example/uploads/c.png', display_name: 'Uploaded C' },
              { id: 'd', media_type: 'image', url_path: 'https://cdn.example.com/d.png' },
            ]);

            assert.deepEqual(refs.map((item) => item.value), ['/generated/a.png', '/uploads/c.png']);
            assert.equal(refs[0].label, 'first cat');
            assert.equal(refs[1].label, 'Uploaded C');
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {})["ok"])

    def test_result_preview_renders_safe_generated_url(self) -> None:
        script = textwrap.dedent(
            """
            import assert from 'node:assert/strict';

            class FakeElement {
              constructor(tagName) {
                this.tagName = tagName.toUpperCase();
                this.children = [];
                this.dataset = {};
                this.className = '';
                this.value = '';
              }
              appendChild(child) {
                this.children.push(child);
                return child;
              }
              addEventListener() {}
              setAttribute(key, value) {
                this[key] = String(value);
              }
              set textContent(value) {
                this._textContent = String(value);
                if (value === '') this.children = [];
              }
              get textContent() {
                return this._textContent || '';
              }
            }
            class FakeText {
              constructor(text) {
                this.tagName = '#TEXT';
                this.textContent = text;
                this.children = [];
              }
            }
            globalThis.window = { location: { origin: 'http://testserver' } };
            globalThis.document = {
              createElement: (tagName) => new FakeElement(tagName),
              createTextNode: (text) => new FakeText(text),
            };

            const { renderResultSuccess } = await import('./studio/features/generate-image/result-preview.js');
            const target = new FakeElement('div');
            renderResultSuccess(target, {
              provider: 'siliconflow',
              model: 'Kwai-Kolors/Kolors',
              job_id: 'job-1',
              history_id: 'hist-1',
              data: [{ url: 'http://testserver/generated/preview.png' }],
            }, 'a cat');

            function walk(node, predicate) {
              if (predicate(node)) return node;
              for (const child of node.children || []) {
                const found = walk(child, predicate);
                if (found) return found;
              }
              return null;
            }

            const image = walk(target, (node) => node.tagName === 'IMG');
            const download = walk(target, (node) => node.tagName === 'A' && node.href === '/generated/preview.png');
            assert.equal(image.src, '/generated/preview.png');
            assert.ok(download, 'safe generated result should have a download link');
            console.log(JSON.stringify({ ok: true }));
            """
        )
        self.assertTrue(run_studio_module_script(script, {})["ok"])

    def test_operation_control_source_stays_catalog_driven_and_imports_form_helpers(self) -> None:
        source = (FEATURE_DIR / "operation-controls.js").read_text(encoding="utf-8")
        self.assertIn("operationParams(model)", source)
        self.assertIn("operationRefs(model)", source)
        self.assertIn("imageReferenceSpecs(model)", source)
        self.assertIn("field, input, select, textarea", source)
        self.assertNotIn("model.id", source)
        self.assertNotIn("kolors", source.lower())

    def test_generate_image_page_does_not_hardcode_kolors_capabilities(self) -> None:
        source = (FEATURE_DIR / "page.js").read_text(encoding="utf-8").lower()
        self.assertNotIn("kolors", source)
        self.assertNotIn("kwai-kolors", source)

    def test_reference_upload_stays_modular_and_returns_prepared_path(self) -> None:
        upload_source = (FEATURE_DIR / "reference-upload.js").read_text(encoding="utf-8")
        controls_source = (FEATURE_DIR / "operation-controls.js").read_text(encoding="utf-8")
        page_source = (FEATURE_DIR / "page.js").read_text(encoding="utf-8")
        api_source = (ROOT / "app" / "www" / "assets" / "studio" / "api.js").read_text(encoding="utf-8")
        styles_source = (ROOT / "app" / "www" / "assets" / "studio" / "styles" / "components.css").read_text(encoding="utf-8")

        self.assertIn("api.upload('/uploads', form)", upload_source)
        self.assertIn("URL.createObjectURL", upload_source)
        self.assertIn("formatBytes", upload_source)
        self.assertIn("return referenceUpload.prepare()", controls_source)
        self.assertIn("const uploadedPath = await operationControls.prepare()", page_source)
        self.assertIn("built.payload.image = uploadedPath", page_source)
        self.assertNotIn("FormData", page_source)
        self.assertNotIn("URL.createObjectURL", page_source)
        self.assertIn("body instanceof FormData", api_source)
        self.assertIn("isFormData ? body : JSON.stringify(body)", api_source)
        self.assertRegex(styles_source, r"\.ref-upload-preview\[hidden\]\s*\{\s*display:\s*none;")


if __name__ == "__main__":
    unittest.main()

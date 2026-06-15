import { t } from '../../i18n.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import {
  getTextToImageOperation,
  hasOperationRefs,
  imageReferenceSpecs,
  operationParams,
  operationRefs,
} from './operation-capabilities.js';

const HIDDEN_PARAMS = new Set(['prompt', 'size']);

function paramLabel(name) {
  const key = `generateImage.param.${name}`;
  const translated = t(key);
  if (translated !== key) return translated;
  return name.replaceAll('_', ' ');
}

function defaultHelp(spec) {
  if (spec?.default === null || spec?.default === undefined || spec?.default === '') return '';
  return t('generateImage.paramDefault').replace('{value}', String(spec.default));
}

function numberAttrs(name, spec) {
  const attrs = {
    name: `operation_${name}`,
    type: 'number',
    class: 'operation-number-control',
    autocomplete: 'off',
    dataset: { operationParam: name },
  };
  if (spec.min !== null && spec.min !== undefined) attrs.min = String(spec.min);
  if (spec.max !== null && spec.max !== undefined) attrs.max = String(spec.max);
  if (spec.default !== null && spec.default !== undefined) attrs.placeholder = String(spec.default);
  if (spec.kind === 'float') attrs.step = '0.1';
  return attrs;
}

function randomSeedValue(spec) {
  const min = Number.isFinite(Number(spec.min)) ? Number(spec.min) : 0;
  const max = Number.isFinite(Number(spec.max)) ? Number(spec.max) : 9999999999;
  return String(Math.floor(Math.random() * (max - min + 1)) + min);
}

function renderNumberControl(name, spec) {
  const control = input(numberAttrs(name, spec));
  if (name !== 'seed') return { node: control, control };
  return {
    node: el('div', { class: 'operation-inline-control' },
      control,
      el('button', {
        type: 'button',
        class: 'btn btn-secondary btn-sm operation-seed-random',
        title: t('generateImage.seedRandom'),
        ariaLabel: t('generateImage.seedRandom'),
        onclick: () => {
          control.value = randomSeedValue(spec);
        },
      }, '↻'),
    ),
    control,
  };
}

function renderParamControl(name, spec) {
  if (HIDDEN_PARAMS.has(name)) return null;
  if (spec.kind === 'string') {
    const control = textarea({
      name: `operation_${name}`,
      rows: 3,
      autocomplete: 'off',
      dataset: { operationParam: name },
    });
    return { node: control, control };
  }
  if (spec.kind === 'int' || spec.kind === 'seed' || spec.kind === 'float') {
    return renderNumberControl(name, spec);
  }
  return null;
}

function renderRefSummary(model) {
  if (!hasOperationRefs(model)) return null;
  const refs = operationRefs(model)
    .flatMap((item) => Array.isArray(item?.roles) ? item.roles : [])
    .filter(Boolean);
  return el('div', { class: 'hint-box', dataset: { operationRefs: 'true' } },
    el('span', {}, 'i'),
    el('p', { class: 'field-help' },
      `${t('generateImage.referenceInputs')}: ${refs.join(', ') || t('common.none')}. ${t('generateImage.referenceInputsReserved')}`,
    ),
  );
}

function renderImageReferenceControl() {
  return input({
    name: 'operation_image',
    type: 'url',
    autocomplete: 'off',
    placeholder: t('generateImage.imageReferencePlaceholder'),
    dataset: { operationRef: 'image' },
  });
}

function renderImageReferenceAssetControl(referenceAssets) {
  const options = [
    {
      value: '',
      label: referenceAssets.length ? t('generateImage.referenceAssetNone') : t('generateImage.referenceAssetEmpty'),
    },
    ...referenceAssets.map((asset) => ({
      value: asset.value,
      label: asset.label,
    })),
  ];
  return select(options, {
    name: 'operation_image_asset',
    dataset: { operationRefAsset: 'image' },
  });
}

export function createOperationControls({ target, referenceAssets = [] }) {
  let currentModel = null;
  const controls = new Map();
  const refControls = new Map();

  function clearControls() {
    currentModel = null;
    controls.clear();
    refControls.clear();
    target.hidden = true;
    mount(target);
  }

  function sync(model) {
    clearControls();
    const operation = getTextToImageOperation(model);
    if (!operation) return;

    currentModel = model;
    const fields = [];
    Object.entries(operationParams(model)).forEach(([name, spec]) => {
      const rendered = renderParamControl(name, spec || {});
      if (!rendered) return;
      controls.set(name, rendered.control);
      fields.push(field(paramLabel(name), rendered.node, { help: defaultHelp(spec || {}) }));
    });

    imageReferenceSpecs(model).forEach((ref) => {
      const assetControl = renderImageReferenceAssetControl(referenceAssets);
      const urlControl = renderImageReferenceControl(ref);
      refControls.set('imageAsset', assetControl);
      refControls.set('image', urlControl);
      fields.push(field(t('generateImage.referenceAsset'), assetControl, { help: t('generateImage.referenceAssetHelp') }));
      fields.push(field(t('generateImage.imageReference'), urlControl, { help: t('generateImage.imageReferenceHelp') }));
    });

    const refSummary = renderRefSummary(model);
    if (!fields.length && !refSummary) return;
    target.hidden = false;
    mount(target,
      el('div', { class: 'form-grid', dataset: { operationControls: 'true' } }, fields),
      refSummary,
    );
  }

  function values() {
    const result = {};
    controls.forEach((control, name) => {
      const value = String(control.value || '').trim();
      if (value) result[name] = value;
    });
    const assetReference = String(refControls.get('imageAsset')?.value || '').trim();
    const urlReference = String(refControls.get('image')?.value || '').trim();
    if (assetReference) result.image = assetReference;
    else if (urlReference) result.image = urlReference;
    return result;
  }

  function model() {
    return currentModel;
  }

  return {
    clear: clearControls,
    model,
    sync,
    values,
  };
}

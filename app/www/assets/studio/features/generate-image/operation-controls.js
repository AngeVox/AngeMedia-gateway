import { t } from '../../i18n.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import {
  getImageEditOperation,
  getImageToImageOperation,
  getTextToImageOperation,
  imageReferenceSpecs,
  maskReferenceSpecs,
  operationParams,
  operationRefs,
  requiresPublicReferenceUrl,
} from './operation-capabilities.js';
import { createReferenceUpload } from './reference-upload.js';

const HIDDEN_PARAMS = new Set(['prompt', 'size']);

function paramLabel(name) {
  const key = 'generateImage.param.' + name;
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
    name: 'operation_' + name,
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

function renderEnumControl(name, spec) {
  const values = Array.isArray(spec?.enum_values) ? spec.enum_values : [];
  const control = select([
    { value: '', label: t('generateImage.paramAuto') },
    ...values.map((value) => ({ value: String(value), label: String(value) })),
  ], {
    name: 'operation_' + name,
    dataset: { operationParam: name },
  });
  return { node: control, control };
}

function renderBoolControl(name) {
  const control = select([
    { value: '', label: t('generateImage.paramAuto') },
    { value: 'false', label: t('common.no') },
    { value: 'true', label: t('common.yes') },
  ], {
    name: 'operation_' + name,
    dataset: { operationParam: name },
  });
  return { node: control, control };
}

function renderAspectRatioControl(name, spec) {
  const presets = Array.isArray(spec?.presets) ? spec.presets : [];
  const control = select([
    { value: '', label: t('generateImage.aspectRatioUseSize') },
    ...presets
      .filter((preset) => typeof preset?.value === 'string' && preset.value.trim())
      .map((preset) => ({ value: preset.value, label: preset.label || preset.value })),
  ], {
    name: 'operation_' + name,
    value: '',
    dataset: { operationParam: name },
  });
  return { node: control, control };
}

function renderParamControl(name, spec) {
  if (HIDDEN_PARAMS.has(name)) return null;
  if (spec.kind === 'string') {
    const control = textarea({
      name: 'operation_' + name,
      rows: 3,
      autocomplete: 'off',
      dataset: { operationParam: name },
    });
    return { node: control, control };
  }
  if (spec.kind === 'int' || spec.kind === 'seed' || spec.kind === 'float') {
    return renderNumberControl(name, spec);
  }
  if (spec.kind === 'enum') return renderEnumControl(name, spec);
  if (spec.kind === 'bool') return renderBoolControl(name, spec);
  if (spec.kind === 'aspect_ratio') return renderAspectRatioControl(name, spec);
  return null;
}

function renderRefSummary(model, operationName) {
  const specs = operationRefs(model, operationName);
  if (!specs.length) return null;
  const refs = specs
    .flatMap((item) => Array.isArray(item?.roles) ? item.roles : [])
    .filter(Boolean);
  const localSourceHelp = specs.some((ref) => !requiresPublicReferenceUrl(ref))
    ? ' ' + t('generateImage.referenceInputsReserved')
    : '';
  return el('div', { class: 'hint-box', dataset: { operationRefs: 'true' } },
    el('span', {}, 'i'),
    el('p', { class: 'field-help' },
      t('generateImage.referenceInputs') + ': ' + (refs.join(', ') || t('common.none')) + '.' + localSourceHelp,
    ),
  );
}

function assetOptions(referenceAssets) {
  return referenceAssets.map((asset) => ({ value: asset.value, label: asset.label }));
}

function renderAssetControl(referenceAssets, { multiple = false, name = 'operation_image_asset', dataset = {} } = {}) {
  const options = multiple
    ? assetOptions(referenceAssets)
    : [
      {
        value: '',
        label: referenceAssets.length ? t('generateImage.referenceAssetNone') : t('generateImage.referenceAssetEmpty'),
      },
      ...assetOptions(referenceAssets),
    ];
  return select(options, {
    name,
    multiple,
    size: multiple ? Math.min(6, Math.max(2, referenceAssets.length)) : undefined,
    dataset,
  });
}

function selectedValues(control) {
  if (!control) return [];
  if (control.multiple) {
    return Array.from(control.selectedOptions || []).map((option) => String(option.value || '').trim()).filter(Boolean);
  }
  const value = String(control.value || '').trim();
  return value ? [value] : [];
}

function refLimit(spec) {
  const value = Number(spec?.max_count ?? spec?.max_total ?? 1);
  return Number.isInteger(value) && value > 0 ? value : 1;
}

export function createOperationControls({ target, referenceAssets = [], onOperationChange = null }) {
  let currentModel = null;
  let currentOperationName = 'text_to_image';
  const controls = new Map();
  const refControls = new Map();

  const singleUploadTarget = el('div');
  const multiUploadTarget = el('div');
  const maskUploadTarget = el('div');
  const singleReferenceUpload = createReferenceUpload({ target: singleUploadTarget });
  const multiReferenceUpload = createReferenceUpload({ target: multiUploadTarget, multiple: true, maxFiles: 10 });
  const maskUpload = createReferenceUpload({ target: maskUploadTarget, role: 'mask' });

  function clearUploadState() {
    singleReferenceUpload.clear();
    multiReferenceUpload.clear();
    maskUpload.clear();
  }

  function clearControls() {
    currentModel = null;
    currentOperationName = 'text_to_image';
    controls.clear();
    refControls.clear();
    clearUploadState();
    target.hidden = true;
    mount(target);
  }

  function render(model, operationName) {
    controls.clear();
    refControls.clear();
    clearUploadState();
    currentModel = model;
    currentOperationName = operationName;
    if (typeof onOperationChange === 'function') onOperationChange(operationName);

    const fields = [];
    const hasEdit = Boolean(getImageEditOperation(model));
    if (hasEdit && getTextToImageOperation(model)) {
      const operationControl = select([
        { value: 'text_to_image', label: t('generateImage.operationGenerate') },
        { value: 'image_edit', label: t('generateImage.operationEdit') },
      ], { value: operationName, name: 'operation_mode' });
      operationControl.addEventListener('change', () => render(model, operationControl.value));
      fields.push(field(t('generateImage.operation'), operationControl));
    }

    Object.entries(operationParams(model, operationName)).forEach(([name, spec]) => {
      const rendered = renderParamControl(name, spec || {});
      if (!rendered) return;
      controls.set(name, rendered.control);
      const help = name === 'aspect_ratio' && spec?.allow_with_size !== true
        ? t('generateImage.aspectRatioOverridesSize')
        : defaultHelp(spec || {});
      fields.push(field(paramLabel(name), rendered.node, { help }));
    });

    let referenceOperation = operationName;
    if (operationName === 'text_to_image' && !hasEdit && getImageToImageOperation(model)) {
      referenceOperation = 'image_to_image';
    }
    const refs = imageReferenceSpecs(model, referenceOperation);
    refs.forEach((ref, index) => {
      const limit = refLimit(ref);
      const isMulti = referenceOperation === 'image_edit' && limit > 1;
      const publicUrlOnly = requiresPublicReferenceUrl(ref);

      if (!publicUrlOnly) {
        const assetControl = renderAssetControl(referenceAssets, {
          multiple: isMulti,
          name: isMulti ? 'operation_reference_assets_' + index : 'operation_image_asset_' + index,
          dataset: isMulti ? { operationRefAssets: 'reference_images' } : { operationRefAsset: 'image' },
        });
        refControls.set(isMulti ? 'referenceAssets' : 'imageAsset', assetControl);
        const uploadTarget = isMulti ? multiUploadTarget : singleUploadTarget;
        fields.push(field(
          isMulti ? t('generateImage.uploadReferences') : t('generateImage.uploadReference'),
          uploadTarget,
          { help: isMulti ? t('generateImage.uploadReferencesHelp') : t('generateImage.uploadReferenceHelp'), className: 'span-2' },
        ));
        fields.push(field(
          isMulti ? t('generateImage.referenceAssets') : t('generateImage.referenceAsset'),
          assetControl,
          { help: isMulti ? t('generateImage.referenceAssetsHelp') : t('generateImage.referenceAssetHelp') },
        ));
      }

      if (Array.isArray(ref?.formats) && ref.formats.includes('url')) {
        if (isMulti) {
          const urlControl = textarea({
            name: 'operation_reference_urls_' + index,
            rows: 4,
            autocomplete: 'off',
            placeholder: t('generateImage.referenceUrlsPlaceholder'),
            dataset: { operationRefUrls: 'reference_images' },
          });
          refControls.set('referenceUrls', urlControl);
          fields.push(field(t('generateImage.referenceUrls'), urlControl, {
            help: t('generateImage.referenceUrlsHelp').replace('{count}', String(limit)),
            className: 'span-2',
          }));
        } else {
          const urlControl = input({
            name: 'operation_image_' + index,
            type: 'url',
            autocomplete: 'off',
            placeholder: t('generateImage.imageReferencePlaceholder'),
            dataset: { operationRef: 'image' },
          });
          refControls.set('imageUrl', urlControl);
          fields.push(field(t('generateImage.imageReference'), urlControl, {
            help: publicUrlOnly ? t('generateImage.publicUrlRequired') : t('generateImage.imageReferenceHelp'),
          }));
        }
      }
    });

    const masks = maskReferenceSpecs(model, referenceOperation);
    if (masks.length) {
      const maskAsset = renderAssetControl(referenceAssets, { name: 'operation_mask_asset' });
      refControls.set('maskAsset', maskAsset);
      fields.push(field(t('generateImage.uploadMask'), maskUploadTarget, {
        help: t('generateImage.uploadMaskHelp'),
        className: 'span-2',
      }));
      fields.push(field(t('generateImage.maskAsset'), maskAsset, { help: t('generateImage.maskAssetHelp') }));
    }

    const refSummary = renderRefSummary(model, referenceOperation);
    if (!fields.length && !refSummary) {
      target.hidden = true;
      mount(target);
      return;
    }
    target.hidden = false;
    mount(target,
      el('div', { class: 'form-grid', dataset: { operationControls: 'true' } }, fields),
      refSummary,
    );
  }

  function sync(model) {
    clearControls();
    if (getTextToImageOperation(model)) {
      render(model, 'text_to_image');
      return;
    }
    if (getImageEditOperation(model)) {
      render(model, 'image_edit');
    }
  }

  function values() {
    const result = {};
    controls.forEach((control, name) => {
      const value = String(control.value || '').trim();
      if (value) result[name] = value;
    });
    if (currentOperationName === 'image_edit') result.operation = 'edit';

    if (currentOperationName === 'image_edit') {
      const references = selectedValues(refControls.get('referenceAssets'));
      const urlReferences = String(refControls.get('referenceUrls')?.value || '')
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean);
      const combined = [...references, ...urlReferences];
      if (combined.length) result.reference_images = combined;
    } else {
      const asset = selectedValues(refControls.get('imageAsset'))[0];
      const url = String(refControls.get('imageUrl')?.value || '').trim();
      if (asset) result.image = asset;
      else if (url) result.image = url;
    }
    const mask = selectedValues(refControls.get('maskAsset'))[0];
    if (mask) result.mask = mask;
    return result;
  }

  async function prepare() {
    const prepared = {};
    if (currentOperationName === 'image_edit' && multiReferenceUpload.hasPendingFile()) {
      prepared.reference_images = await multiReferenceUpload.prepare();
    } else if (singleReferenceUpload.hasPendingFile()) {
      prepared.image = await singleReferenceUpload.prepare();
    }
    if (maskUpload.hasPendingFile()) prepared.mask = await maskUpload.prepare();
    return Object.keys(prepared).length ? prepared : null;
  }

  function model() {
    return currentModel;
  }

  return { clear: clearControls, model, prepare, sync, values };
}

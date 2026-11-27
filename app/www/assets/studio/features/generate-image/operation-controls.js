import { t } from '../../i18n.js';
import { ApiError } from '../../api.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import {
  getImageEditOperation,
  getImageToImageOperation,
  getTextToImageOperation,
  imageReferenceSpecs,
  auxiliaryReferenceSpecs,
  operationParams,
  requiresPublicReferenceUrl,
} from './operation-capabilities.js';
import { createReferenceUpload } from './reference-upload.js';

const HIDDEN_PARAMS = new Set(['prompt', 'size']);
const ADVANCED_PARAMS = new Set(['negative_prompt', 'seed', 'steps', 'guidance']);

function paramLabel(name) {
  const key = 'generateImage.param.' + name;
  const translated = t(key);
  if (translated !== key) return translated;
  return name.replaceAll('_', ' ');
}

function enumLabel(name, value) {
  const key = 'generateImage.value.' + name + '.' + value;
  const localized = t(key);
  return localized !== key ? localized : String(value);
}

function defaultHelp(name, spec) {
  if (spec?.default === null || spec?.default === undefined || spec?.default === '') return '';
  return t('generateImage.paramDefault').replace('{value}', enumLabel(name, spec.default));
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
    ...values.map((value) => ({ value: String(value), label: enumLabel(name, value) })),
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
  let disposed = false;
  let currentModel = null;
  let currentOperationName = 'text_to_image';
  const controls = new Map();
  const refControls = new Map();

  const singleUploadTarget = el('div');
  const multiUploadTarget = el('div');
  const auxiliary = ['mask', 'markup'].map((role) => {
    const target = el('div');
    return { role, target, upload: createReferenceUpload({ target, role }) };
  });
  const singleReferenceUpload = createReferenceUpload({ target: singleUploadTarget });
  const multiReferenceUpload = createReferenceUpload({
    target: multiUploadTarget, multiple: true,
    maxFiles: () => refLimit(imageReferenceSpecs(currentModel, 'image_edit')[0]),
  });
  const uploads = [singleReferenceUpload, multiReferenceUpload, ...auxiliary.map(({ upload }) => upload)];

  function clearUploadState() {
    uploads.forEach(upload => upload.clear());
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
    if (disposed) return;
    controls.clear();
    refControls.clear();
    clearUploadState();
    currentModel = model;
    currentOperationName = operationName;
    if (typeof onOperationChange === 'function') onOperationChange(operationName);

    const fields = [];
    const animationFields = [];
    const advancedFields = [];
    const referenceFields = [];
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
        : defaultHelp(name, spec);
      const group = ADVANCED_PARAMS.has(name) && spec?.required !== true ? advancedFields : fields;
      const node = field(paramLabel(name), rendered.node, { help });
      group.push(node);
      if (['frame_count', 'frame_duration_ms', 'output_compression'].includes(name)) {
        animationFields.push({ name, node, control: rendered.control });
      }
    });

    let referenceOperation = operationName;
    if (operationName === 'text_to_image' && !hasEdit && getImageToImageOperation(model)) {
      referenceOperation = 'image_to_image';
    }
    const canAnnotate = auxiliaryReferenceSpecs(model, referenceOperation, 'markup').length > 0;
    if (canAnnotate) {
      const original = renderAssetControl(referenceAssets, { name: 'operation_original_asset' });
      refControls.set('imageAsset', original);
      referenceFields.push(field(t('generateImage.uploadOriginal'), singleUploadTarget, {
        help: t('generateImage.originalHelp'), className: 'span-2',
      }), field(t('generateImage.originalAsset'), original));
    }
    const refs = imageReferenceSpecs(model, referenceOperation);
    refs.forEach((ref, index) => {
      const limit = refLimit(ref);
      const isMulti = referenceOperation === 'image_edit' && limit > 1;
      const publicUrlOnly = requiresPublicReferenceUrl(ref);

      const assetControl = renderAssetControl(referenceAssets, {
        multiple: isMulti,
        name: isMulti ? 'operation_reference_assets_' + index : 'operation_image_asset_' + index,
        dataset: isMulti ? { operationRefAssets: 'reference_images' } : { operationRefAsset: 'image' },
      });
      refControls.set(isMulti ? 'referenceAssets' : 'imageAsset', assetControl);
      const uploadTarget = isMulti ? multiUploadTarget : singleUploadTarget;
      const localHelp = publicUrlOnly
        ? t('generateImage.relayLocalReferenceHelp')
        : (isMulti ? t('generateImage.uploadReferencesHelp') : t('generateImage.uploadReferenceHelp'));
      referenceFields.push(field(
        isMulti ? t(canAnnotate ? 'generateImage.uploadAdditional' : 'generateImage.uploadReferences') : t('generateImage.uploadReference'),
        uploadTarget,
        { help: localHelp, className: 'span-2' },
      ));
      referenceFields.push(field(
        isMulti ? t(canAnnotate ? 'generateImage.additionalAssets' : 'generateImage.referenceAssets') : t('generateImage.referenceAsset'),
        assetControl,
        { help: publicUrlOnly ? t('generateImage.relayAssetReferenceHelp') : (isMulti ? t('generateImage.referenceAssetsHelp') : t('generateImage.referenceAssetHelp')) },
      ));

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
          referenceFields.push(field(t('generateImage.referenceUrls'), urlControl, {
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
          referenceFields.push(field(t('generateImage.imageReference'), urlControl, {
            help: publicUrlOnly ? t('generateImage.publicUrlRequired') : t('generateImage.imageReferenceHelp'),
          }));
        }
      }
    });

    const maskFields = [];
    for (const { role, target: uploadTarget } of auxiliary) {
      if (!auxiliaryReferenceSpecs(model, referenceOperation, role).length) continue;
      const isMarkup = role === 'markup';
      const assets = isMarkup ? referenceAssets.filter(asset => /\.png$/i.test(asset.value)) : referenceAssets;
      const control = renderAssetControl(assets, { name: `operation_${role}_asset` });
      refControls.set(`${role}Asset`, control);
      const nodes = [field(t(isMarkup ? 'generateImage.uploadMarkup' : 'generateImage.uploadMask'), uploadTarget, {
        help: t(isMarkup ? 'generateImage.uploadMarkupHelp' : 'generateImage.uploadMaskHelp'), className: 'span-2',
      }), field(t(`generateImage.${role}Asset`), control, { help: t(`generateImage.${role}AssetHelp`) })];
      if (!isMarkup) maskFields.push(...nodes);
      referenceFields.push(...nodes);
    }

    const formatControl = controls.get('output_format');
    const formats = Array.from(formatControl?.options || [], option => option.value);
    if (formats.includes('gif') || formats.includes('svg')) {
      const gifHint = el('p', { class: 'field-help span-2' }, t('generateImage.gifPreviewHint'));
      const svgHint = formats.includes('svg') ? el('p', { class: 'field-help span-2' }, t('generateImage.svgPreviewHint')) : null;
      fields.push(gifHint);
      if (svgHint) fields.push(svgHint);
      const updateFormat = () => {
        const gif = formatControl.value === 'gif';
        const svg = formatControl.value === 'svg';
        for (const {name, node, control} of animationFields) {
          node.hidden = control.disabled = name === 'output_compression' ? gif || svg : !gif;
        }
        for (const node of maskFields) node.hidden = gif;
        const mask = refControls.get('maskAsset');
        if (mask) mask.disabled = gif;
        const transparent = Array.from(controls.get('background')?.options || [])
          .find(option => option.value === 'transparent');
        if (transparent) {
          transparent.disabled = gif || svg;
          if (transparent.disabled && transparent.selected) transparent.parentElement.value = '';
        }
        gifHint.hidden = !gif;
        if (svgHint) svgHint.hidden = !svg;
      };
      formatControl.addEventListener('change', updateFormat);
      updateFormat();
    }

    if (!fields.length && !advancedFields.length && !referenceFields.length) {
      target.hidden = true;
      mount(target);
      return;
    }
    target.hidden = false;
    mount(target,
      fields.length ? el('div', { class: 'form-grid', dataset: { operationControls: 'true' } }, fields) : null,
      referenceFields.length ? el('details', {
        class: 'list-disclosure',
        open: operationName === 'image_edit'
          || (referenceOperation === operationName && refs.some((ref) => ref.required === true)),
        dataset: { creationReferences: 'true' },
      },
        el('summary', {}, t('creation.references')),
        el('div', { class: 'form-grid' }, referenceFields),
      ) : null,
      advancedFields.length ? el('details', { class: 'list-disclosure', dataset: { creationAdvanced: 'true' } },
        el('summary', {}, t('creation.advanced')),
        el('p', { class: 'field-help' }, t('creation.advancedHelp')),
        el('div', { class: 'form-grid' }, advancedFields),
      ) : null,
    );
  }

  function sync(model) {
    if (disposed) return;
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
      if (control.disabled) return;
      const value = String(control.value || '').trim();
      if (value) result[name] = value;
    });
    if (currentOperationName === 'image_edit') result.operation = 'edit';

    const asset = selectedValues(refControls.get('imageAsset'))[0];
    if (asset) result.image = asset;
    if (currentOperationName === 'image_edit') {
      const references = selectedValues(refControls.get('referenceAssets'));
      const urlReferences = String(refControls.get('referenceUrls')?.value || '')
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean);
      const combined = [...references, ...urlReferences];
      if (combined.length) result.reference_images = combined;
    } else if (!asset) {
      const url = String(refControls.get('imageUrl')?.value || '').trim();
      if (url) result.image = url;
    }
    for (const { role } of auxiliary) {
      const control = refControls.get(`${role}Asset`);
      const value = control?.disabled ? null : selectedValues(control)[0];
      if (value) result[role] = value;
    }
    return result;
  }

  async function prepare(isActive = () => true) {
    // 所有输入先绑定同一次选择，避免上传期间改变原图与标注的配对。
    const selected = values();
    const required = imageReferenceSpecs(currentModel, currentOperationName).some(ref => ref.required === true);
    if (required && !selected.image && !selected.reference_images?.length
        && !singleReferenceUpload.hasPendingFile() && !multiReferenceUpload.hasPendingFile()) {
      const message = t('generateImage.referenceRequired');
      throw new ApiError(message, { safe: { code: 'image_reference_required', message } });
    }
    const active = auxiliary.filter(({ role }) => refControls.has(`${role}Asset`) && !refControls.get(`${role}Asset`).disabled);
    const hasAux = role => Boolean(selected[role] || active.find(item => item.role === role)?.upload.hasPendingFile());
    if (hasAux('markup')) {
      const errorKey = selected.output_format === 'gif' ? 'image_markup_static'
        : hasAux('mask') ? 'image_markup_mask_conflict'
        : !selected.image && !singleReferenceUpload.hasPendingFile() ? 'image_markup_source_required' : '';
      if (errorKey) {
        const message = t(`generateImage.${errorKey}`);
        throw new ApiError(message, { safe: { code: errorKey, message } });
      }
    }
    const captures = [['image', singleReferenceUpload], ['reference_images', multiReferenceUpload],
      ...active.map(({ role, upload }) => [role, upload])]
      .map(([key, upload]) => [key, upload.capture(isActive)]);
    const prepared = {};
    for (const [key, capture] of captures) {
      const value = await capture();
      if (value?.length) prepared[key] = value;
    }
    return Object.keys(prepared).length ? prepared : null;
  }

  function model() {
    return currentModel;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    clearControls();
    uploads.forEach(upload => upload.dispose());
  }

  return { clear: clearControls, model, prepare, sync, values, dispose,
    suspend: () => uploads.forEach(upload => upload.suspend()),
    resume: () => uploads.forEach(upload => upload.resume()),
  };
}

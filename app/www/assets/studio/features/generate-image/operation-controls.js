import { t } from '../../i18n.js';
import { el, mount } from '../../components/dom.js';
import { field, input, textarea } from '../../components/forms.js';
import {
  getTextToImageOperation,
  hasOperationRefs,
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
    autocomplete: 'off',
    dataset: { operationParam: name },
  };
  if (spec.min !== null && spec.min !== undefined) attrs.min = String(spec.min);
  if (spec.max !== null && spec.max !== undefined) attrs.max = String(spec.max);
  if (spec.default !== null && spec.default !== undefined) attrs.placeholder = String(spec.default);
  if (spec.kind === 'float') attrs.step = '0.1';
  return attrs;
}

function renderParamControl(name, spec) {
  if (HIDDEN_PARAMS.has(name)) return null;
  if (spec.kind === 'string') {
    return textarea({
      name: `operation_${name}`,
      rows: 3,
      autocomplete: 'off',
      dataset: { operationParam: name },
    });
  }
  if (spec.kind === 'int' || spec.kind === 'seed' || spec.kind === 'float') {
    return input(numberAttrs(name, spec));
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

export function createOperationControls({ target }) {
  let currentModel = null;
  const controls = new Map();

  function clearControls() {
    currentModel = null;
    controls.clear();
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
      const control = renderParamControl(name, spec || {});
      if (!control) return;
      controls.set(name, control);
      fields.push(field(paramLabel(name), control, { help: defaultHelp(spec || {}) }));
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

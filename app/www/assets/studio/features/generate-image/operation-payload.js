import {
  activeOperationName,
  imageReferenceSpecs,
  maskReferenceSpecs,
  operationParams,
} from './operation-capabilities.js';

function cleanStrings(values) {
  if (!Array.isArray(values)) return [];
  return values
    .filter((value) => typeof value === 'string')
    .map((value) => value.trim())
    .filter(Boolean);
}

export function coerceOperationValue(rawValue, spec = {}) {
  if (rawValue === null || rawValue === undefined) return null;
  const value = typeof rawValue === 'string' ? rawValue.trim() : rawValue;
  if (value === '') return null;

  if (spec.kind === 'int' || spec.kind === 'seed') {
    const number = Number(value);
    if (!Number.isInteger(number)) return null;
    return number;
  }
  if (spec.kind === 'float') {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  if (spec.kind === 'bool') {
    if (value === true || value === 'true' || value === '1') return true;
    if (value === false || value === 'false' || value === '0') return false;
    return null;
  }
  return value;
}

export function buildOperationPayload(model, values = {}) {
  const payload = {};
  const operationName = activeOperationName(model, values);
  if (!operationName) return payload;
  const params = operationParams(model, operationName);
  Object.entries(values || {}).forEach(([name, rawValue]) => {
    if (!Object.prototype.hasOwnProperty.call(params, name)) return;
    if (name === 'prompt' || name === 'size') return;
    const value = coerceOperationValue(rawValue, params[name]);
    if (value !== null) payload[name] = value;
  });

  if (operationName === 'image_edit') payload.operation = 'edit';
  else if (values.operation === 'generate') payload.operation = 'generate';

  const imageValue = typeof values.image === 'string' ? values.image.trim() : '';
  const references = cleanStrings(values.reference_images);
  if (imageValue && imageReferenceSpecs(model, operationName).length) {
    payload.image = imageValue;
  }
  if (references.length && imageReferenceSpecs(model, operationName).length) {
    payload.reference_images = references;
  }
  const mask = typeof values.mask === 'string' ? values.mask.trim() : '';
  if (mask && maskReferenceSpecs(model, operationName).length) {
    payload.mask = mask;
  }
  return payload;
}

export function aspectRatioOverridesSize(model, values = {}) {
  const operationName = activeOperationName(model, values);
  const spec = operationParams(model, operationName).aspect_ratio;
  const value = typeof values.aspect_ratio === 'string' ? values.aspect_ratio.trim() : '';
  return Boolean(value && spec && spec.allow_with_size !== true);
}

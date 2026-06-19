import {
  getImageToImageOperation,
  getTextToImageOperation,
  imageReferenceSpecs,
} from './operation-capabilities.js';

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
    return Boolean(value);
  }
  return value;
}

export function buildOperationPayload(model, values = {}) {
  const payload = {};
  const imageValue = typeof values.image === 'string' ? values.image.trim() : '';
  const operation = imageValue ? getImageToImageOperation(model) : getTextToImageOperation(model);
  const params = operation?.params || {};
  Object.entries(values || {}).forEach(([name, rawValue]) => {
    if (!Object.prototype.hasOwnProperty.call(params, name)) return;
    if (name === 'prompt' || name === 'size') return;
    const value = coerceOperationValue(rawValue, params[name]);
    if (value !== null) payload[name] = value;
  });
  if (imageValue && imageReferenceSpecs(model).length) {
    payload.image = imageValue;
  }
  return payload;
}

export function aspectRatioOverridesSize(model, values = {}) {
  const imageValue = typeof values.image === 'string' ? values.image.trim() : '';
  const operation = imageValue ? getImageToImageOperation(model) : getTextToImageOperation(model);
  const spec = operation?.params?.aspect_ratio;
  const value = typeof values.aspect_ratio === 'string' ? values.aspect_ratio.trim() : '';
  return Boolean(value && spec && spec.allow_with_size !== true);
}

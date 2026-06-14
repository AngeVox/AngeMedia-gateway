import { supportsOperationParam } from './operation-capabilities.js';

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
  const params = model?.operations?.text_to_image?.params || {};
  Object.entries(values || {}).forEach(([name, rawValue]) => {
    if (!supportsOperationParam(model, name)) return;
    if (name === 'prompt' || name === 'size') return;
    const value = coerceOperationValue(rawValue, params[name]);
    if (value !== null) payload[name] = value;
  });
  return payload;
}

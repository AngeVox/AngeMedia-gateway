function positiveConstraint(value, fallback) {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

export function validateCustomSize(value, constraints = {}) {
  const match = String(value || '').trim().match(/^([1-9]\d{1,3})x([1-9]\d{1,3})$/i);
  if (!match) {
    return { ok: false, messageKey: 'generateImage.sizeInvalidFormat' };
  }
  const width = Number(match[1]);
  const height = Number(match[2]);
  const minWidth = positiveConstraint(constraints?.min_width, 256);
  const maxWidth = positiveConstraint(constraints?.max_width, 4096);
  const minHeight = positiveConstraint(constraints?.min_height, 256);
  const maxHeight = positiveConstraint(constraints?.max_height, 4096);
  const minPixels = positiveConstraint(constraints?.min_pixels, null);
  const maxPixels = positiveConstraint(constraints?.max_pixels, null);
  const multipleOf = positiveConstraint(constraints?.multiple_of, null);
  const pixels = width * height;
  if (
    width < minWidth || width > maxWidth || height < minHeight || height > maxHeight
    || (minPixels && pixels < minPixels) || (maxPixels && pixels > maxPixels)
    || (multipleOf && (width % multipleOf || height % multipleOf))
  ) {
    return { ok: false, messageKey: 'generateImage.sizeInvalidRange' };
  }
  return { ok: true, value: `${width}x${height}` };
}

export function providersFromResponse(result) {
  if (Array.isArray(result?.data)) return result.data;
  if (Array.isArray(result)) return result;
  return [];
}

export function isSelectableImageProvider(item) {
  return Boolean(
    item &&
    typeof item === 'object' &&
    item.enabled === true &&
    item.provider_type === 'openai_image' &&
    item.id
  );
}

export function providerModelValue(providerId) {
  return `custom:${providerId}`;
}

export function providersFromCatalog(result) {
  return Array.isArray(result?.providers) ? result.providers : [];
}

export function modelsFromCatalog(result) {
  return Array.isArray(result?.models) ? result.models : [];
}

export function selectableVideoModels(result) {
  return modelsFromCatalog(result).filter((item) => (
    item &&
    typeof item === 'object' &&
    item.media_type === 'video' &&
    item.selectable === true &&
    item.id
  ));
}

export function selectableImageModels(result) {
  return modelsFromCatalog(result).filter((item) => (
    item &&
    typeof item === 'object' &&
    item.media_type === 'image' &&
    item.selectable === true &&
    ['release', 'experimental'].includes(item.status) &&
    item.id
  ));
}

export function videoProvidersForModels(result, models) {
  const providerIds = new Set(models.map((item) => item.provider_id).filter(Boolean));
  return providersFromCatalog(result).filter((item) => providerIds.has(item.id));
}

export function imageProvidersForModels(result, models) {
  const providerIds = new Set(models.map((item) => item.provider_id).filter(Boolean));
  return providersFromCatalog(result).filter((item) => providerIds.has(item.id));
}

export function imageSizeOptions(model) {
  const presets = Array.isArray(model?.size_presets) ? model.size_presets : [];
  return [
    ...presets.map((preset) => ({ value: preset, label: preset })),
    { value: 'custom', label: 'Custom' },
  ];
}

export function parseSizePreset(value) {
  const match = String(value || '').trim().match(/^([1-9]\d{1,3})x([1-9]\d{1,3})$/i);
  if (!match) return null;
  return { width: Number(match[1]), height: Number(match[2]) };
}

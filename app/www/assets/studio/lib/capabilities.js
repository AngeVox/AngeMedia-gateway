function positiveConstraint(value, fallback) {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function positiveNumberConstraint(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
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
  const maxAspectRatio = positiveNumberConstraint(constraints?.max_aspect_ratio, null);
  const pixels = width * height;
  const aspectRatio = Math.max(width, height) / Math.min(width, height);
  if (
    width < minWidth || width > maxWidth || height < minHeight || height > maxHeight
    || (minPixels && pixels < minPixels) || (maxPixels && pixels > maxPixels)
    || (multipleOf && (width % multipleOf || height % multipleOf))
    || (maxAspectRatio && aspectRatio > maxAspectRatio)
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
    providerAvailability(item).selectable &&
    item.provider_type === 'openai_image' &&
    item.id
  );
}

// 仅判断本地提交前提；连接、账户权限、计费和真实生成证据继续分别展示。
export function providerAvailability(provider) {
  const builtin = typeof provider?.ui_group === 'string';
  if (!provider || (builtin && !provider.ui_group.startsWith('builtin'))
      || (!builtin && provider.provider_type !== 'openai_image')
      || provider.policy?.lifecycle === 'retired') {
    return { selectable: false, reasonKey: 'availability.unsupported' };
  }
  const runtime = builtin ? provider.runtime : provider;
  if (typeof runtime?.enabled !== 'boolean') return { selectable: false, reasonKey: 'availability.unknown' };
  if (!runtime.enabled) return { selectable: false, reasonKey: 'availability.disabled' };
  if ((builtin && provider.requires_key && typeof runtime.api_key_configured !== 'boolean')
      || (!builtin && typeof runtime.configuration_complete !== 'boolean')) {
    return { selectable: false, reasonKey: 'availability.unknown' };
  }
  const configured = builtin ? (!provider.requires_key || runtime.api_key_configured === true)
    : runtime.configuration_complete === true;
  return configured ? { selectable: true, reasonKey: 'availability.configured' }
    : { selectable: false, reasonKey: 'availability.missingConfiguration' };
}

export function providerInventory(catalog, customProviders) {
  return [
    ...providersFromCatalog(catalog).filter((item) => item.ui_group?.startsWith('builtin')),
    ...customProviders,
  ];
}

export function defaultImageRouteReady(catalog) {
  return selectableImageModels(catalog).some((model) => Number.isInteger(model.default_chain_order)
    && providerAvailability(providersFromCatalog(catalog).find((p) => p.id === model.provider_id)).selectable);
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
    item.policy?.lifecycle !== 'retired' &&
    ['release', 'experimental'].includes(item.status) &&
    item.id
  ));
}

export function selectableImageModels(result) {
  return modelsFromCatalog(result).filter((item) => (
    item &&
    typeof item === 'object' &&
    item.media_type === 'image' &&
    item.selectable === true &&
    item.policy?.lifecycle !== 'retired' &&
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

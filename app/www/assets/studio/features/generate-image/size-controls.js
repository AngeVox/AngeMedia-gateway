import { t } from '../../i18n.js';
import { validateCustomSize } from '../../lib/capabilities.js';
import { replaceOptions } from './catalog-state.js';
import { sizeOptionsForModel } from './operation-capabilities.js';

function imageSizeOptions(model) {
  return sizeOptionsForModel(model);
}

export function syncSizeFields(sizeSelect, customSizeInput) {
  customSizeInput.hidden = sizeSelect.value !== 'custom';
}

export function syncSizeOptions({
  sizeSelect,
  customSizeInput,
  sizeCapabilityWarning,
  catalogProviderId,
  customProvider,
  model,
}) {
  const options = imageSizeOptions(model);
  replaceOptions(sizeSelect, options);
  const presetValues = options
    .map((item) => item.value)
    .filter((value) => value && value !== 'custom');
  sizeSelect.value = presetValues[0] || 'custom';
  if (catalogProviderId) {
    sizeCapabilityWarning.textContent = t('generateImage.sizeCapabilityCatalogUnknown');
    sizeCapabilityWarning.hidden = presetValues.length > 0;
  } else if (customProvider) {
    sizeCapabilityWarning.textContent = t('generateImage.sizeCapabilityCustomUnknown');
    sizeCapabilityWarning.hidden = false;
  } else {
    sizeCapabilityWarning.textContent = t('generateImage.sizeCapabilityDefaultHint');
    sizeCapabilityWarning.hidden = false;
  }
  syncSizeFields(sizeSelect, customSizeInput);
}

export function selectedSize(sizeSelect, customSizeInput, model = null) {
  let size = sizeSelect.value || 'custom';
  if (size !== 'custom') {
    return { ok: true, value: size };
  }

  const validation = validateCustomSize(customSizeInput.value, model?.size);
  if (!validation.ok) {
    return validation;
  }
  size = validation.value;
  return { ok: true, value: size };
}

const TEXT_TO_IMAGE_OPERATION = 'text_to_image';
const IMAGE_TO_IMAGE_OPERATION = 'image_to_image';
const IMAGE_EDIT_OPERATION = 'image_edit';

function isObject(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function getOperation(model, name) {
  const operation = model?.operations?.[name];
  if (!isObject(operation) || operation.supported !== true) return null;
  return operation;
}

function nonEmptyString(value) {
  return typeof value === 'string' && Boolean(value.trim());
}

function hasReferenceValues(values = {}) {
  return nonEmptyString(values.image)
    || (Array.isArray(values.reference_images) && values.reference_images.some(nonEmptyString))
    || nonEmptyString(values.mask);
}

export function getTextToImageOperation(model) {
  return getOperation(model, TEXT_TO_IMAGE_OPERATION);
}

export function getImageToImageOperation(model) {
  return getOperation(model, IMAGE_TO_IMAGE_OPERATION);
}

export function getImageEditOperation(model) {
  return getOperation(model, IMAGE_EDIT_OPERATION);
}

export function activeOperationName(model, values = {}) {
  const requested = String(values?.operation || '').trim().toLowerCase();
  if (requested === 'edit' && getImageEditOperation(model)) return IMAGE_EDIT_OPERATION;
  if (requested === 'generate' && getTextToImageOperation(model)) return TEXT_TO_IMAGE_OPERATION;
  if (hasReferenceValues(values)) {
    if (getImageEditOperation(model)) return IMAGE_EDIT_OPERATION;
    if (getImageToImageOperation(model)) return IMAGE_TO_IMAGE_OPERATION;
    return null;
  }
  return getTextToImageOperation(model) ? TEXT_TO_IMAGE_OPERATION : null;
}

export function operationParams(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  const operation = getOperation(model, operationName);
  return isObject(operation?.params) ? operation.params : {};
}

export function operationRefs(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  const operation = getOperation(model, operationName);
  return Array.isArray(operation?.refs) ? operation.refs : [];
}

export function hasOperationRefs(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  return operationRefs(model, operationName).length > 0;
}

export function supportedParamNames(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  return Object.keys(operationParams(model, operationName));
}

export function supportsOperationParam(model, name, operationName = TEXT_TO_IMAGE_OPERATION) {
  return Object.prototype.hasOwnProperty.call(operationParams(model, operationName), name);
}

export function operationSupportsSize(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  return supportsOperationParam(model, 'size', operationName);
}

export function supportsCustomSize(model) {
  return operationParams(model).size?.mode !== 'preset';
}

function referenceSpecsForOperation(model, operationName) {
  return operationRefs(model, operationName)
    .filter((ref) => {
      const field = typeof ref?.provider_field === 'string' ? ref.provider_field : '';
      const roles = Array.isArray(ref?.roles) ? ref.roles : [];
      return field !== 'mask' && !roles.includes('mask');
    });
}

export function imageReferenceSpecs(model, operationName = null) {
  if (operationName) return referenceSpecsForOperation(model, operationName);
  if (getImageToImageOperation(model)) {
    return referenceSpecsForOperation(model, IMAGE_TO_IMAGE_OPERATION);
  }
  return referenceSpecsForOperation(model, IMAGE_EDIT_OPERATION);
}

export function maskReferenceSpecs(model, operationName = IMAGE_EDIT_OPERATION) {
  return operationRefs(model, operationName)
    .filter((ref) => {
      const field = typeof ref?.provider_field === 'string' ? ref.provider_field : '';
      const roles = Array.isArray(ref?.roles) ? ref.roles : [];
      return field === 'mask' || roles.includes('mask');
    });
}

export function supportsImageReference(model) {
  return Boolean(imageReferenceSpecs(model).length);
}

export function supportsImageEdit(model) {
  return Boolean(getImageEditOperation(model));
}

export function requiresPublicReferenceUrl(ref) {
  return ref?.provider_format === 'url';
}

export function sizeOptionsForModel(model) {
  const operationSize = operationParams(model).size;
  const customOption = supportsCustomSize(model) ? [{ value: 'custom', label: 'Custom' }] : [];
  const operationPresets = Array.isArray(operationSize?.presets) ? operationSize.presets : [];
  if (operationPresets.length) {
    return [
      ...operationPresets
        .filter((preset) => typeof preset?.value === 'string' && preset.value.trim())
        .map((preset) => {
          const label = typeof preset.label === 'string' ? preset.label.trim() : '';
          return {
            value: preset.value,
            label: label && label !== preset.value ? label + ' - ' + preset.value : preset.value,
          };
        }),
      ...customOption,
    ];
  }

  const legacyPresets = Array.isArray(model?.size_presets) ? model.size_presets : [];
  return [
    ...legacyPresets.map((preset) => ({ value: preset, label: preset })),
    ...customOption,
  ];
}

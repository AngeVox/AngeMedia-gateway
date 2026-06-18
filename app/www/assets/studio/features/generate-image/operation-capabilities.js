const TEXT_TO_IMAGE_OPERATION = 'text_to_image';
const IMAGE_TO_IMAGE_OPERATION = 'image_to_image';

function isObject(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function getOperation(model, name) {
  const operation = model?.operations?.[name];
  if (!isObject(operation) || operation.supported !== true) return null;
  return operation;
}

export function getTextToImageOperation(model) {
  return getOperation(model, TEXT_TO_IMAGE_OPERATION);
}

export function getImageToImageOperation(model) {
  return getOperation(model, IMAGE_TO_IMAGE_OPERATION);
}

export function operationParams(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  const operation = getOperation(model, operationName);
  return isObject(operation?.params) ? operation.params : {};
}

export function operationRefs(model, operationName = TEXT_TO_IMAGE_OPERATION) {
  const operation = getOperation(model, operationName);
  return Array.isArray(operation?.refs) ? operation.refs : [];
}

export function hasOperationRefs(model) {
  return operationRefs(model).length > 0;
}

export function supportedParamNames(model) {
  return Object.keys(operationParams(model));
}

export function supportsOperationParam(model, name) {
  return Object.prototype.hasOwnProperty.call(operationParams(model), name);
}

export function supportsCustomSize(model) {
  return operationParams(model).size?.mode !== 'preset';
}

export function imageReferenceSpecs(model) {
  return operationRefs(model, IMAGE_TO_IMAGE_OPERATION)
    .filter((ref) => {
      const field = typeof ref?.provider_field === 'string' ? ref.provider_field : '';
      const roles = Array.isArray(ref?.roles) ? ref.roles : [];
      return field === 'image' || roles.includes('input_image');
    });
}

export function supportsImageReference(model) {
  return Boolean(getImageToImageOperation(model) && imageReferenceSpecs(model).length);
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
        .map((preset) => ({
          value: preset.value,
          label: preset.label ? `${preset.label} - ${preset.value}` : preset.value,
        })),
      ...customOption,
    ];
  }

  const legacyPresets = Array.isArray(model?.size_presets) ? model.size_presets : [];
  return [
    ...legacyPresets.map((preset) => ({ value: preset, label: preset })),
    ...customOption,
  ];
}

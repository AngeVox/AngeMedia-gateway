const TEXT_TO_IMAGE_OPERATION = 'text_to_image';

function isObject(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

export function getTextToImageOperation(model) {
  const operation = model?.operations?.[TEXT_TO_IMAGE_OPERATION];
  if (!isObject(operation) || operation.supported !== true) return null;
  return operation;
}

export function operationParams(model) {
  const operation = getTextToImageOperation(model);
  return isObject(operation?.params) ? operation.params : {};
}

export function operationRefs(model) {
  const operation = getTextToImageOperation(model);
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

export function sizeOptionsForModel(model) {
  const operationSize = operationParams(model).size;
  const operationPresets = Array.isArray(operationSize?.presets) ? operationSize.presets : [];
  if (operationPresets.length) {
    return [
      ...operationPresets
        .filter((preset) => typeof preset?.value === 'string' && preset.value.trim())
        .map((preset) => ({
          value: preset.value,
          label: preset.label ? `${preset.label} - ${preset.value}` : preset.value,
        })),
      { value: 'custom', label: 'Custom' },
    ];
  }

  const legacyPresets = Array.isArray(model?.size_presets) ? model.size_presets : [];
  return [
    ...legacyPresets.map((preset) => ({ value: preset, label: preset })),
    { value: 'custom', label: 'Custom' },
  ];
}

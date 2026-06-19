import { t } from '../../i18n.js';
import { toast } from '../../components/toast.js';
import { routeModelValue } from './catalog-state.js';
import { aspectRatioOverridesSize, buildOperationPayload } from './operation-payload.js';
import { selectedSize } from './size-controls.js';

export function buildGenerationPayload({
  promptInput,
  sizeSelect,
  customSizeInput,
  providerSelect,
  modelInput,
  operationValues = {},
  currentCatalogProviderId,
  currentCatalogModel,
  currentCustomProvider,
}) {
  const prompt = promptInput.value.trim();
  if (!prompt) {
    toast(t('generateImage.promptRequired'), 'error');
    promptInput.focus();
    return null;
  }

  const catalogProviderId = currentCatalogProviderId();
  const catalogModel = catalogProviderId ? currentCatalogModel() : null;
  const customProvider = currentCustomProvider();
  if (catalogProviderId && !catalogModel) {
    toast(t('generateImage.modelRequired'), 'error');
    return null;
  }
  const operationPayload = catalogModel ? buildOperationPayload(catalogModel, operationValues) : {};
  const omitSize = catalogModel ? aspectRatioOverridesSize(catalogModel, operationValues) : false;

  const payload = {
    prompt,
    response_format: 'url',
  };
  if (!omitSize) {
    const sizeResult = selectedSize(sizeSelect, customSizeInput, catalogModel);
    if (!sizeResult.ok) {
      toast(t(sizeResult.messageKey), 'error');
      customSizeInput.focus();
      return null;
    }
    Object.assign(payload, { size: sizeResult.value });
  }

  if (customProvider) {
    payload.model = providerSelect.value;
    const provider_model = modelInput.value.trim() || customProvider.default_model || '';
    if (provider_model) payload['provider_model'] = provider_model;
  } else if (catalogProviderId) {
    payload.model = routeModelValue(catalogModel);
    Object.assign(payload, operationPayload);
  } else if (modelInput.value.trim()) {
    payload.model = modelInput.value.trim();
  }

  return { payload, prompt };
}

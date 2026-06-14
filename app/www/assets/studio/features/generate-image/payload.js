import { t } from '../../i18n.js';
import { toast } from '../../components/toast.js';
import { routeModelValue } from './catalog-state.js';
import { buildOperationPayload } from './operation-payload.js';
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

  const sizeResult = selectedSize(sizeSelect, customSizeInput);
  if (!sizeResult.ok) {
    toast(t(sizeResult.messageKey), 'error');
    customSizeInput.focus();
    return null;
  }

  const payload = {
    prompt,
    response_format: 'url',
    size: sizeResult.value,
  };

  const customProvider = currentCustomProvider();
  const catalogProviderId = currentCatalogProviderId();
  if (customProvider) {
    payload.model = providerSelect.value;
    const provider_model = modelInput.value.trim() || customProvider.default_model || '';
    if (provider_model) payload['provider_model'] = provider_model;
  } else if (catalogProviderId) {
    const model = currentCatalogModel();
    if (!model) {
      toast(t('generateImage.modelRequired'), 'error');
      return null;
    }
    payload.model = routeModelValue(model);
    Object.assign(payload, buildOperationPayload(model, operationValues));
  } else if (modelInput.value.trim()) {
    payload.model = modelInput.value.trim();
  }

  return { payload, prompt };
}

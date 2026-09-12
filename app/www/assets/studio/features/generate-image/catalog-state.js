import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { el } from '../../components/dom.js';
import {
  imageProvidersForModels,
  isSelectableImageProvider,
  providerModelValue,
  providersFromResponse,
  selectableImageModels,
} from '../../lib/capabilities.js';

export function option(label, value, disabled = false) {
  return { label, value, disabled };
}

export function providerLabel(provider) {
  return provider?.display_name || provider?.name || provider?.id || '-';
}

export function modelProvider(providers, model) {
  return providers.find((provider) => provider.id === model?.provider_id) || null;
}

export function modelLabel(providers, model) {
  const provider = modelProvider(providers, model);
  const prefix = provider ? `${providerLabel(provider)} / ` : '';
  return `${prefix}${model.display_name || model.id}`;
}

export function routeModelValue(model) {
  const aliases = Array.isArray(model?.aliases) ? model.aliases.filter(Boolean) : [];
  return aliases[0] || model?.id || '';
}

export function catalogProviderValue(providerId) {
  return `catalog:${providerId}`;
}

export function catalogProviderIdFromValue(value) {
  return value && value.startsWith('catalog:') ? value.slice('catalog:'.length) : '';
}


export function customProviderOperationModel(provider) {
  if (!provider) return null;
  const capabilities = provider.capabilities || {};
  const sizeParam = {
    kind: 'size',
    provider_field: 'size',
    evidence: 'unknown',
    mode: 'freeform',
    presets: [],
  };
  const promptParam = { kind: 'string', provider_field: 'prompt', evidence: 'unknown', required: true };
  const operations = {
    text_to_image: {
      supported: true,
      params: { prompt: promptParam, size: sizeParam },
      refs: [],
    },
  };
  if (capabilities.image_edit === true) {
    const maxReferences = Math.max(1, Math.min(10, Number(capabilities.max_reference_images || 1)));
    const refs = [{
      roles: ['image', 'reference_images'],
      provider_field: 'image',
      max_count: maxReferences,
      max_total: maxReferences,
      formats: ['data_url'],
      provider_format: 'data_url',
      required: true,
    }];
    if (capabilities.supports_mask === true) {
      refs.push({
        roles: ['mask'],
        provider_field: 'mask',
        max_count: 1,
        max_total: 1,
        formats: ['data_url'],
        provider_format: 'data_url',
        required: false,
      });
    }
    operations.image_edit = {
      supported: true,
      params: { prompt: promptParam, size: sizeParam },
      refs,
    };
  }
  return {
    id: 'custom:' + provider.id,
    provider_id: provider.id,
    provider_model: provider.default_model || '',
    display_name: provider.name || provider.id,
    size: { mode: 'freeform', presets: [] },
    size_presets: [],
    operations,
  };
}

export function customProviderByValue(providers, value) {
  if (!value || !value.startsWith('custom:')) return null;
  const id = value.slice('custom:'.length);
  return providers.find((provider) => provider.id === id) || null;
}

export function modelsForProvider(models, providerId) {
  return models.filter((model) => !providerId || model.provider_id === providerId);
}

export function modelById(models, id) {
  return models.find((model) => model.id === id) || null;
}

export function replaceOptions(node, items) {
  node.textContent = '';
  items.forEach((item) => {
    node.appendChild(el('option', { value: item.value, disabled: item.disabled }, item.label));
  });
}

export function providerOptions(catalogProviders, customProviders) {
  const options = [option(t('generateImage.providerDefault'), '')];
  catalogProviders.forEach((provider) => {
    options.push(option(providerLabel(provider), catalogProviderValue(provider.id)));
  });
  customProviders.forEach((provider) => {
    const model = provider.default_model ? ` - ${provider.default_model}` : '';
    options.push(option(`${provider.name || provider.id}${model}`, providerModelValue(provider.id)));
  });
  return options;
}

export function buildCatalogState(catalog, customProviders) {
  const catalogModels = selectableImageModels(catalog);
  const catalogProviders = imageProvidersForModels(catalog, catalogModels);
  return { catalogModels, catalogProviders, customProviders };
}

export async function loadCatalog() {
  return api.get('/admin/catalog');
}

export async function loadProviders() {
  const result = await api.get('/admin/providers');
  return providersFromResponse(result).filter(isSelectableImageProvider);
}

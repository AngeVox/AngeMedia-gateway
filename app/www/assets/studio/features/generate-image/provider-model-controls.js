import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { metaGrid } from '../../components/page.js';
import { emptyState } from '../../components/states.js';
import { safeText } from '../../lib/security.js';
import {
  catalogProviderIdFromValue,
  customProviderByValue,
  modelById,
  modelLabel,
  modelProvider,
  modelsForProvider,
  option,
  providerLabel,
  replaceOptions,
  routeModelValue,
} from './catalog-state.js';
import { syncSizeFields, syncSizeOptions } from './size-controls.js';

export function providerModeFromSelection(catalogProviderId, customProvider) {
  if (catalogProviderId) return 'catalog';
  if (customProvider) return 'custom';
  return 'default';
}

export function providerHelpKeyForMode(mode, providerLoadFailed = false) {
  if (mode === 'catalog') return 'generateImage.providerHelpCatalog';
  if (mode === 'custom') return 'generateImage.providerHelpCustom';
  if (providerLoadFailed) return 'generateImage.providerLoadFailed';
  return 'generateImage.providerHelpDefault';
}

function renderSelectionSummary(target, catalogProviders, model, customProvider, routeMode) {
  if (routeMode === 'default') {
    mount(target, emptyState(t('generateImage.defaultRouteSummary')));
    return;
  }
  if (customProvider) {
    mount(target,
      el('div', { class: 'video-model-summary' },
        el('div', { class: 'job-card-header' },
          el('div', {},
            el('p', { class: 'card-title' }, safeText(customProvider.name || customProvider.id, 80)),
            el('p', { class: 'card-subtitle' }, t('generateImage.customProviderSummary')),
          ),
          badge(t('providers.customProviders'), 'info'),
        ),
        metaGrid([
          { label: t('generateImage.model'), value: safeText(customProvider.default_model || '-', 96) },
        ]),
      ),
    );
    return;
  }
  const provider = modelProvider(catalogProviders, model);
  mount(target,
    el('div', { class: 'video-model-summary' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, safeText(model?.display_name || model?.id || '-', 80)),
          el('p', { class: 'card-subtitle' }, safeText(providerLabel(provider), 80)),
        ),
        badge(safeText(model?.status || '-', 24), model?.status === 'release' ? 'success' : 'warning'),
      ),
      metaGrid([
        { label: t('generateImage.provider'), value: providerLabel(provider) },
        { label: t('generateImage.model'), value: safeText(model?.provider_model || routeModelValue(model), 96) },
        { label: t('generateImage.size'), value: (model?.size_presets || []).join(', ') },
      ]),
    ),
  );
}

export function createProviderModelControls({
  catalogModels,
  catalogProviders,
  customProviders,
  providerSelect,
  modelSelect,
  modelInput,
  modelSelectField,
  modelInputField,
  sizeSelect,
  customSizeInput,
  sizeCapabilityWarning,
  selectionSummary,
}) {
  const modelInputLabel = modelInputField.querySelector('.field-label');

  function currentCatalogProviderId() {
    return catalogProviderIdFromValue(providerSelect.value);
  }

  function currentCatalogModels() {
    return modelsForProvider(catalogModels, currentCatalogProviderId());
  }

  function currentCatalogModel() {
    return modelById(catalogModels, modelSelect.value) || currentCatalogModels()[0] || null;
  }

  function currentCustomProvider() {
    return customProviderByValue(customProviders, providerSelect.value);
  }

  function currentProviderMode() {
    return providerModeFromSelection(currentCatalogProviderId(), currentCustomProvider());
  }

  function syncCurrentSizeOptions() {
    const catalogProviderId = currentCatalogProviderId();
    const customProvider = currentCustomProvider();
    const model = catalogProviderId ? currentCatalogModel() : null;
    syncSizeOptions({
      sizeSelect,
      customSizeInput,
      sizeCapabilityWarning,
      catalogProviderId,
      customProvider,
      model,
    });
  }

  function syncModelOptions(providerChanged = false) {
    const catalogProviderId = currentCatalogProviderId();
    const customProvider = currentCustomProvider();
    const providerMode = providerModeFromSelection(catalogProviderId, customProvider);
    modelSelectField.hidden = providerMode !== 'catalog';
    modelInputField.hidden = providerMode === 'catalog';

    if (providerMode === 'catalog') {
      if (modelInputLabel) modelInputLabel.textContent = t('generateImage.routeModel');
      modelInput.value = '';
      const models = currentCatalogModels();
      replaceOptions(modelSelect, models.map((model) => option(modelLabel(catalogProviders, model), model.id)));
      if (!modelById(models, modelSelect.value) && models[0]) {
        modelSelect.value = models[0].id;
      }
    } else if (providerMode === 'custom') {
      if (modelInputLabel) modelInputLabel.textContent = t('generateImage.providerModelOverride');
      if (providerChanged) modelInput.value = customProvider.default_model || '';
      modelInput.placeholder = t('generateImage.customProviderModel');
    } else {
      if (modelInputLabel) modelInputLabel.textContent = t('generateImage.routeModel');
      if (providerChanged) modelInput.value = '';
      modelInput.placeholder = t('generateImage.modelPlaceholder');
    }

    syncCurrentSizeOptions();
    renderSelectionSummary(
      selectionSummary,
      catalogProviders,
      providerMode === 'catalog' ? currentCatalogModel() : null,
      customProvider,
      providerMode,
    );
  }

  function handleModelChange() {
    syncCurrentSizeOptions();
    renderSelectionSummary(selectionSummary, catalogProviders, currentCatalogModel(), null, 'selected');
  }

  return {
    currentCatalogProviderId,
    currentCatalogModel,
    currentCustomProvider,
    currentProviderMode,
    handleModelChange,
    syncModelOptions,
    syncSizeFields: () => syncSizeFields(sizeSelect, customSizeInput),
  };
}

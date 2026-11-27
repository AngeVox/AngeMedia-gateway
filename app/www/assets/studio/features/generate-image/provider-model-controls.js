import { providerAvailability } from '../../lib/capabilities.js';
import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { metaGrid } from '../../components/page.js';
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
    mount(target);
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
  if (!model) {
    mount(target);
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
      ),
    ),
  );
}

export function createProviderModelControls({
  catalogModels,
  filterCatalogModels = (models) => models,
  catalogProviders,
  customProviders,
  providerSelect,
  modelSelect,
  modelInput,
  modelSelectField,
  modelInputField,
  sizeSelect,
  customSizeInput,
  customSizeField,
  sizeCapabilityWarning,
  selectionSummary,
}) {
  const modelInputLabel = modelInputField.querySelector('.field-label');

  function currentCatalogProviderId() {
    return catalogProviderIdFromValue(providerSelect.value);
  }

  function currentCatalogModels() {
    return filterCatalogModels(modelsForProvider(catalogModels, currentCatalogProviderId()));
  }

  function currentCatalogModel() {
    const model = modelById(currentCatalogModels(), modelSelect.value);
    return model && providerAvailability(modelProvider(catalogProviders, model)).selectable ? model : null;
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
      customSizeField,
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
      const previousModel = providerChanged ? '' : modelSelect.value;
      replaceOptions(modelSelect, models.map((model) => option(modelLabel(model), model.id, !providerAvailability(modelProvider(catalogProviders, model)).selectable)));
      if (previousModel) modelSelect.value = previousModel;
      if (!currentCatalogModel()) {
        modelSelect.value = models.find((model) => providerAvailability(modelProvider(catalogProviders, model)).selectable)?.id || '';
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
    syncSizeFields: () => syncSizeFields(
      sizeSelect,
      customSizeInput,
      customSizeField,
      currentCatalogProviderId() ? currentCatalogModel() : null,
    ),
  };
}

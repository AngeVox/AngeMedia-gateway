import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button, linkButton } from '../../components/buttons.js';
import { statusBadge, badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import { pageHeader, panel, metaGrid } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { showWipFeature } from '../../components/wip.js';
import {
  imageProvidersForModels,
  imageSizeOptions,
  isSelectableImageProvider,
  providerModelValue,
  providersFromResponse,
  selectableImageModels,
  validateCustomSize,
} from '../../lib/capabilities.js';
import { safeAssetHref, buildAssetDownloadName } from '../../lib/asset-url.js';
import { errorDiagnostics, safeErrorMessage } from '../../lib/safe-error.js';
import { formatDate, formatDuration, shortId, truncateText } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';
import { navigate } from '../../router.js';

function option(label, value, disabled = false) {
  return { label, value, disabled };
}

function imageUrlFromResult(result) {
  const item = Array.isArray(result?.data) ? result.data[0] : null;
  return item?.url || '';
}

function providerLabel(provider) {
  return provider?.display_name || provider?.name || provider?.id || '-';
}

function modelProvider(providers, model) {
  return providers.find((provider) => provider.id === model?.provider_id) || null;
}

function modelLabel(providers, model) {
  const provider = modelProvider(providers, model);
  const prefix = provider ? `${providerLabel(provider)} / ` : '';
  return `${prefix}${model.display_name || model.id}`;
}

function routeModelValue(model) {
  const aliases = Array.isArray(model?.aliases) ? model.aliases.filter(Boolean) : [];
  return aliases[0] || model?.id || '';
}

function catalogProviderValue(providerId) {
  return `catalog:${providerId}`;
}

function catalogProviderIdFromValue(value) {
  return value && value.startsWith('catalog:') ? value.slice('catalog:'.length) : '';
}

function customProviderByValue(providers, value) {
  if (!value || !value.startsWith('custom:')) return null;
  const id = value.slice('custom:'.length);
  return providers.find((provider) => provider.id === id) || null;
}

function modelsForProvider(models, providerId) {
  return models.filter((model) => !providerId || model.provider_id === providerId);
}

function modelById(models, id) {
  return models.find((model) => model.id === id) || null;
}

function replaceOptions(node, items) {
  node.textContent = '';
  items.forEach((item) => {
    node.appendChild(el('option', { value: item.value, disabled: item.disabled }, item.label));
  });
}

function providerOptions(catalogProviders, customProviders) {
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

function recentJobCard(job) {
  return el('article', { class: `job-card ${job.status === 'failed' ? 'failed' : ''}` },
    el('div', { class: 'job-card-header' },
      el('div', {},
        el('p', { class: 'card-title' }, shortId(job.id)),
        el('p', { class: 'card-subtitle' }, truncateText(safeText(job.prompt || '-', 160), 72)),
      ),
      statusBadge(job.status, t(`jobs.${job.status}`)),
    ),
    metaGrid([
      { label: t('jobs.provider'), value: safeText(job.provider || '-', 80) },
      { label: t('jobs.model'), value: safeText(job.model || '-', 80) },
      { label: t('jobs.created'), value: formatDate(job.created_at) },
    ]),
  );
}

function renderResultEmpty(target) {
  mount(target, emptyState(t('generateImage.resultEmptyTitle'), t('generateImage.resultEmptyCopy')));
}

function renderResultLoading(target) {
  mount(target, loadingState(t('generateImage.generating')));
}

function renderResultSuccess(target, result, prompt) {
  const imageUrl = safeAssetHref(imageUrlFromResult(result));
  const assetLike = {
    id: result.job_id || result.history_id || 'generated',
    prompt,
    filename: imageUrl,
    url_path: imageUrl,
    media_type: 'image',
  };

  const preview = imageUrl ?
    el('div', { class: 'preview-box' }, el('img', { class: 'result-image', src: imageUrl, alt: t('generateImage.previewAlt') })) :
    el('div', { class: 'preview-box' }, emptyState(t('generateImage.imageUnavailable')));

  mount(target,
    el('div', { class: 'result-success' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateImage.success')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt, 240), 120)),
        ),
        badge(t('jobs.succeeded'), 'success'),
      ),
      preview,
      imageUrl ? linkButton(t('generateImage.download'), imageUrl, {
        variant: 'primary',
        download: buildAssetDownloadName(assetLike),
      }) : null,
      metaGrid([
        { label: t('generateImage.provider'), value: safeText(result.provider, 80) },
        { label: t('generateImage.model'), value: safeText(result.model, 80) },
        { label: t('generateImage.jobId'), value: result.job_id },
        { label: t('generateImage.historyId'), value: result.history_id },
        { label: t('generateImage.duration'), value: result.duration_ms ? formatDuration(result.duration_ms) : '' },
      ]),
    ),
  );
}

function renderResultError(target, error) {
  const diagnostics = errorDiagnostics(error);
  const message = safeErrorMessage(error, error?.status === 409 ? t('generateImage.duplicate') : t('generateImage.error'));
  mount(target,
    el('div', { class: 'diagnostic-card' },
      el('p', { class: 'card-title' }, message),
      metaGrid([
        { label: t('jobs.errorCategory'), value: diagnostics.error_category },
        { label: t('jobs.gatewayStage'), value: diagnostics.gateway_stage },
        { label: t('jobs.retryable'), value: diagnostics.retryable === true ? t('jobs.retryable') : diagnostics.retryable === false ? t('jobs.notRetryable') : '' },
        { label: t('generateImage.jobId'), value: diagnostics.existing_job?.job_id },
      ]),
    ),
  );
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

async function loadCatalog() {
  return api.get('/admin/catalog');
}

async function loadProviders() {
  const result = await api.get('/admin/providers');
  return providersFromResponse(result).filter(isSelectableImageProvider);
}

async function loadRecentImageJobs() {
  const result = await api.get('/jobs?kind=image&limit=4&offset=0');
  return Array.isArray(result?.data) ? result.data : [];
}

function buildPage(catalog, customProviders, recentJobs, providerLoadFailed) {
  const catalogModels = selectableImageModels(catalog);
  const catalogProviders = imageProvidersForModels(catalog, catalogModels);
  const providerSelect = select(providerOptions(catalogProviders, customProviders), { name: 'provider' });
  const modelSelect = select([], { name: 'model' });
  const modelInput = input({ name: 'model', type: 'text', autocomplete: 'off', placeholder: t('generateImage.modelPlaceholder') });
  const sizeSelect = select([], { name: 'size' });
  const customSizeInput = input({
    name: 'custom_size',
    type: 'text',
    autocomplete: 'off',
    placeholder: t('generateImage.customSizePlaceholder'),
    value: '1024x1024',
  });
  const promptInput = textarea({
    name: 'prompt',
    maxLength: 32000,
    placeholder: t('generateImage.promptPlaceholder'),
  });
  const resultPanel = el('div', { class: 'result-frame' });
  const selectionSummary = el('div', { class: 'video-summary-frame' });
  const providerStatus = el('p', { class: providerLoadFailed ? 'error-text' : 'field-help' },
    providerLoadFailed ? t('generateImage.providerLoadFailed') : t('generateImage.providerHelp'),
  );
  const sizeCapabilityWarning = el('p', { class: 'field-help' }, t('generateImage.sizeCapabilityUnknown'));
  const submit = button(t('generateImage.submit'), { variant: 'primary' });
  const modelSelectField = field(t('generateImage.model'), modelSelect);
  const modelInputField = field(t('generateImage.routeModel'), modelInput);
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

  function syncSizeFields() {
    customSizeInput.hidden = sizeSelect.value !== 'custom';
  }

  function syncSizeOptions() {
    const catalogProviderId = currentCatalogProviderId();
    const customProvider = customProviderByValue(customProviders, providerSelect.value);
    const model = catalogProviderId ? currentCatalogModel() : null;
    const options = imageSizeOptions(model);
    replaceOptions(sizeSelect, options);
    const presets = Array.isArray(model?.size_presets) ? model.size_presets : [];
    sizeSelect.value = presets[0] || 'custom';
    if (catalogProviderId) {
      sizeCapabilityWarning.textContent = t('generateImage.sizeCapabilityCatalogUnknown');
      sizeCapabilityWarning.hidden = presets.length > 0;
    } else if (customProvider) {
      sizeCapabilityWarning.textContent = t('generateImage.sizeCapabilityCustomUnknown');
      sizeCapabilityWarning.hidden = false;
    } else {
      sizeCapabilityWarning.textContent = t('generateImage.sizeCapabilityDefaultHint');
      sizeCapabilityWarning.hidden = false;
    }
    syncSizeFields();
  }

  function syncModelOptions(providerChanged = false) {
    const catalogProviderId = currentCatalogProviderId();
    const customProvider = customProviderByValue(customProviders, providerSelect.value);
    modelSelectField.hidden = !catalogProviderId;
    modelInputField.hidden = Boolean(catalogProviderId);

    if (catalogProviderId) {
      if (modelInputLabel) modelInputLabel.textContent = t('generateImage.routeModel');
      const models = currentCatalogModels();
      replaceOptions(modelSelect, models.map((model) => option(modelLabel(catalogProviders, model), model.id)));
      if (!modelById(models, modelSelect.value) && models[0]) {
        modelSelect.value = models[0].id;
      }
    } else if (customProvider) {
      if (modelInputLabel) modelInputLabel.textContent = t('generateImage.providerModelOverride');
      if (providerChanged) modelInput.value = customProvider.default_model || '';
      modelInput.placeholder = t('generateImage.customProviderModel');
    } else {
      if (modelInputLabel) modelInputLabel.textContent = t('generateImage.routeModel');
      if (providerChanged) modelInput.value = '';
      modelInput.placeholder = t('generateImage.modelPlaceholder');
    }

    syncSizeOptions();
    renderSelectionSummary(
      selectionSummary,
      catalogProviders,
      currentCatalogModel(),
      customProvider,
      !providerSelect.value ? 'default' : 'selected',
    );
  }

  async function submitGeneration() {
    const prompt = promptInput.value.trim();
    if (!prompt) {
      toast(t('generateImage.promptRequired'), 'error');
      promptInput.focus();
      return;
    }

    let size = sizeSelect.value || 'custom';
    if (size === 'custom') {
      const validation = validateCustomSize(customSizeInput.value);
      if (!validation.ok) {
        toast(t(validation.messageKey), 'error');
        customSizeInput.focus();
        return;
      }
      size = validation.value;
    }

    const payload = {
      prompt,
      response_format: 'url',
      size: size,
    };

    const customProvider = customProviderByValue(customProviders, providerSelect.value);
    const catalogProviderId = currentCatalogProviderId();
    if (customProvider) {
      payload.model = providerSelect.value;
      const provider_model = modelInput.value.trim() || customProvider.default_model || '';
      if (provider_model) payload['provider_model'] = provider_model;
    } else if (catalogProviderId) {
      const model = currentCatalogModel();
      if (!model) {
        toast(t('generateImage.modelRequired'), 'error');
        return;
      }
      payload.model = routeModelValue(model);
    } else if (modelInput.value.trim()) {
      payload.model = modelInput.value.trim();
    }

    submit.disabled = true;
    submit.textContent = t('generateImage.generating');
    renderResultLoading(resultPanel);
    try {
      const result = await api.post('/images/generations', payload);
      renderResultSuccess(resultPanel, result, prompt);
    } catch (error) {
      renderResultError(resultPanel, error);
    } finally {
      submit.disabled = false;
      submit.textContent = t('generateImage.submit');
    }
  }

  providerSelect.addEventListener('change', () => syncModelOptions(true));
  modelSelect.addEventListener('change', () => {
    syncSizeOptions();
    renderSelectionSummary(selectionSummary, catalogProviders, currentCatalogModel(), null, 'selected');
  });
  sizeSelect.addEventListener('change', syncSizeFields);
  submit.addEventListener('click', submitGeneration);
  syncModelOptions(true);
  renderResultEmpty(resultPanel);

  return [
    pageHeader({
      kicker: t('generateImage.kicker'),
      title: t('generateImage.title'),
      subtitle: t('generateImage.subtitle'),
      actions: [
        button(t('generateImage.videoWipAction'), { onClick: () => navigate('#/generate/video') }),
        button(t('generateImage.promptCopilotAction'), {
          onClick: () => showWipFeature({ title: t('wip.promptCopilotTitle') }),
        }),
      ],
    }),
    el('div', { class: 'generate-grid' },
      panel({ title: t('generateImage.title'), className: 'creator-panel' },
        el('div', { class: 'panel-body form-stack' },
          field(t('generateImage.prompt'), promptInput, { className: 'span-2' }),
          el('div', { class: 'form-grid' },
            field(t('generateImage.provider'), providerSelect),
            modelSelectField,
            modelInputField,
            field(t('generateImage.size'), sizeSelect),
            field(t('generateImage.customSize'), customSizeInput),
          ),
          sizeCapabilityWarning,
          el('div', { class: 'hint-box' }, el('span', {}, 'i'), providerStatus),
          el('div', { class: 'action-row creator-actions' },
            button(t('generateImage.promptCopilotAction'), {
              onClick: () => showWipFeature({ title: t('wip.promptCopilotTitle') }),
            }),
            button(t('generateImage.routeAdviceAction'), {
              onClick: () => showWipFeature({ title: t('wip.planningTitle') }),
            }),
            submit,
          ),
        ),
      ),
      panel({ title: t('generateImage.preview'), className: 'preview-panel' },
        el('div', { class: 'panel-body' },
          selectionSummary,
          resultPanel,
        ),
      ),
    ),
    panel({ title: t('generateImage.recentImages') },
      el('div', { class: 'panel-body' },
        recentJobs.length ? el('div', { class: 'recent-strip' }, recentJobs.map(recentJobCard)) :
          emptyState(t('generateImage.noRecentJobs')),
      ),
    ),
  ];
}

export async function render() {
  const content = document.getElementById('content');
  mount(content, loadingState(t('common.loading')));

  try {
    const catalog = await loadCatalog();
    let customProviders = [];
    let providerLoadFailed = false;
    try {
      customProviders = await loadProviders();
    } catch (_) {
      providerLoadFailed = true;
    }
    const recentJobs = await loadRecentImageJobs();
    mount(content, buildPage(catalog, customProviders, recentJobs, providerLoadFailed));
  } catch (error) {
    mount(content,
      pageHeader({ kicker: t('generateImage.kicker'), title: t('generateImage.title'), subtitle: t('generateImage.subtitle') }),
      errorState(t('generateImage.catalogError'), safeErrorMessage(error, t('generateImage.catalogError'))),
    );
  }
}

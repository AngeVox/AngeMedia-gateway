import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import { pageHeader, panel } from '../../components/page.js';
import { applyAssistantPlanPrefill, openAssistantPlanner } from '../../components/assistant-planner.js?v=web-studio-2h';
import { openPromptCopilot } from '../../components/prompt-copilot.js?v=web-studio-2h';
import { errorState, loadingState } from '../../components/states.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { navigate } from '../../router.js';
import {
  buildCatalogState,
  catalogProviderValue,
  loadCatalog,
  loadProviders,
  providerOptions,
} from './catalog-state.js';
import { createOperationControls } from './operation-controls.js?v=web-studio-2h';
import { createProviderModelControls, providerHelpKeyForMode } from './provider-model-controls.js';
import { buildGenerationPayload } from './payload.js';
import { loadImageReferenceAssets } from './reference-assets.js';
import {
  renderResultEmpty,
  renderResultError,
  renderResultLoading,
  renderResultQueued,
} from './result-preview.js';
import { loadRecentImageJobs, recentImagesPanel } from './recent-jobs.js';

function normalizedModelHint(value) {
  return String(value || '').trim().toLowerCase();
}

function modelMatchesHint(model, hint) {
  const normalized = normalizedModelHint(hint);
  if (!normalized || !model) return false;
  const candidates = [
    model.id,
    model.provider_model,
    ...(Array.isArray(model.aliases) ? model.aliases : []),
  ].map(normalizedModelHint).filter(Boolean);
  return candidates.some((candidate) => (
    candidate === normalized ||
    candidate.endsWith(`/${normalized}`) ||
    normalized.endsWith(`/${candidate}`)
  ));
}

function buildPage(catalog, customProviders, recentJobs, referenceAssets, providerLoadFailed) {
  const { catalogModels, catalogProviders } = buildCatalogState(catalog, customProviders);
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
  const operationControlsTarget = el('div', { class: 'form-stack', hidden: true, dataset: { operationControlsTarget: 'true' } });
  const submit = button(t('generateImage.submit'), { variant: 'primary' });
  const modelSelectField = field(t('generateImage.model'), modelSelect);
  const modelInputField = field(t('generateImage.routeModel'), modelInput);
  const customSizeField = field(t('generateImage.customSize'), customSizeInput);

  const controls = createProviderModelControls({
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
    customSizeField,
    sizeCapabilityWarning,
    selectionSummary,
  });
  const operationControls = createOperationControls({ target: operationControlsTarget, referenceAssets });

  function currentOperationModel() {
    return controls.currentCatalogProviderId() ? controls.currentCatalogModel() : null;
  }

  function syncOperationControls() {
    operationControls.sync(currentOperationModel());
  }

  function syncProviderStatus() {
    const mode = controls.currentProviderMode();
    const showProviderLoadFailure = providerLoadFailed && mode !== 'catalog';
    providerStatus.className = showProviderLoadFailure ? 'error-text' : 'field-help';
    providerStatus.textContent = t(providerHelpKeyForMode(mode, showProviderLoadFailure));
  }

  function syncModeDependentControls() {
    syncOperationControls();
    syncProviderStatus();
  }

  function applySizeSuggestion(size) {
    const value = String(size || '').trim();
    if (!/^[1-9]\d{2,3}x[1-9]\d{2,3}$/i.test(value)) return;
    const hasPreset = Array.from(sizeSelect.options).some((item) => item.value === value);
    sizeSelect.value = hasPreset ? value : 'custom';
    customSizeInput.value = value;
    controls.syncSizeFields();
  }

  function applyModelSuggestion(result) {
    const route = result?.route || {};
    const modelHint = route.model || result?.model_hint || result?.recommended_model;
    const providerHint = route.provider || result?.provider;
    const model = catalogModels.find((item) => (
      (!providerHint || item.provider_id === providerHint) &&
      modelMatchesHint(item, modelHint)
    )) || (providerHint ? catalogModels.find((item) => item.provider_id === providerHint) : null);
    if (!model) return false;
    providerSelect.value = catalogProviderValue(model.provider_id);
    controls.syncModelOptions(true);
    modelSelect.value = model.id;
    controls.handleModelChange();
    syncModeDependentControls();
    return true;
  }

  function applyPromptCopilotResult(result) {
    const appliedModel = applyModelSuggestion(result);
    applySizeSuggestion(result?.suggested_params?.size || result?.route?.size);
    return appliedModel;
  }

  function openImagePromptCopilot() {
    openPromptCopilot({ promptInput, mediaType: 'image', onApply: applyPromptCopilotResult });
  }

  function openImageAssistantPlanner() {
    openAssistantPlanner({ promptInput, mediaType: 'image', currentPage: 'generate-image' });
  }

  async function submitGeneration() {
    const built = buildGenerationPayload({
      promptInput,
      sizeSelect,
      customSizeInput,
      providerSelect,
      modelInput,
      operationValues: operationControls.values(),
      currentCatalogProviderId: controls.currentCatalogProviderId,
      currentCatalogModel: controls.currentCatalogModel,
      currentCustomProvider: controls.currentCustomProvider,
    });
    if (!built) return;

    submit.disabled = true;
    submit.textContent = t('generateImage.generating');
    try {
      const uploadedPath = await operationControls.prepare();
      if (uploadedPath) {
        built.payload.image = uploadedPath;
      }
    } catch (_) {
      submit.disabled = false;
      submit.textContent = t('generateImage.submit');
      return;
    }
    renderResultLoading(resultPanel);
    try {
      const result = await api.post('/admin/jobs/images', built.payload);
      renderResultQueued(resultPanel, result, built.prompt);
    } catch (error) {
      renderResultError(resultPanel, error);
    } finally {
      submit.disabled = false;
      submit.textContent = t('generateImage.submit');
    }
  }

  providerSelect.addEventListener('change', () => {
    controls.syncModelOptions(true);
    syncModeDependentControls();
  });
  modelSelect.addEventListener('change', () => {
    controls.handleModelChange();
    syncModeDependentControls();
  });
  sizeSelect.addEventListener('change', controls.syncSizeFields);
  submit.addEventListener('click', submitGeneration);
  controls.syncModelOptions(true);
  syncModeDependentControls();
  renderResultEmpty(resultPanel);
  applyAssistantPlanPrefill(promptInput, 'image');

  return [
    pageHeader({
      kicker: t('generateImage.kicker'),
      title: t('generateImage.title'),
      subtitle: t('generateImage.subtitle'),
      actions: [
        button(t('generateImage.videoWipAction'), { onClick: () => navigate('#/generate/video') }),
        button(t('generateImage.promptCopilotAction'), {
          onClick: openImagePromptCopilot,
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
            customSizeField,
          ),
          sizeCapabilityWarning,
          operationControlsTarget,
          el('div', { class: 'hint-box' }, el('span', {}, 'i'), providerStatus),
          el('div', { class: 'action-row creator-actions' },
            button(t('generateImage.promptCopilotAction'), {
              onClick: openImagePromptCopilot,
            }),
            button(t('generateImage.routeAdviceAction'), {
              onClick: openImageAssistantPlanner,
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
    recentImagesPanel(recentJobs),
  ];
}

export async function render() {
  const content = document.getElementById('content');
  mount(content, loadingState(t('common.loading')));

  try {
    const catalog = await loadCatalog();
    let customProviders = [];
    let providerLoadFailed = false;
    let referenceAssets = [];
    try {
      customProviders = await loadProviders();
    } catch (_) {
      providerLoadFailed = true;
    }
    try {
      referenceAssets = await loadImageReferenceAssets();
    } catch (_) {
      referenceAssets = [];
    }
    const recentJobs = await loadRecentImageJobs();
    mount(content, buildPage(catalog, customProviders, recentJobs, referenceAssets, providerLoadFailed));
  } catch (error) {
    mount(content,
      pageHeader({ kicker: t('generateImage.kicker'), title: t('generateImage.title'), subtitle: t('generateImage.subtitle') }),
      errorState(t('generateImage.catalogError'), safeErrorMessage(error, t('generateImage.catalogError'))),
    );
  }
}

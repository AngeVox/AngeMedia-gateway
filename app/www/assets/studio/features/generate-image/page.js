import { defaultImageRouteReady, providerAvailability } from '../../lib/capabilities.js';
import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import { pageHeader, panel } from '../../components/page.js';
import { applyAssistantPlanPrefill, openAssistantPlanner } from '../../components/assistant-planner.js?v=web-studio-2j';
import { openPromptCopilot } from '../../components/prompt-copilot.js?v=web-studio-2j';
import { errorState, loadingState } from '../../components/states.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { navigate, createRenderScope, createPendingScope, bindViewResources } from '../../router.js';
import { captureSessionScope } from '../../auth.js';
import { prepareConnectionEvidence } from '../providers/connection-evidence.js';
import {
  buildCatalogState,
  catalogProviderValue,
  customProviderOperationModel,
  loadCatalog,
  loadProviders,
  providerOptions,
} from './catalog-state.js';
import { operationSupportsSize } from './operation-capabilities.js';
import { createOperationControls } from './operation-controls.js?v=web-studio-2j';
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
import { filterAccountImageModels, MODELSCOPE_PROVIDER_ID, modelScopeDirectoryMessage } from './modelscope-directory.js';

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

function buildPage(catalog, customProviders, recentJobs, referenceAssets, providerLoadFailed, isViewActive) {
  let submitting = false;
  const { catalogModels, catalogProviders } = buildCatalogState(catalog, customProviders);
  const modelScopeProvider = catalogProviders.find(provider => provider.id === MODELSCOPE_PROVIDER_ID);
  const savedDirectory = modelScopeProvider?.runtime?.model_directory;
  let modelDirectory = savedDirectory ? prepareConnectionEvidence(savedDirectory) : null;
  let directoryRefreshing = false;
  let directoryExpiry = null;
  const filterCatalogModels = (models) => filterAccountImageModels(models, modelDirectory);
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
  const refreshDirectory = button(t('generateImage.modelScopeRefresh'), {
    size: 'sm', variant: 'secondary', onClick: refreshModelDirectory,
  });
  refreshDirectory.hidden = true;
  const sizeCapabilityWarning = el('p', { class: 'field-help' }, t('generateImage.sizeCapabilityUnknown'));
  const operationControlsTarget = el('div', { class: 'form-stack', hidden: true, dataset: { operationControlsTarget: 'true' } });
  const submit = button(t('generateImage.submit'), { variant: 'primary' });
  const modelSelectField = field(t('generateImage.model'), modelSelect);
  const modelInputField = field(t('generateImage.routeModel'), modelInput);
  const sizeSelectField = field(t('generateImage.size'), sizeSelect);
  const customSizeField = field(t('generateImage.customSize'), customSizeInput);

  const controls = createProviderModelControls({
    catalogModels,
    filterCatalogModels,
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
  function currentOperationModel() {
    if (controls.currentCatalogProviderId()) return controls.currentCatalogModel();
    return customProviderOperationModel(controls.currentCustomProvider());
  }

  function syncOperationSizeVisibility(operationName) {
    const model = currentOperationModel();
    const visible = !model || operationSupportsSize(model, operationName);
    sizeSelectField.hidden = !visible;
    if (!visible) {
      customSizeField.hidden = true;
      sizeCapabilityWarning.hidden = true;
      return;
    }
    controls.syncSizeFields();
    sizeCapabilityWarning.hidden = Boolean(model?.size_presets?.length);
  }

  const operationControls = createOperationControls({
    target: operationControlsTarget,
    referenceAssets,
    onOperationChange: (name) => {
      syncOperationSizeVisibility(name);
      syncSubmitLabel();
    },
  });

  function syncOperationControls() {
    const model = currentOperationModel();
    if (!model) {
      sizeSelectField.hidden = false;
      controls.syncSizeFields();
    }
    operationControls.sync(model);
  }

  function canSubmitSelection() {
    if (controls.currentCatalogProviderId()) return Boolean(controls.currentCatalogModel());
    if (providerSelect.value) return providerAvailability(controls.currentCustomProvider()).selectable;
    return Boolean(modelInput.value.trim()) || defaultImageRouteReady(catalog);
  }

  function syncSubmitLabel() {
    if (!submitting) submit.textContent = t(operationControls.values().operation === 'edit'
      ? 'generateImage.operationEdit' : 'generateImage.submit');
  }

  function syncProviderStatus() {
    const mode = controls.currentProviderMode();
    const showProviderLoadFailure = providerLoadFailed && mode !== 'catalog';
    providerStatus.className = showProviderLoadFailure ? 'error-text' : 'field-help';
    const ready = canSubmitSelection();
    providerStatus.textContent = showProviderLoadFailure ? t(providerHelpKeyForMode(mode, true))
      : ready ? '' : t('availability.missingConfiguration');
    const showDirectory = modelDirectory && controls.currentCatalogProviderId() === MODELSCOPE_PROVIDER_ID;
    refreshDirectory.hidden = !showDirectory;
    refreshDirectory.disabled = directoryRefreshing || !providerAvailability(modelScopeProvider).selectable;
    refreshDirectory.textContent = t(directoryRefreshing ? 'providers.builtinTesting' : 'generateImage.modelScopeRefresh');
    if (showDirectory && providerAvailability(modelScopeProvider).selectable) {
      providerStatus.textContent = t(modelScopeDirectoryMessage(modelDirectory, ready));
    }
    providerStatus.hidden = !providerStatus.textContent;
    submit.disabled = submitting || !ready;
    syncSubmitLabel();
  }

  function syncDirectoryExpiry() {
    clearTimeout(directoryExpiry);
    const remaining = modelDirectory?.deadline - Date.now();
    if (controls.currentCatalogProviderId() !== MODELSCOPE_PROVIDER_ID || !(remaining > 0)) return;
    directoryExpiry = setTimeout(() => {
      if (!resources.isActive()) return;
      controls.syncModelOptions();
      syncModeDependentControls();
    }, remaining + 1);
  }

  async function refreshModelDirectory() {
    if (!resources.isActive() || directoryRefreshing || refreshDirectory.disabled) return;
    const session = captureSessionScope();
    const scope = createPendingScope(() => resources.isActive() && session());
    directoryRefreshing = true;
    syncProviderStatus();
    try {
      const result = await api.post('/admin/provider-configs/modelscope/test', {});
      if (scope.isActive()) modelDirectory = prepareConnectionEvidence(result?.data);
    } catch (error) {
      if (scope.isActive()) modelDirectory = prepareConnectionEvidence({ status: 'failed' });
    } finally {
      directoryRefreshing = false;
      if (scope.isActive()) {
        controls.syncModelOptions();
        syncModeDependentControls();
        syncDirectoryExpiry();
      }
      scope.close();
    }
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
    const eligible = filterCatalogModels(catalogModels).filter((item) => providerAvailability(catalogProviders.find((p) => p.id === item.provider_id)).selectable);
    const model = eligible.find((item) => (
      (!providerHint || item.provider_id === providerHint) &&
      modelMatchesHint(item, modelHint)
    )) || (providerHint ? eligible.find((item) => item.provider_id === providerHint) : null);
    if (!model) return false;
    providerSelect.value = catalogProviderValue(model.provider_id);
    controls.syncModelOptions(true);
    modelSelect.value = model.id;
    controls.handleModelChange();
    syncModeDependentControls();
    syncDirectoryExpiry();
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
    if (submitting || !resources.isActive() || !canSubmitSelection()) return;
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

    submitting = true; submit.disabled = true;
    submit.textContent = t('generateImage.generating');
    const isSessionCurrent = captureSessionScope();
    const scope = createPendingScope(() => resources.isActive() && isSessionCurrent());
    try {
      const prepared = await operationControls.prepare(scope.isActive);
      if (!scope.isActive()) return;
      if (prepared?.image) built.payload.image = prepared.image;
      if (Array.isArray(prepared?.reference_images) && prepared.reference_images.length) {
        built.payload.reference_images = [
          ...(Array.isArray(built.payload.reference_images) ? built.payload.reference_images : []),
          ...prepared.reference_images,
        ];
      }
      if (prepared?.mask) built.payload.mask = prepared.mask;
      if (prepared?.markup) built.payload.markup = prepared.markup;
      renderResultLoading(resultPanel);
      const result = await api.post('/admin/jobs/images', built.payload);
      if (scope.isActive()) renderResultQueued(resultPanel, result, built.prompt);
    } catch (error) {
      if (scope.isActive()) renderResultError(resultPanel, error);
    } finally {
      const active = resources.isActive() && isSessionCurrent();
      scope.close();
      submitting = false;
      if (active) {
        submit.disabled = !canSubmitSelection();
        syncSubmitLabel();
      }
    }
  }

  providerSelect.addEventListener('change', () => {
    controls.syncModelOptions(true);
    syncModeDependentControls();
    syncDirectoryExpiry();
  });
  modelSelect.addEventListener('change', () => {
    controls.handleModelChange();
    syncModeDependentControls();
  });
  modelInput.addEventListener('input', syncProviderStatus);
  sizeSelect.addEventListener('change', controls.syncSizeFields);
  submit.addEventListener('click', submitGeneration);
  controls.syncModelOptions(true);
  syncModeDependentControls();
  renderResultEmpty(resultPanel);
  applyAssistantPlanPrefill(promptInput, 'image');

  const resources = bindViewResources(() => isViewActive() && operationControlsTarget.isConnected && resultPanel.isConnected, {
    dispose: () => {
      clearTimeout(directoryExpiry);
      operationControls.dispose();
    },
    suspend: () => {
      clearTimeout(directoryExpiry);
      operationControls.suspend();
      if (submitting) renderResultEmpty(resultPanel);
    },
    resume: () => {
      operationControls.resume();
      if (controls.currentCatalogProviderId() === MODELSCOPE_PROVIDER_ID) {
        controls.syncModelOptions();
        syncModeDependentControls();
        syncDirectoryExpiry();
      } else {
        syncProviderStatus();
      }
      syncSubmitLabel();
    },
  });

  return [
    pageHeader({
      title: t('generateImage.title'),
      actions: [
        button(t('generateImage.videoWipAction'), { onClick: () => navigate('#/generate/video') }),
      ],
    }),
    el('div', { class: 'generate-grid' },
      panel({ className: 'creator-panel' },
        el('div', { class: 'panel-body form-stack' },
          field(t('generateImage.prompt'), promptInput, { className: 'span-2' }),
          el('div', { class: 'form-grid' },
            field(t('generateImage.provider'), providerSelect),
            modelSelectField,
            modelInputField,
            sizeSelectField,
            customSizeField,
          ),
          sizeCapabilityWarning,
          operationControlsTarget,
          providerStatus,
          refreshDirectory,
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
  const scope = createRenderScope(content);
  mount(content, loadingState(t('common.loading')));

  try {
    const catalog = await loadCatalog();
    if (!scope.isActive()) return;
    let customProviders = [];
    let providerLoadFailed = false;
    let referenceAssets = [];
    try {
      customProviders = await loadProviders();
    } catch (_) {
      providerLoadFailed = true;
    }
    if (!scope.isActive()) return;
    try {
      referenceAssets = await loadImageReferenceAssets();
    } catch (_) {
      referenceAssets = [];
    }
    if (!scope.isActive()) return;
    const recentJobs = await loadRecentImageJobs();
    if (!scope.isActive()) return;
    mount(content, buildPage(catalog, customProviders, recentJobs, referenceAssets, providerLoadFailed, scope.isActive));
  } catch (error) {
    if (!scope.isActive()) return;
    mount(content,
      pageHeader({ kicker: t('generateImage.kicker'), title: t('generateImage.title'), subtitle: t('generateImage.subtitle') }),
      errorState(t('generateImage.catalogError'), safeErrorMessage(error, t('generateImage.catalogError'))),
    );
  }
}

import { providerAvailability } from '../../lib/capabilities.js';
import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { badge, statusBadge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import { pageHeader, panel, metaGrid } from '../../components/page.js';
import { applyAssistantPlanPrefill } from '../../components/assistant-planner.js?v=web-studio-2j';
import { startJobResultTracker, stopJobResultTracker } from '../../components/job-result-tracker.js?v=web-studio-2j';
import { openPromptCopilot } from '../../components/prompt-copilot.js?v=web-studio-2j';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { errorDiagnostics, safeErrorMessage } from '../../lib/safe-error.js';
import { formatDuration, truncateText } from '../../lib/format.js';
import { parseSizePreset, selectableVideoModels, videoProvidersForModels } from '../../lib/capabilities.js';
import { safeText } from '../../lib/security.js';
import { isLegacyModel } from '../../components/model-policy.js';
import { displayJobErrorCategory, displayJobProviderStatus, displayJobStage } from '../../lib/job-display.js';
import { navigate, createRenderScope, createPendingScope, bindViewResources } from '../../router.js';
import { captureSessionScope } from '../../auth.js';
import { loadImageReferenceAssets } from '../generate-image/reference-assets.js';
import { createReferenceUpload } from '../generate-image/reference-upload.js';
import { createVideoMediaInputs } from './media-inputs.js';
import { applyVideoReferences, publicReferenceSpec, supportsReferenceImage, VideoReferenceInputError } from './reference-payload.js';

function option(label, value, disabled = false) {
  return { label, value, disabled };
}

function modelProvider(providers, model) {
  return providers.find((provider) => provider.id === model?.provider_id) || null;
}

function providerLabel(provider) {
  return provider?.display_name || provider?.id || '-';
}

function modelLabel(model) {
  return model.display_name || model.id;
}

function humanizeParam(key) {
  return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

function paramLabel(key) {
  const lookup = `generateVideo.param.${key}`;
  const translated = t(lookup);
  return translated === lookup ? humanizeParam(key) : translated;
}

function modelById(models, id) {
  return models.find((model) => model.id === id) || null;
}

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

function modelsForProvider(models, providerId) {
  return models.filter((model) => !providerId || model.provider_id === providerId);
}

function replaceOptions(node, items) {
  node.textContent = '';
  items.forEach((item) => {
    node.appendChild(el('option', { value: item.value, disabled: item.disabled }, item.label));
  });
}

function sizeOptions(model) {
  const presets = Array.isArray(model?.size_presets) ? model.size_presets : [];
  return [
    ...presets.map((preset) => option(preset, preset)),
    option(t('generateVideo.customSize'), 'custom'),
  ];
}

function numericInputAttrs(type) {
  const normalized = String(type || '').toLowerCase();
  if (normalized === 'integer') return { type: 'number', step: '1' };
  if (normalized === 'number' || normalized === 'float') return { type: 'number', step: 'any' };
  return { type: 'text' };
}

function paramValue(value, type) {
  const text = String(value || '').trim();
  if (!text) return undefined;
  const normalized = String(type || '').toLowerCase();
  if (['integer', 'number', 'float'].includes(normalized)) {
    const numberValue = Number(text);
    return Number.isFinite(numberValue) ? numberValue : undefined;
  }
  return text;
}

function videoParamSpec(model, key) {
  const specs = model?.param_specs && typeof model.param_specs === 'object' ? model.param_specs : {};
  return specs[key] && typeof specs[key] === 'object' ? specs[key] : null;
}

function createVideoParamControl(key, type, spec) {
  if (spec?.kind === 'enum' && Array.isArray(spec.enum_values)) {
    const control = select([
      option('-', ''),
      ...spec.enum_values.map((value) => option(
        key === 'mode' && ['text', 'keyframe', 'reference'].includes(value)
          ? t(`generateVideo.mode.${value}`) : String(value), String(value),
      )),
    ], { name: key });
    if (spec.default !== null && spec.default !== undefined) control.value = String(spec.default);
    return control;
  }
  const control = input({
    name: key,
    autocomplete: 'off',
    placeholder: t('generateImage.paramAuto'),
    ...numericInputAttrs(spec?.kind || type),
  });
  if (spec?.default !== null && spec?.default !== undefined) control.value = String(spec.default);
  return control;
}

function usesDimensionSize(model) {
  const params = model?.params && typeof model.params === 'object' ? model.params : {};
  return Object.hasOwn(params, 'width') || Object.hasOwn(params, 'height');
}

function capabilityBadges(model) {
  const capabilities = model?.capabilities && typeof model.capabilities === 'object' ? model.capabilities : {};
  const enabled = Object.entries(capabilities).filter(([, value]) => value === true);
  if (!enabled.length) return emptyState(t('generateVideo.noCapabilities'));
  return el('div', { class: 'video-capability-list' },
    enabled.map(([key]) => {
      const labelKey = `jobDisplay.inputModes.${key}`;
      const translated = t(labelKey);
      return badge(translated === labelKey ? humanizeParam(key) : translated, 'info');
    }),
  );
}

function renderModelSummary(target, model) {
  if (!model) {
    mount(target, emptyState(t('generateVideo.modelEmpty')));
    return;
  }
  mount(target,
    el('div', { class: 'video-model-summary' },
      el('p', { class: 'card-title' }, safeText(model.display_name || model.id, 80)),
      capabilityBadges(model),
      isLegacyModel(model.policy) ? el('p', { class: 'field-help' }, t('modelPolicy.legacyWarning')) : null,
    ),
  );
}

function renderResultEmpty(target) {
  stopJobResultTracker(target);
  mount(target, emptyState(t('generateVideo.resultEmptyTitle'), t('generateVideo.resultEmptyCopy')));
}

function renderResultLoading(target) {
  stopJobResultTracker(target);
  mount(target, loadingState(t('generateVideo.submitting')));
}

function renderResultSuccess(target, result, model) {
  stopJobResultTracker(target);
  const status = result?.status || 'submitted';
  mount(target,
    el('div', { class: 'result-success' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateVideo.submitSuccess')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(result?.task_id || result?.id || result?.job_id || '', 160), 88)),
        ),
        statusBadge(status, displayJobProviderStatus(status)),
      ),
      metaGrid([
        { label: t('generateVideo.jobId'), value: result?.job_id },
        { label: t('generateVideo.taskId'), value: result?.task_id || result?.id },
        { label: t('generateVideo.status'), value: displayJobProviderStatus(status) },
        { label: t('generateVideo.provider'), value: safeText(result?.provider, 80) },
        { label: t('generateVideo.model'), value: safeText(result?.model || model?.provider_model, 96) },
        { label: t('generateVideo.historyId'), value: result?.history_id },
        { label: t('generateVideo.duration'), value: result?.duration_ms ? formatDuration(result.duration_ms) : '' },
      ]),
      result?.job_id ? el('p', { class: 'card-subtitle video-async-job-help' }, t('generateVideo.asyncJobHelp')) : null,
      el('div', { class: 'action-row creator-actions' },
        button(t('generateVideo.viewJobs'), { onClick: () => navigate('#/jobs') }),
        result?.job_id ? button(t('jobs.detail'), {
          onClick: () => {
            sessionStorage.setItem('studio_open_job_id', result.job_id);
            navigate('#/jobs');
          },
        }) : null,
        button(t('generateVideo.viewAssets'), { onClick: () => navigate('#/assets') }),
      ),
    ),
  );
}

function renderResultError(target, error) {
  stopJobResultTracker(target);
  const diagnostics = errorDiagnostics(error);
  const message = error instanceof VideoReferenceInputError ? error.message
    : safeErrorMessage(error, t('generateVideo.error'));
  mount(target,
    el('div', { class: 'diagnostic-card' },
      el('p', { class: 'card-title' }, message),
      metaGrid([
        { label: t('jobs.errorCategory'), value: displayJobErrorCategory(diagnostics.error_category) },
        { label: t('jobs.gatewayStage'), value: displayJobStage(diagnostics.gateway_stage) },
        { label: t('jobs.retryable'), value: diagnostics.retryable === true ? t('jobs.retryable') : diagnostics.retryable === false ? t('jobs.notRetryable') : '' },
        { label: t('generateVideo.jobId'), value: diagnostics.existing_job?.job_id },
      ]),
    ),
  );
}

async function loadCatalog() {
  return api.get('/admin/catalog');
}

function buildPage(catalog, referenceAssets, isViewActive) {
  let submitting = false;
  const allModels = selectableVideoModels(catalog);
  const providers = videoProvidersForModels(catalog, allModels);
  if (!allModels.length) {
    return [
      pageHeader({
        kicker: t('generateVideo.kicker'),
        title: t('generateVideo.title'),
        subtitle: t('generateVideo.subtitle'),
      }),
      panel({ title: t('generateVideo.title') },
        el('div', { class: 'panel-body' }, emptyState(t('generateVideo.noModels'))),
      ),
    ];
  }

  const providerSelect = select(providers.map((provider) => {
    const { selectable, reasonKey } = providerAvailability(provider);
    const label = providerLabel(provider);
    return option(selectable ? label : `${label} · ${t(reasonKey)}`, provider.id, !selectable);
  }), { name: 'provider' });
  const releaseProviders = providers.filter((provider) =>
    modelsForProvider(allModels, provider.id).some((model) => model.status === 'release'));
  providerSelect.value = releaseProviders.find((provider) => providerAvailability(provider).selectable)?.id ||
    releaseProviders[0]?.id || '';
  const availabilityHint = el('p', { class: 'field-help', dataset: { providerAvailability: 'true' } });
  const modelSelect = select([], { name: 'model' });
  const sizeSelect = select([], { name: 'size' });
  const widthInput = input({ name: 'width', type: 'number', min: 256, max: 2048 });
  const heightInput = input({ name: 'height', type: 'number', min: 256, max: 1536 });
  const firstFrameUrlInput = input({ name: 'first_frame', type: 'url', autocomplete: 'off', placeholder: 'https://example.com/first.png' });
  const lastFrameUrlInput = input({ name: 'last_frame', type: 'url', autocomplete: 'off', placeholder: 'https://example.com/last.png' });
  const referenceUrlsInput = textarea({ name: 'reference_urls', rows: 4, autocomplete: 'off', placeholder: 'https://example.com/reference-1.png\nhttps://example.com/reference-2.png' });
  const promptInput = textarea({
    name: 'prompt',
    maxLength: 32000,
    placeholder: t('generateVideo.promptPlaceholder'),
  });
  const paramsTarget = el('div', { class: 'form-grid video-param-grid' });
  const advancedParamsTarget = el('div', { class: 'form-grid video-param-grid' });
  const advancedParams = el('details', { class: 'list-disclosure', dataset: { creationAdvanced: 'true' } },
    el('summary', {}, t('creation.advanced')),
    el('p', { class: 'field-help' }, t('creation.advancedHelp')),
    advancedParamsTarget,
  );
  const refInputsTarget = el('div', { class: 'form-stack' });
  const referenceSection = el('details', { class: 'list-disclosure', dataset: { creationReferences: 'true' } },
    el('summary', {}, t('creation.references')),
    el('p', { class: 'field-help' }, t('generateVideo.refInputsSubtitle')),
    refInputsTarget,
  );
  const mediaInputs = createVideoMediaInputs();
  const referenceUploadTarget = el('div');
  const referenceUpload = createReferenceUpload({ target: referenceUploadTarget });
  const publicReferenceUploadTarget = el('div');
  const publicReferenceUpload = createReferenceUpload({ target: publicReferenceUploadTarget, multiple: true, maxFiles: 8 });
  const publicReferenceAssetSelect = select([
    ...referenceAssets.map((asset) => option(asset.label, asset.value)),
  ], { name: 'public_reference_assets', multiple: true, size: Math.min(6, Math.max(2, referenceAssets.length || 2)) });
  const referenceAssetSelect = select([
    option(t('generateVideo.referenceAssetNone'), ''),
    ...referenceAssets.map((asset) => option(asset.label, asset.value)),
  ], { name: 'reference_asset' });
  const referencePreviewImage = el('img', { class: 'ref-upload-thumb', alt: '' });
  const referencePreviewName = el('span', { class: 'ref-upload-info' });
  const referencePreview = el('div', { class: 'ref-upload-preview', hidden: true },
    referencePreviewImage,
    referencePreviewName,
  );
  const referenceClear = button(t('generateVideo.referenceClear'), { variant: 'secondary' });
  const modelSummary = el('div', { class: 'video-summary-frame' });
  const resultPanel = el('div', { class: 'result-frame' });
  const submit = button(t('generateVideo.submit'), { variant: 'primary' });
  const sizePresetField = field(t('generateVideo.sizePreset'), sizeSelect);
  const widthField = field(t('generateVideo.width'), widthInput);
  const heightField = field(t('generateVideo.height'), heightInput);
  let paramInputs = {};

  function syncReferencePreview() {
    const selected = referenceAssets.find((asset) => asset.value === referenceAssetSelect.value);
    referencePreview.hidden = !selected;
    const href = api.assetUrl(selected?.value);
    referencePreviewImage.hidden = !href;
    if (href) referencePreviewImage.src = href;
    else referencePreviewImage.removeAttribute('src');
    referencePreviewName.textContent = selected?.label || '';
  }

  function clearReference() {
    referenceUpload.clear();
    publicReferenceUpload.clear();
    referenceAssetSelect.value = '';
    Array.from(publicReferenceAssetSelect.options).forEach((item) => { item.selected = false; });
    firstFrameUrlInput.value = '';
    lastFrameUrlInput.value = '';
    referenceUrlsInput.value = '';
    mediaInputs.clear();
    mount(refInputsTarget, renderReferenceInputs(currentModel()));
    syncReferencePreview();
  }

  function selectedPublicReferenceAssets() {
    return Array.from(publicReferenceAssetSelect.selectedOptions || [])
      .map((item) => String(item.value || '').trim())
      .filter(Boolean);
  }

  function renderReferenceInputs(model) {
    if (!supportsReferenceImage(model)) return el('div', { class: 'form-stack' }, mediaInputs.render(model), referenceClear);
    const publicSpec = publicReferenceSpec(model);
    if (publicSpec) {
      const nodes = [
        field(t('generateVideo.referenceUpload'), publicReferenceUploadTarget, {
          help: t('generateVideo.relayLocalReferenceHelp'),
        }),
        field(t('generateVideo.referenceAssets'), publicReferenceAssetSelect, {
          help: t('generateVideo.relayAssetReferenceHelp'),
        }),
      ];
      if (publicSpec.roles.includes('first_frame')) {
        nodes.push(field(t('generateVideo.firstFrameUrl'), firstFrameUrlInput, { help: t('generateVideo.publicReferenceHelp') }));
      }
      if (publicSpec.roles.includes('last_frame')) {
        nodes.push(field(t('generateVideo.lastFrameUrl'), lastFrameUrlInput, { help: t('generateVideo.publicReferenceHelp') }));
      }
      if (publicSpec.roles.includes('images')) {
        nodes.push(field(t('generateVideo.referenceUrls'), referenceUrlsInput, {
          help: t('generateVideo.referenceUrlsHelp').replace('{count}', String(publicSpec.maxTotal)),
        }));
      }
      nodes.push(mediaInputs.render(model));
      nodes.push(el('div', { class: 'action-row video-reference-actions' }, referenceClear));
      return el('div', { class: 'form-stack compact-stack video-reference-controls' }, nodes);
    }
    return el('div', { class: 'form-stack compact-stack video-reference-controls' },
      field(t('generateVideo.referenceUpload'), referenceUploadTarget, {
        help: t('generateVideo.referenceUploadHelp'),
      }),
      field(t('generateVideo.referenceAsset'), referenceAssetSelect, {
        help: t('generateVideo.referenceAssetHelp'),
      }),
      referencePreview,
      mediaInputs.render(model),
      el('div', { class: 'action-row video-reference-actions' }, referenceClear),
    );
  }

  function currentModel() {
    const model = modelById(allModels, modelSelect.value);
    return model?.provider_id === providerSelect.value && providerAvailability(modelProvider(providers, model)).selectable ? model : null;
  }

  function syncModelOptions() {
    const provider = providers.find((item) => item.id === providerSelect.value);
    const state = providerAvailability(provider);
    availabilityHint.hidden = state.selectable;
    availabilityHint.textContent = state.selectable ? '' : t(state.reasonKey);
    submit.disabled = submitting || !state.selectable;
    const filtered = state.selectable ? modelsForProvider(allModels, providerSelect.value) : [];
    replaceOptions(modelSelect, filtered.map((model) => option(modelLabel(model), model.id)));
    if (!modelById(filtered, modelSelect.value) && filtered[0]) {
      modelSelect.value = filtered[0].id;
    }
    syncModelMetadata();
  }

  function syncSizeFields() {
    const selected = sizeSelect.value;
    const parsed = parseSizePreset(selected);
    const custom = selected === 'custom' || !parsed;
    widthInput.disabled = !custom;
    heightInput.disabled = !custom;
    if (parsed) {
      widthInput.value = String(parsed.width);
      heightInput.value = String(parsed.height);
    }
  }

  function syncReferenceSection() {
    const mode = paramInputs.mode?.control.value;
    if (mode === 'keyframe' || mode === 'reference' || currentModel()?.ref_input_spec?.required === true) {
      referenceSection.open = true;
    }
  }

  function syncModelMetadata() {
    const model = currentModel();
    submit.disabled = submitting || !model;
    const dimensionSize = usesDimensionSize(model);
    sizePresetField.hidden = !dimensionSize;
    widthField.hidden = !dimensionSize;
    heightField.hidden = !dimensionSize;
    if (dimensionSize) {
      replaceOptions(sizeSelect, sizeOptions(model));
      sizeSelect.value = (model?.size_presets || [])[0] || 'custom';
      syncSizeFields();
    }

    const params = model?.params && typeof model.params === 'object' ? model.params : {};
    paramInputs = {};
    const primaryFields = [];
    const advancedFields = [];
    Object.entries(params)
        .filter(([key]) => !['width', 'height'].includes(key))
        .forEach(([key, type]) => {
          const spec = videoParamSpec(model, key);
          const control = createVideoParamControl(key, type, spec);
          paramInputs[key] = { control, type: spec?.kind || type };
          if (key === 'mode') control.addEventListener('change', syncReferenceSection);
          const fields = key === 'seed' ? advancedFields : primaryFields;
          fields.push(field(paramLabel(key), control));
        });
    mount(paramsTarget, primaryFields);
    mount(advancedParamsTarget, advancedFields);
    advancedParams.hidden = !advancedFields.length;
    if (!Object.keys(paramInputs).length) {
      mount(paramsTarget, emptyState(t('generateVideo.noParams')));
    }

    mount(refInputsTarget, renderReferenceInputs(model));
    syncReferenceSection();
    renderModelSummary(modelSummary, model);
  }

  function applySizeSuggestion(size) {
    const value = String(size || '').trim();
    const parsed = parseSizePreset(value);
    if (!parsed) return;
    const hasPreset = Array.from(sizeSelect.options).some((item) => item.value === value);
    sizeSelect.value = hasPreset ? value : 'custom';
    widthInput.value = String(parsed.width);
    heightInput.value = String(parsed.height);
    syncSizeFields();
  }

  function applyModelSuggestion(result) {
    const route = result?.route || {};
    const modelHint = route.model || result?.model_hint || result?.recommended_model;
    const providerHint = route.provider || result?.provider;
    const eligible = allModels.filter((item) => providerAvailability(modelProvider(providers, item)).selectable);
    const model = eligible.find((item) => (
      (!providerHint || item.provider_id === providerHint) &&
      modelMatchesHint(item, modelHint)
    )) || (providerHint ? eligible.find((item) => item.provider_id === providerHint) : null);
    if (!model) return false;
    providerSelect.value = model.provider_id;
    syncModelOptions();
    modelSelect.value = model.id;
    syncModelMetadata();
    return true;
  }

  function applyPromptCopilotResult(result) {
    const appliedModel = applyModelSuggestion(result);
    applySizeSuggestion(result?.suggested_params?.size || result?.route?.size);
    return appliedModel;
  }

  function openVideoPromptCopilot() {
    openPromptCopilot({ promptInput, mediaType: 'video', onApply: applyPromptCopilotResult });
  }

  async function submitVideo() {
    if (submitting || !resources.isActive()) return;
    const prompt = promptInput.value.trim();
    const model = currentModel();
    if (!prompt) {
      toast(t('generateVideo.promptRequired'), 'error');
      promptInput.focus();
      return;
    }
    if (!model) {
      toast(t('generateVideo.modelRequired'), 'error');
      return;
    }

    const payload = {
      prompt,
      model: model.provider_model || model.id,
      wait_for_completion: false,
    };
    if (model.provider_id === 'pollinations') payload.provider = model.provider_id;
    if (usesDimensionSize(model)) {
      const width = Number(widthInput.value);
      const height = Number(heightInput.value);
      if (!Number.isFinite(width) || !Number.isFinite(height)) {
        toast(t('generateVideo.sizeRequired'), 'error');
        return;
      }
      payload.width = width;
      payload.height = height;
    }

    Object.entries(paramInputs).forEach(([key, item]) => {
      const value = paramValue(item.control.value, item.type);
      if (value !== undefined) payload[key] = value;
    });

    submitting = true; submit.disabled = true;
    submit.textContent = t('generateVideo.submitting');
    const referenceValues = {
      asset: referenceAssetSelect.value,
      assets: selectedPublicReferenceAssets(),
      firstFrame: firstFrameUrlInput.value,
      lastFrame: lastFrameUrlInput.value,
      urls: referenceUrlsInput.value,
    };
    const mediaValues = mediaInputs.capture();
    const isSessionCurrent = captureSessionScope();
    const scope = createPendingScope(() => resources.isActive() && isSessionCurrent());
    const prepareReference = supportsReferenceImage(model)
      ? (publicReferenceSpec(model) ? publicReferenceUpload : referenceUpload).capture(scope.isActive)
      : async () => null;
    renderResultLoading(resultPanel);
    try {
      const uploaded = await prepareReference();
      if (!scope.isActive()) return;
      applyVideoReferences(payload, model, referenceValues, uploaded);
      mediaInputs.apply(payload, model, mediaValues);
      const result = await api.post('/admin/jobs/videos', payload);
      if (!scope.isActive()) return;
      renderResultSuccess(resultPanel, result, model);
      if (result?.job_id) {
        startJobResultTracker(resultPanel, {
          jobId: result.job_id,
          mediaType: 'video',
          prompt,
          initial: result,
        });
      }
    } catch (error) {
      if (scope.isActive()) renderResultError(resultPanel, error);
    } finally {
      const active = resources.isActive() && isSessionCurrent();
      scope.close();
      submitting = false;
      if (active) {
        submit.disabled = !currentModel();
        submit.textContent = t('generateVideo.submit');
      }
    }
  }

  providerSelect.addEventListener('change', syncModelOptions);
  modelSelect.addEventListener('change', syncModelMetadata);
  sizeSelect.addEventListener('change', syncSizeFields);
  referenceAssetSelect.addEventListener('change', syncReferencePreview);
  referenceClear.addEventListener('click', clearReference);
  submit.addEventListener('click', submitVideo);

  syncModelOptions();
  renderResultEmpty(resultPanel);
  applyAssistantPlanPrefill(promptInput, 'video');

  const uploads = [referenceUpload, publicReferenceUpload];
  const resources = bindViewResources(() => isViewActive() && referenceSection.isConnected && resultPanel.isConnected, {
    dispose: () => { uploads.forEach(upload => upload.dispose()); mediaInputs.clear(); },
    suspend: () => {
      uploads.forEach(upload => upload.suspend());
      if (submitting) renderResultEmpty(resultPanel);
    },
    resume: () => {
      uploads.forEach(upload => upload.resume());
      submit.disabled = submitting || !currentModel();
      if (!submitting) submit.textContent = t('generateVideo.submit');
    },
  });

  return [
    pageHeader({
      title: t('generateVideo.title'),
      actions: [
        button(t('generateVideo.viewJobs'), { onClick: () => navigate('#/jobs') }),
        button(t('generateVideo.viewAssets'), { onClick: () => navigate('#/assets') }),
      ],
    }),
    el('div', { class: 'generate-grid video-generate-grid' },
      panel({ className: 'creator-panel' },
        el('div', { class: 'panel-body form-stack' },
          field(t('generateVideo.prompt'), promptInput, { className: 'span-2' }),
          el('div', { class: 'form-grid' },
            field(t('generateVideo.provider'), providerSelect),
            availabilityHint,
            field(t('generateVideo.model'), modelSelect),
            sizePresetField,
            widthField,
            heightField,
          ),
          el('div', { class: 'form-subsection' },
            el('div', { class: 'form-subsection-header' }, t('generateVideo.params')),
            paramsTarget,
          ),
          referenceSection,
          advancedParams,
          el('div', { class: 'action-row creator-actions' },
            button(t('generateVideo.promptCopilotAction'), { onClick: openVideoPromptCopilot }),
            submit,
          ),
        ),
      ),
      panel({ title: t('generateVideo.preview'), className: 'preview-panel' },
        el('div', { class: 'panel-body' },
          modelSummary,
          resultPanel,
        ),
      ),
    ),
  ];
}

export async function render() {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  mount(content, loadingState(t('common.loading')));

  try {
    const [catalog, referenceAssets] = await Promise.all([
      loadCatalog(),
      loadImageReferenceAssets().catch(() => []),
    ]);
    if (!scope.isActive()) return;
    mount(content, buildPage(catalog, referenceAssets, scope.isActive));
  } catch (error) {
    if (!scope.isActive()) return;
    mount(content,
      pageHeader({ kicker: t('generateVideo.kicker'), title: t('generateVideo.title'), subtitle: t('generateVideo.subtitle') }),
      errorState(t('generateVideo.catalogError'), safeErrorMessage(error, t('generateVideo.catalogError'))),
    );
  }
}

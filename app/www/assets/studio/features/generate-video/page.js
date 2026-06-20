import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { badge, statusBadge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea } from '../../components/forms.js';
import { pageHeader, panel, metaGrid } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { errorDiagnostics, safeErrorMessage } from '../../lib/safe-error.js';
import { formatDuration, truncateText } from '../../lib/format.js';
import { parseSizePreset, selectableVideoModels, videoProvidersForModels } from '../../lib/capabilities.js';
import { safeText } from '../../lib/security.js';
import { navigate } from '../../router.js';
import { loadImageReferenceAssets } from '../generate-image/reference-assets.js';
import { createReferenceUpload } from '../generate-image/reference-upload.js';

function option(label, value, disabled = false) {
  return { label, value, disabled };
}

function modelProvider(providers, model) {
  return providers.find((provider) => provider.id === model?.provider_id) || null;
}

function providerLabel(provider) {
  return provider?.display_name || provider?.id || '-';
}

function modelLabel(providers, model) {
  const provider = modelProvider(providers, model);
  const prefix = provider ? `${providerLabel(provider)} / ` : '';
  return `${prefix}${model.display_name || model.id}`;
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

function capabilityBadges(model) {
  const capabilities = model?.capabilities && typeof model.capabilities === 'object' ? model.capabilities : {};
  const enabled = Object.entries(capabilities).filter(([, value]) => value === true);
  if (!enabled.length) return emptyState(t('generateVideo.noCapabilities'));
  return el('div', { class: 'video-capability-list' },
    enabled.map(([key]) => badge(humanizeParam(key), 'info')),
  );
}

function supportsReferenceImage(model) {
  const refInputs = model?.ref_inputs && typeof model.ref_inputs === 'object' ? model.ref_inputs : {};
  return model?.capabilities?.image_to_video === true || Object.hasOwn(refInputs, 'image');
}

function renderModelSummary(target, providers, model) {
  if (!model) {
    mount(target, emptyState(t('generateVideo.modelEmpty')));
    return;
  }
  const provider = modelProvider(providers, model);
  mount(target,
    el('div', { class: 'video-model-summary' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, safeText(model.display_name || model.id, 80)),
          el('p', { class: 'card-subtitle' }, safeText(providerLabel(provider), 80)),
        ),
        badge(safeText(model.status || '-', 24), model.status === 'release' ? 'success' : 'warning'),
      ),
      capabilityBadges(model),
      metaGrid([
        { label: t('generateVideo.provider'), value: providerLabel(provider) },
        { label: t('generateVideo.model'), value: safeText(model.provider_model || model.id, 96) },
        { label: t('generateVideo.size'), value: (model.size_presets || []).join(', ') },
        { label: t('generateVideo.tags'), value: (model.tags || []).join(', ') },
      ]),
    ),
  );
}

function renderResultEmpty(target) {
  mount(target, emptyState(t('generateVideo.resultEmptyTitle'), t('generateVideo.resultEmptyCopy')));
}

function renderResultLoading(target) {
  mount(target, loadingState(t('generateVideo.submitting')));
}

function renderResultSuccess(target, result, model) {
  const status = result?.status || 'submitted';
  mount(target,
    el('div', { class: 'result-success' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateVideo.submitSuccess')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(result?.task_id || result?.id || '', 160), 88)),
        ),
        statusBadge(status, status),
      ),
      metaGrid([
        { label: t('generateVideo.jobId'), value: result?.job_id },
        { label: t('generateVideo.taskId'), value: result?.task_id || result?.id },
        { label: t('generateVideo.status'), value: status },
        { label: t('generateVideo.provider'), value: safeText(result?.provider, 80) },
        { label: t('generateVideo.model'), value: safeText(result?.model || model?.provider_model, 96) },
        { label: t('generateVideo.historyId'), value: result?.history_id },
        { label: t('generateVideo.duration'), value: result?.duration_ms ? formatDuration(result.duration_ms) : '' },
      ]),
      result?.job_id ? el('p', { class: 'card-subtitle video-async-job-help' }, t('generateVideo.asyncJobHelp')) : null,
      el('div', { class: 'action-row creator-actions' },
        button(t('generateVideo.viewJobs'), { onClick: () => navigate('#/jobs') }),
        button(t('generateVideo.viewAssets'), { onClick: () => navigate('#/assets') }),
      ),
    ),
  );
}

function renderResultError(target, error) {
  const diagnostics = errorDiagnostics(error);
  const message = safeErrorMessage(error, t('generateVideo.error'));
  mount(target,
    el('div', { class: 'diagnostic-card' },
      el('p', { class: 'card-title' }, message),
      metaGrid([
        { label: t('jobs.errorCategory'), value: diagnostics.error_category },
        { label: t('jobs.gatewayStage'), value: diagnostics.gateway_stage },
        { label: t('jobs.retryable'), value: diagnostics.retryable === true ? t('jobs.retryable') : diagnostics.retryable === false ? t('jobs.notRetryable') : '' },
        { label: t('generateVideo.jobId'), value: diagnostics.existing_job?.job_id },
      ]),
    ),
  );
}

async function loadCatalog() {
  return api.get('/admin/catalog');
}

function buildPage(catalog, referenceAssets = []) {
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

  const providerSelect = select(providers.map((provider) => option(providerLabel(provider), provider.id)), { name: 'provider' });
  const modelSelect = select([], { name: 'model' });
  const sizeSelect = select([], { name: 'size' });
  const widthInput = input({ name: 'width', type: 'number', min: 256, max: 2048 });
  const heightInput = input({ name: 'height', type: 'number', min: 256, max: 1536 });
  const promptInput = textarea({
    name: 'prompt',
    maxLength: 32000,
    placeholder: t('generateVideo.promptPlaceholder'),
  });
  const paramsTarget = el('div', { class: 'form-grid video-param-grid' });
  const refInputsTarget = el('div', { class: 'form-stack' });
  const referenceUploadTarget = el('div');
  const referenceUpload = createReferenceUpload({ target: referenceUploadTarget });
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
  let paramInputs = {};

  function syncReferencePreview() {
    const selected = referenceAssets.find((asset) => asset.value === referenceAssetSelect.value);
    referencePreview.hidden = !selected;
    referencePreviewImage.src = selected?.value || '';
    referencePreviewName.textContent = selected?.label || '';
  }

  function clearReference() {
    referenceUpload.clear();
    referenceAssetSelect.value = '';
    syncReferencePreview();
  }

  function renderReferenceInputs(model) {
    if (!supportsReferenceImage(model)) return emptyState(t('generateVideo.noRefInputs'));
    return el('div', { class: 'form-stack compact-stack video-reference-controls' },
      field(t('generateVideo.referenceUpload'), referenceUploadTarget, {
        help: t('generateVideo.referenceUploadHelp'),
      }),
      field(t('generateVideo.referenceAsset'), referenceAssetSelect, {
        help: t('generateVideo.referenceAssetHelp'),
      }),
      referencePreview,
      el('div', { class: 'action-row video-reference-actions' }, referenceClear),
    );
  }

  function currentModel() {
    return modelById(allModels, modelSelect.value) || allModels[0];
  }

  function syncModelOptions() {
    const filtered = modelsForProvider(allModels, providerSelect.value);
    replaceOptions(modelSelect, filtered.map((model) => option(modelLabel(providers, model), model.id)));
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

  function syncModelMetadata() {
    const model = currentModel();
    replaceOptions(sizeSelect, sizeOptions(model));
    sizeSelect.value = (model?.size_presets || [])[0] || 'custom';
    syncSizeFields();

    const params = model?.params && typeof model.params === 'object' ? model.params : {};
    paramInputs = {};
    mount(paramsTarget,
      Object.entries(params)
        .filter(([key]) => !['width', 'height'].includes(key))
        .map(([key, type]) => {
          const control = input({
            name: key,
            autocomplete: 'off',
            placeholder: String(type || ''),
            ...numericInputAttrs(type),
          });
          paramInputs[key] = { control, type };
          return field(paramLabel(key), control);
        }),
    );
    if (!Object.keys(paramInputs).length) {
      mount(paramsTarget, emptyState(t('generateVideo.noParams')));
    }

    mount(refInputsTarget, renderReferenceInputs(model));
    renderModelSummary(modelSummary, providers, model);
  }

  async function submitVideo() {
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

    const width = Number(widthInput.value);
    const height = Number(heightInput.value);
    if (!Number.isFinite(width) || !Number.isFinite(height)) {
      toast(t('generateVideo.sizeRequired'), 'error');
      return;
    }

    const payload = {
      prompt,
      model: model.provider_model || model.id,
      width,
      height,
      wait_for_completion: false,
    };

    Object.entries(paramInputs).forEach(([key, item]) => {
      const value = paramValue(item.control.value, item.type);
      if (value !== undefined) payload[key] = value;
    });

    submit.disabled = true;
    submit.textContent = t('generateVideo.submitting');
    renderResultLoading(resultPanel);
    try {
      if (supportsReferenceImage(model)) {
        const uploadedReference = await referenceUpload.prepare();
        const selectedReference = uploadedReference || referenceAssetSelect.value;
        if (selectedReference) payload.image = selectedReference;
      }
      const result = await api.post('/videos', payload);
      renderResultSuccess(resultPanel, result, model);
    } catch (error) {
      renderResultError(resultPanel, error);
    } finally {
      submit.disabled = false;
      submit.textContent = t('generateVideo.submit');
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

  return [
    pageHeader({
      kicker: t('generateVideo.kicker'),
      title: t('generateVideo.title'),
      subtitle: t('generateVideo.subtitle'),
      actions: [
        button(t('generateVideo.viewJobs'), { onClick: () => navigate('#/jobs') }),
        button(t('generateVideo.viewAssets'), { onClick: () => navigate('#/assets') }),
      ],
    }),
    el('div', { class: 'generate-grid video-generate-grid' },
      panel({ title: t('generateVideo.title'), className: 'creator-panel' },
        el('div', { class: 'panel-body form-stack' },
          field(t('generateVideo.prompt'), promptInput, { className: 'span-2' }),
          el('div', { class: 'form-grid' },
            field(t('generateVideo.provider'), providerSelect),
            field(t('generateVideo.model'), modelSelect),
            field(t('generateVideo.sizePreset'), sizeSelect),
            field(t('generateVideo.width'), widthInput),
            field(t('generateVideo.height'), heightInput),
          ),
          el('div', { class: 'form-subsection' },
            el('div', { class: 'form-subsection-header' }, t('generateVideo.params')),
            paramsTarget,
          ),
          el('div', { class: 'form-subsection' },
            el('div', { class: 'form-subsection-header' },
              el('span', {}, t('generateVideo.refInputs')),
              el('small', {}, t('generateVideo.refInputsSubtitle')),
            ),
            refInputsTarget,
          ),
          el('div', { class: 'hint-box' }, el('span', {}, 'i'), el('p', {}, t('generateVideo.catalogHelp'))),
          el('div', { class: 'action-row creator-actions' }, submit),
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
  mount(content, loadingState(t('common.loading')));

  try {
    const [catalog, referenceAssets] = await Promise.all([
      loadCatalog(),
      loadImageReferenceAssets().catch(() => []),
    ]);
    mount(content, buildPage(catalog, referenceAssets));
  } catch (error) {
    mount(content,
      pageHeader({ kicker: t('generateVideo.kicker'), title: t('generateVideo.title'), subtitle: t('generateVideo.subtitle') }),
      errorState(t('generateVideo.catalogError'), safeErrorMessage(error, t('generateVideo.catalogError'))),
    );
  }
}

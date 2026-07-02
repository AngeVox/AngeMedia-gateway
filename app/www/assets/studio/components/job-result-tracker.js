import { api } from '../api.js';
import { t } from '../i18n.js';
import { badge, statusBadge } from './badges.js';
import { button, linkButton } from './buttons.js';
import { el, mount } from './dom.js';
import { metaGrid } from './page.js';
import { emptyState, loadingState } from './states.js';
import { safeAssetHref, buildAssetDownloadName, isVideoAsset } from '../lib/asset-url.js';
import { formatDuration, truncateText } from '../lib/format.js';
import { displayJobErrorCategory, displayJobProviderStatus, displayJobStage } from '../lib/job-display.js';
import { safeText } from '../lib/security.js';
import { navigate } from '../router.js';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled']);

function firstAsset(detail, mediaType) {
  const assets = Array.isArray(detail?.assets) ? detail.assets : [];
  return assets.find((asset) => asset?.media_type === mediaType) || assets[0] || null;
}

function resultPath(detail, mediaType) {
  const asset = firstAsset(detail, mediaType);
  const assetPath = safeAssetHref(asset?.url_path || '');
  if (assetPath) return { path: assetPath, asset };
  const generationPath = safeAssetHref(detail?.generation?.result_url || '');
  return generationPath ? { path: generationPath, asset: { ...detail.generation, media_type: mediaType } } : { path: '', asset };
}

function openJob(jobId) {
  sessionStorage.setItem('studio_open_job_id', jobId);
  navigate('#/jobs');
}

function openAssets(jobId) {
  if (jobId) sessionStorage.setItem('studio_asset_filter_job_id', jobId);
  navigate('#/assets');
}

function actions(jobId, assetPath, asset) {
  return el('div', { class: 'action-row creator-actions' },
    button(t('generateResult.viewJob'), { onClick: () => openJob(jobId) }),
    button(t('generateResult.viewAssets'), { onClick: () => openAssets(jobId) }),
    assetPath ? linkButton(t('generateResult.openAsset'), assetPath, {
      variant: 'primary',
      download: buildAssetDownloadName(asset || { id: jobId, url_path: assetPath }),
    }) : null,
  );
}

function renderPending(target, detail, mediaType, prompt, refresh) {
  mount(target,
    el('div', { class: 'result-success job-result-tracker' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateResult.trackingTitle')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt || detail?.prompt_summary || '-', 240), 120)),
        ),
        statusBadge(detail?.status || 'queued', t(`jobs.${detail?.status || 'queued'}`)),
      ),
      loadingState(t('generateResult.waiting')),
      metaGrid([
        { label: t('generateImage.jobId'), value: detail?.job_id },
        { label: t('jobs.status'), value: detail?.status ? t(`jobs.${detail.status}`) : '' },
        { label: t('jobs.stage'), value: displayJobStage(detail?.stage) },
        { label: t('jobs.providerStatus'), value: displayJobProviderStatus(detail?.provider_status) },
        { label: t('generateImage.provider'), value: safeText(detail?.provider, 80) },
        { label: t('generateImage.model'), value: safeText(detail?.model, 96) },
      ]),
      el('p', { class: 'field-help' }, mediaType === 'video' ? t('generateVideo.workerRequiredHelp') : t('generateImage.workerRequiredHelp')),
      el('div', { class: 'action-row creator-actions' },
        button(t('generateResult.refresh'), { onClick: refresh }),
        button(t('generateResult.viewJob'), { onClick: () => openJob(detail?.job_id) }),
      ),
    ),
  );
}

function renderFailure(target, detail, prompt, refresh) {
  mount(target,
    el('div', { class: 'diagnostic-card job-result-tracker' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateResult.failed')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt || detail?.prompt_summary || '-', 240), 120)),
        ),
        badge(t('jobs.failed'), 'danger'),
      ),
      metaGrid([
        { label: t('generateImage.jobId'), value: detail?.job_id },
        { label: t('jobs.errorCategory'), value: displayJobErrorCategory(detail?.error_category) },
        { label: t('jobs.gatewayStage'), value: displayJobStage(detail?.gateway_stage) },
        { label: t('jobs.retryable'), value: detail?.retryable === true ? t('jobs.retryable') : detail?.retryable === false ? t('jobs.notRetryable') : '' },
      ]),
      detail?.human_hint ? el('p', { class: 'field-help' }, safeText(detail.human_hint, 240)) : null,
      detail?.error_message ? el('p', { class: 'field-help bounded-error' }, safeText(detail.error_message, 500)) : null,
      el('div', { class: 'action-row creator-actions' },
        button(t('generateResult.refresh'), { onClick: refresh }),
        button(t('generateResult.viewJob'), { onClick: () => openJob(detail?.job_id) }),
      ),
    ),
  );
}

function renderSuccess(target, detail, mediaType, prompt) {
  const { path, asset } = resultPath(detail, mediaType);
  const preview = path && mediaType === 'video'
    ? el('div', { class: 'preview-box' }, el('video', { class: 'result-video', src: path, controls: true, preload: 'metadata' }))
    : path
      ? el('div', { class: 'preview-box' }, el('img', { class: 'result-image', src: path, alt: t('generateImage.previewAlt') }))
      : el('div', { class: 'preview-box' }, emptyState(t('generateResult.assetPending')));
  mount(target,
    el('div', { class: 'result-success job-result-tracker' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, mediaType === 'video' ? t('generateVideo.submitSuccess') : t('generateImage.success')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt || detail?.prompt_summary || '-', 240), 120)),
        ),
        badge(t('jobs.succeeded'), 'success'),
      ),
      preview,
      metaGrid([
        { label: t('generateImage.jobId'), value: detail?.job_id },
        { label: t('generateImage.provider'), value: safeText(detail?.provider, 80) },
        { label: t('generateImage.model'), value: safeText(detail?.model, 96) },
        { label: t('generateImage.duration'), value: detail?.duration_ms ? formatDuration(detail.duration_ms) : '' },
      ]),
      actions(detail?.job_id, path, isVideoAsset(asset) ? { ...asset, media_type: 'video' } : asset),
    ),
  );
}

function renderDetail(target, detail, mediaType, prompt, refresh) {
  if (!detail) {
    mount(target, emptyState(t('generateResult.unavailable')));
    return;
  }
  if (detail.status === 'failed' || detail.status === 'canceled') {
    renderFailure(target, detail, prompt, refresh);
    return;
  }
  if (detail.status === 'succeeded') {
    renderSuccess(target, detail, mediaType, prompt);
    return;
  }
  renderPending(target, detail, mediaType, prompt, refresh);
}

async function loadSnapshot(jobId) {
  const result = await api.get(`/admin/jobs/${encodeURIComponent(jobId)}`);
  return result?.data || null;
}

export function startJobResultTracker(target, { jobId, mediaType, prompt = '', initial = null } = {}) {
  if (!jobId) return null;
  let closed = false;
  let source = null;

  async function refresh() {
    const detail = await loadSnapshot(jobId);
    if (closed) return;
    renderDetail(target, detail, mediaType, prompt, refresh);
    if (TERMINAL_STATUSES.has(detail?.status)) close();
  }

  function close() {
    closed = true;
    if (source) source.close();
  }

  if (initial) {
    renderDetail(target, { ...initial, job_id: jobId, status: initial.status || 'queued' }, mediaType, prompt, refresh);
  } else {
    mount(target, loadingState(t('generateResult.waiting')));
  }

  if (typeof EventSource === 'function') {
    source = new EventSource(`/v1/admin/jobs/${encodeURIComponent(jobId)}/stream`);
    source.addEventListener('job', (event) => {
      if (closed) return;
      const payload = JSON.parse(event.data || '{}');
      const detail = payload?.data || null;
      renderDetail(target, detail, mediaType, prompt, refresh);
      if (TERMINAL_STATUSES.has(detail?.status)) close();
    });
    source.addEventListener('error', () => {
      if (closed) return;
      close();
      refresh().catch(() => mount(target, emptyState(t('generateResult.unavailable'))));
    });
    refresh().catch(() => null);
  } else {
    refresh().catch(() => mount(target, emptyState(t('generateResult.unavailable'))));
  }

  return { close, refresh };
}

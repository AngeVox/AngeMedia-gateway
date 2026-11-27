import { api } from '../api.js';
import { t } from '../i18n.js';
import { badge, statusBadge } from './badges.js';
import { button, linkButton } from './buttons.js';
import { el, mount } from './dom.js';
import { metaGrid } from './page.js';
import { emptyState, loadingState } from './states.js';
import { safeAssetHref, buildAssetDownloadName } from '../lib/asset-url.js';
import { formatDuration, truncateText } from '../lib/format.js';
import { displayJobErrorCategory, displayJobErrorHint, displayJobProvider, displayJobProviderStatus, displayJobStage, showJobErrorMessage } from '../lib/job-display.js';
import { safeText } from '../lib/security.js';
import { navigate, createRenderScope, bindViewResources } from '../router.js';
import { captureSessionScope } from '../auth.js';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled']);
const JOB_STATUSES = new Set(['queued', 'running', ...TERMINAL_STATUSES]);
const trackers = new WeakMap();

export function stopJobResultTracker(target) {
  trackers.get(target)?.close();
}

function refreshButton(refresh) {
  const node = button(t('generateResult.refresh'), { onClick: refresh });
  node.dataset.jobRefresh = 'true';
  return node;
}

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

function actions(jobId, href, asset) {
  return el('div', { class: 'action-row creator-actions' },
    button(t('generateResult.viewJob'), { onClick: () => openJob(jobId) }),
    button(t('generateResult.viewAssets'), { onClick: () => openAssets(jobId) }),
    href ? linkButton(t('common.download'), href, {
      variant: 'primary',
      download: buildAssetDownloadName(asset),
    }) : null,
  );
}

function renderPending(target, detail, prompt, refresh) {
  mount(target,
    el('div', { class: 'result-success job-result-tracker' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateResult.trackingTitle')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt || detail?.prompt_summary || '-', 240), 120)),
        ),
        statusBadge(detail?.status || 'queued', t(`jobs.${detail?.status || 'queued'}`)),
      ),
      loadingState(t(detail?.status === 'queued' ? 'generateResult.queueWaiting' : 'generateResult.waiting')),
      metaGrid([
        { label: t('generateImage.jobId'), value: detail?.job_id },
        { label: t('jobs.status'), value: detail?.status ? t(`jobs.${detail.status}`) : '' },
        { label: t('jobs.stage'), value: displayJobStage(detail?.stage) },
        { label: t('jobs.providerStatus'), value: displayJobProviderStatus(detail?.provider_status) },
        { label: t('generateImage.provider'), value: safeText(displayJobProvider(detail?.provider), 80) },
        { label: t('generateImage.model'), value: safeText(detail?.model, 96) },
      ]),
      el('div', { class: 'action-row creator-actions' },
        refreshButton(refresh),
        button(t('generateResult.viewJob'), { onClick: () => openJob(detail?.job_id) }),
      ),
    ),
  );
}

function renderFailure(target, detail, prompt, refresh) {
  const canceled = detail.status === 'canceled';
  const hint = displayJobErrorHint(detail);
  mount(target,
    el('div', { class: 'diagnostic-card job-result-tracker' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t(canceled ? 'jobs.canceled' : 'generateResult.failed')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt || detail?.prompt_summary || '-', 240), 120)),
        ),
        badge(t(canceled ? 'jobs.canceled' : 'jobs.failed'), canceled ? 'muted' : 'danger'),
      ),
      metaGrid([
        { label: t('generateImage.jobId'), value: detail?.job_id },
        { label: t('jobs.errorCategory'), value: displayJobErrorCategory(detail?.error_category) },
        { label: t('jobs.gatewayStage'), value: displayJobStage(detail?.gateway_stage) },
        { label: t('jobs.retryable'), value: detail?.retryable === true ? t('jobs.retryable') : detail?.retryable === false ? t('jobs.notRetryable') : '' },
      ]),
      hint ? el('p', { class: 'field-help' }, safeText(hint, 240)) : null,
      showJobErrorMessage(detail) ? el('p', { class: 'field-help bounded-error' }, safeText(detail.error_message, 500)) : null,
      el('div', { class: 'action-row creator-actions' },
        refreshButton(refresh),
        button(t('generateResult.viewJob'), { onClick: () => openJob(detail?.job_id) }),
      ),
    ),
  );
}

function renderSuccess(target, detail, mediaType, prompt, refresh) {
  const { path, asset } = resultPath(detail, mediaType);
  const href = api.assetUrl(path);
  const previewHref = api.assetUrl(path, { preview: true });
  const preview = previewHref && mediaType === 'video'
    ? el('div', { class: 'preview-box' }, el('video', { class: 'result-video', src: previewHref, controls: true, preload: 'metadata' }))
    : previewHref
      ? el('div', { class: 'preview-box' }, el('img', { class: 'result-image', src: previewHref, alt: t('generateImage.previewAlt') }))
      : el('div', { class: 'preview-box' }, emptyState(t('generateResult.assetPending')));
  mount(target,
    el('div', { class: 'result-success job-result-tracker' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, mediaType === 'video' ? t('generateVideo.success') : t('generateImage.success')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt || detail?.prompt_summary || '-', 240), 120)),
        ),
        badge(t('jobs.succeeded'), 'success'),
      ),
      preview,
      metaGrid([
        { label: t('generateImage.jobId'), value: detail?.job_id },
        { label: t('generateImage.provider'), value: safeText(displayJobProvider(detail?.provider), 80) },
        { label: t('generateImage.model'), value: safeText(detail?.model, 96) },
        { label: t('generateImage.duration'), value: detail?.duration_ms ? formatDuration(detail.duration_ms) : '' },
      ]),
      actions(detail?.job_id, href, { id: detail?.job_id, ...asset, url_path: path, media_type: mediaType }),
      !href ? refreshButton(refresh) : null,
    ),
  );
}

function renderDetail(target, detail, mediaType, prompt, refresh, jobId) {
  if (!detail) {
    mount(target, emptyState(t('generateResult.unavailable')),
      el('div', { class: 'action-row creator-actions' },
        refreshButton(refresh), button(t('generateResult.viewJob'), { onClick: () => openJob(jobId) })),
    );
    return;
  }
  if (detail.status === 'failed' || detail.status === 'canceled') {
    renderFailure(target, detail, prompt, refresh);
    return;
  }
  if (detail.status === 'succeeded') {
    renderSuccess(target, detail, mediaType, prompt, refresh);
    return;
  }
  renderPending(target, detail, prompt, refresh);
}

async function loadSnapshot(jobId) {
  const result = await api.get(`/admin/jobs/${encodeURIComponent(jobId)}`);
  return result?.data || null;
}

export function startJobResultTracker(target, { jobId, mediaType, prompt = '', initial = null } = {}) {
  if (!jobId || !target?.isConnected) return null;
  stopJobResultTracker(target);
  const scope = createRenderScope(target);
  const isSessionCurrent = captureSessionScope();
  let disposed = false, source = null, pending = null;
  let revision = 0, disconnected = false, refreshFailed = false, hasSnapshot = !!initial;
  let pendingRevision = 0;
  let detail = initial ? { ...initial, job_id: jobId, status: initial.status || 'queued' } : null;
  const isCurrent = () => !disposed && scope.isActive() && isSessionCurrent();
  const active = () => {
    if (!isCurrent()) { close(); return false; }
    return resources.isActive();
  };
  const finished = value => TERMINAL_STATUSES.has(value?.status) &&
    (value.status !== 'succeeded' || !!resultPath(value, mediaType).path);
  const valid = value => value === null || (value?.job_id === jobId && JOB_STATUSES.has(value.status));

  function stopStream() {
    if (source) { source.close(); source = null; }
  }

  function close() {
    if (disposed) return;
    disposed = true;
    revision += 1; pending = null;
    stopStream(); resources.close();
    if (trackers.get(target) === controller) {
      trackers.delete(target); target.removeAttribute('aria-busy');
    }
  }

  function updateControls() {
    target.setAttribute('aria-busy', String(!!pending));
    target.querySelectorAll('[data-job-refresh]').forEach(node => {
      node.disabled = !!pending;
      node.textContent = t(pending ? 'generateResult.refreshing' : 'generateResult.refresh');
    });
  }

  function paint() {
    if (!active()) return;
    if (!hasSnapshot && !refreshFailed) {
      mount(target, loadingState(t('generateResult.waiting')), refreshButton(refresh));
    } else renderDetail(target, detail, mediaType, prompt, refresh, jobId);
    const message = refreshFailed ? t('generateResult.refreshFailed') :
      disconnected && !finished(detail) ? t(source?.readyState === 0 ? 'generateResult.reconnecting' : 'generateResult.connectionLost') : '';
    if (message) target.append(el('p', { class: 'field-help', role: refreshFailed ? 'alert' : 'status' }, message));
    if (refreshFailed && !target.querySelector('[data-job-refresh]')) target.append(refreshButton(refresh));
    updateControls();
  }

  function refresh() {
    if (!active()) return Promise.resolve();
    if (pending) return pending;
    const observedRevision = revision;
    pendingRevision = observedRevision;
    // 一次读取共用一个Promise；读取期间收到的更新事件优先，不让旧快照回退进度。
    const request = Promise.resolve().then(() => active() ? loadSnapshot(jobId) : null).then(value => {
      if (!active() || observedRevision !== revision) return;
      if (!valid(value)) throw new Error();
      detail = value; hasSnapshot = true; refreshFailed = false;
      if (value === null || finished(value)) stopStream();
      paint();
    }).catch(() => {
      if (!active() || observedRevision !== revision) return;
      refreshFailed = true; paint();
    }).finally(() => {
      if (pending === request) pending = null;
      if (active()) updateControls();
    });
    pending = request; updateControls();
    return request;
  }

  function recover(keepStream = false) {
    if (!keepStream) stopStream();
    disconnected = true;
    paint();
    // 先收尾已经被事件淘汰的旧读取，再补读一次；不并发堆积，也不循环轮询。
    if (pending && pendingRevision !== revision) {
      const recoveryRevision = revision;
      pending.then(() => { if (active() && revision === recoveryRevision) return refresh(); }).catch(() => null);
    } else refresh().catch(() => null);
  }

  const controller = { close, refresh };
  trackers.set(target, controller);
  const resources = bindViewResources(isCurrent, {
    dispose: close,
    suspend: () => {
      // 缓存保留任务事实；旧读取与旧流永久失效，恢复不等待冻结前的请求。
      revision += 1; pending = null;
      stopStream(); updateControls();
    },
    resume: () => {
      const resumedRevision = revision;
      refresh().then(() => {
        if (!active() || revision !== resumedRevision) return;
        connectStream(); paint();
      });
    },
  });

  function connectStream() {
    if (!active() || source || finished(detail) || (hasSnapshot && detail === null)) return;
    disconnected = false;
    if (typeof EventSource !== 'function') { disconnected = true; return; }
    try {
      const stream = new EventSource(api.eventUrl(`/admin/jobs/${encodeURIComponent(jobId)}/stream`));
      source = stream;
      stream.addEventListener('job', event => {
        if (!active()) return;
        if (source !== stream) return;
        try {
          const value = JSON.parse(event.data || '{}').data;
          if (!valid(value)) throw new Error();
          revision += 1; detail = value; hasSnapshot = true; refreshFailed = false; disconnected = false;
          if (value === null || finished(value)) stopStream();
          paint();
        } catch (_) { recover(); }
      });
      stream.addEventListener('error', () => {
        if (!active()) return;
        // CONNECTING交给原生EventSource重连；永久关闭或非法帧才停止流，不复制重连定时器。
        if (source === stream) recover(stream.readyState === 0);
      });
    } catch (_) { disconnected = true; }
  }
  connectStream();
  // 终态但暂无安全作品链接时继续接收事件，保留手动刷新，不抢先丢弃后到的作品。
  paint(); refresh().catch(() => null);
  return controller;
}

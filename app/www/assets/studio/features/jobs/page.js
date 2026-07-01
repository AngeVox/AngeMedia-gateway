import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { badge, statusBadge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { input, select } from '../../components/forms.js';
import { confirmModal, doubleConfirmModal } from '../../components/modal.js?v=web-studio-2h';
import { clampPage, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel, metricCard, metaGrid } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { formatDate, formatDuration, shortId, truncateText } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';
import { navigate } from '../../router.js';
import { clearHiddenIds, hiddenIdSet, hideIds } from '../../lib/local-display-filters.js?v=web-studio-2h';

const JOB_PAGE_SIZE = 10;
const SORT_DEFAULT = 'created_at_desc';
const HIDDEN_JOBS_KEY = 'studio_jobs_hidden_ids';

const state = {
  page: 1,
  total: 0,
  jobs: [],
  loadingDetail: false,
  selectedJob: null,
  filters: {
    status: '',
    kind: '',
    provider: '',
    model: '',
    sort: SORT_DEFAULT,
  },
};

const STATUS_OPTIONS = [
  { value: '', key: 'jobs.all' },
  { value: 'queued', key: 'jobs.queued' },
  { value: 'running', key: 'jobs.running' },
  { value: 'succeeded', key: 'jobs.succeeded' },
  { value: 'failed', key: 'jobs.failed' },
  { value: 'canceled', key: 'jobs.canceled' },
];

const KIND_OPTIONS = [
  { value: '', key: 'jobs.allKinds' },
  { value: 'image', key: 'jobs.image' },
  { value: 'video', key: 'jobs.video' },
];

const SORT_OPTIONS = [
  { value: 'created_at_desc', key: 'jobs.sortNewest' },
  { value: 'created_at_asc', key: 'jobs.sortOldest' },
  { value: 'updated_at_desc', key: 'jobs.sortUpdatedNewest' },
  { value: 'updated_at_asc', key: 'jobs.sortUpdatedOldest' },
];

function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

function pagerLabels() {
  return {
    prev: t('common.prev'),
    next: t('common.next'),
    status: t('common.pageStatus'),
  };
}

function kindLabel(job) {
  if (job.kind === 'image') return t('jobs.image');
  if (job.kind === 'video') return t('jobs.video');
  return safeText(job.kind || t('jobs.unknown'), 40);
}

function optionList(items) {
  return items.map((item) => ({ value: item.value, label: t(item.key) }));
}

function setFilter(name, value) {
  state.filters[name] = value;
  state.page = 1;
}

function queryString() {
  const params = new URLSearchParams();
  params.set('limit', String(JOB_PAGE_SIZE));
  params.set('offset', String((state.page - 1) * JOB_PAGE_SIZE));
  params.set('sort', state.filters.sort || SORT_DEFAULT);
  ['status', 'kind', 'provider', 'model'].forEach((key) => {
    const value = String(state.filters[key] || '').trim();
    if (value) params.set(key, value);
  });
  return params.toString();
}

async function loadJobs() {
  const result = await api.get(`/admin/jobs?${queryString()}`);
  state.jobs = dataArray(result);
  state.total = Number(result?.total || 0);
  state.page = clampPage(state.page, state.total, JOB_PAGE_SIZE);
}

async function loadDetail(job, renderPage) {
  const jobId = job.id || job.job_id;
  if (!jobId) return;
  state.loadingDetail = true;
  state.selectedJob = { job_id: jobId, status: job.status };
  renderPage();
  try {
    const result = await api.get(`/admin/jobs/${encodeURIComponent(jobId)}`);
    state.selectedJob = result?.data || null;
  } catch (error) {
    toast(safeErrorMessage(error, t('jobs.detailError')), 'error');
    state.selectedJob = null;
  } finally {
    state.loadingDetail = false;
    renderPage();
  }
}

function closeDetail(renderPage) {
  state.selectedJob = null;
  state.loadingDetail = false;
  renderPage();
}

function canRefreshVideoJob(job) {
  return job.kind === 'video' &&
    job.provider === 'agnes_video' &&
    ['queued', 'running'].includes(job.status) &&
    Boolean(job.external_task_id);
}

function refreshMessage(data) {
  const keyByStatus = {
    completed: 'jobs.refreshCompleted',
    download_pending: 'jobs.refreshDownloadPending',
    failed: 'jobs.refreshFailed',
    throttled: 'jobs.refreshThrottled',
    unsupported: 'jobs.refreshUnsupported',
    terminal: 'jobs.refreshTerminal',
  };
  return t(keyByStatus[data?.refresh_status] || 'jobs.refreshPolled');
}

async function refreshVideoJob(job, reload, trigger) {
  const originalLabel = trigger.textContent;
  trigger.disabled = true;
  trigger.textContent = t('jobs.refreshing');
  try {
    const response = await api.post(`/admin/jobs/${encodeURIComponent(job.id)}/refresh`, {});
    const data = response?.data || {};
    toast(refreshMessage(data), data.refresh_status === 'failed' ? 'error' : 'success');
    await reload();
  } catch (_) {
    toast(t('jobs.refreshError'), 'error');
  } finally {
    trigger.disabled = false;
    trigger.textContent = originalLabel;
  }
}

function diagnosticSummary(job) {
  if (job.status !== 'failed' && !job.human_hint && !job.error_category) return null;
  return el('div', { class: 'job-diagnostic-summary' },
    job.human_hint ? el('span', { class: 'job-hint truncate' }, safeText(job.human_hint, 120)) : null,
    el('div', { class: 'action-row' },
      job.error_category ? badge(job.error_category, 'danger') : null,
      job.retryable === true ? badge(t('jobs.retryable'), 'warning') : null,
      job.retryable === false ? badge(t('jobs.notRetryable'), 'muted') : null,
      job.gateway_stage ? badge(job.gateway_stage, 'info') : null,
    ),
  );
}

function jobActions(job, reload, renderPage) {
  const actions = [
    button(t('jobs.detail'), {
      size: 'sm',
      variant: 'primary',
      onClick: () => loadDetail(job, renderPage),
    }),
  ];
  if (canRefreshVideoJob(job)) {
    actions.push(button(t('jobs.refreshStatus'), {
      size: 'sm',
      onClick: (event) => refreshVideoJob(job, reload, event.currentTarget),
    }));
  }
  if (job.kind === 'video' && job.status === 'succeeded') {
    actions.push(button(t('jobs.viewAsset'), {
      size: 'sm',
      onClick: () => {
        sessionStorage.setItem('studio_asset_filter_job_id', job.id);
        navigate('#/assets');
      },
    }));
  }
  return el('div', { class: 'action-row job-actions' }, actions);
}

function jobCard(job, reload, renderPage) {
  return el('article', { class: `job-card job-row ${job.status === 'failed' ? 'failed' : ''}` },
    el('div', { class: 'job-main' },
      el('p', { class: 'card-title' }, `${kindLabel(job)} · ${shortId(job.id)}`),
      el('p', { class: 'card-subtitle' }, formatDate(job.created_at)),
      el('p', { class: 'prompt' }, truncateText(safeText(job.prompt || '-', 220), 140)),
      diagnosticSummary(job),
    ),
    el('div', { class: 'kv-grid job-summary-grid' },
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.status')),
        statusBadge(job.status, t(`jobs.${job.status}`)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.provider')),
        el('span', {}, safeText(job.provider || '-', 80)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.model')),
        el('span', {}, safeText(job.model || '-', 80)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.providerStatus')),
        el('span', {}, safeText(job.provider_status || '-', 64)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.gatewayStage')),
        el('span', {}, safeText(job.gateway_stage || job.stage || '-', 80)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.duration')),
        el('span', {}, formatDuration(job.duration_ms)),
      ),
    ),
    el('div', { class: 'job-side' },
      jobActions(job, reload, renderPage),
      el('p', { class: 'job-safe-message' },
        job.error_message ? `${t('jobs.safeMessage')}: ${safeText(job.error_message, 180)}` : t('jobs.controlsUnavailable'),
      ),
    ),
  );
}

function filterControls(reload) {
  const statusSelect = select(optionList(STATUS_OPTIONS), {
    value: state.filters.status,
    onchange: (event) => {
      setFilter('status', event.currentTarget.value);
      reload();
    },
  });
  const kindSelect = select(optionList(KIND_OPTIONS), {
    value: state.filters.kind,
    onchange: (event) => {
      setFilter('kind', event.currentTarget.value);
      reload();
    },
  });
  const sortSelect = select(optionList(SORT_OPTIONS), {
    value: state.filters.sort,
    onchange: (event) => {
      setFilter('sort', event.currentTarget.value);
      reload();
    },
  });
  const providerInput = input({
    placeholder: t('jobs.provider'),
    value: state.filters.provider,
    oninput: (event) => { state.filters.provider = event.currentTarget.value; },
    onkeydown: (event) => {
      if (event.key === 'Enter') {
        state.page = 1;
        reload();
      }
    },
  });
  const modelInput = input({
    placeholder: t('jobs.model'),
    value: state.filters.model,
    oninput: (event) => { state.filters.model = event.currentTarget.value; },
    onkeydown: (event) => {
      if (event.key === 'Enter') {
        state.page = 1;
        reload();
      }
    },
  });
  return el('div', { class: 'jobs-filter-grid' },
    statusSelect,
    kindSelect,
    providerInput,
    modelInput,
    sortSelect,
    button(t('jobs.applyFilters'), { variant: 'primary', onClick: reload }),
  );
}

function summaryList(summary) {
  const entries = Object.entries(summary || {}).filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!entries.length) return emptyState(t('jobs.noSummary'));
  return el('dl', { class: 'job-summary-list' }, ...entries.slice(0, 12).flatMap(([key, value]) => [
    el('dt', {}, safeText(key, 60)),
    el('dd', {}, safeText(String(value), 140)),
  ]));
}

function detailSection(title, body) {
  return el('section', { class: 'job-detail-section' },
    el('h3', {}, title),
    body,
  );
}

function diagnosticBlock(detail) {
  if (!detail?.error_message && !detail?.human_hint && !detail?.error_category) {
    return emptyState(t('jobs.noDiagnostics'));
  }
  return el('div', { class: 'job-diagnostics job-detail-diagnostics' },
    detail.human_hint ? el('p', { class: 'job-hint' }, safeText(detail.human_hint, 180)) : null,
    el('div', { class: 'action-row' },
      detail.error_category ? badge(detail.error_category, 'danger') : null,
      detail.retryable === true ? badge(t('jobs.retryable'), 'warning') : null,
      detail.retryable === false ? badge(t('jobs.notRetryable'), 'muted') : null,
      detail.gateway_stage ? badge(detail.gateway_stage, 'info') : null,
    ),
    detail.error_message ? el('p', { class: 'job-detail-line' }, safeText(detail.error_message, 260)) : null,
  );
}

function eventList(items, emptyKey) {
  if (!Array.isArray(items) || !items.length) return emptyState(t(emptyKey));
  return el('div', { class: 'job-event-list' }, items.slice(-12).reverse().map((item) =>
    el('article', { class: 'job-event-item' },
      el('div', { class: 'job-event-title' },
        el('strong', {}, safeText(item.event_type || item.status || '-', 80)),
        item.stage ? badge(item.stage, 'muted') : null,
      ),
      el('p', { class: 'card-subtitle' }, formatDate(item.created_at || item.started_at)),
      item.error_message ? el('p', { class: 'job-detail-line' }, safeText(item.error_message, 180)) : null,
    ),
  ));
}

function assetLinks(detail) {
  const assets = Array.isArray(detail?.assets) ? detail.assets : [];
  if (!assets.length) return emptyState(t('jobs.noAssets'));
  return el('div', { class: 'job-asset-list' }, assets.map((asset) =>
    el('article', { class: 'job-asset-row' },
      el('div', {},
        el('strong', {}, safeText(asset.filename || asset.id || '-', 80)),
        el('p', { class: 'card-subtitle' }, `${safeText(asset.media_type || '-', 32)} · ${safeText(asset.provider || '-', 60)}`),
      ),
      button(t('jobs.viewAsset'), {
        size: 'sm',
        onClick: () => {
          sessionStorage.setItem('studio_asset_filter_job_id', detail.job_id);
          navigate('#/assets');
        },
      }),
    ),
  ));
}

function detailDrawer(renderPage) {
  const detail = state.selectedJob;
  return el('div', {
    class: 'job-detail-drawer-layer',
    hidden: !detail && !state.loadingDetail,
  },
    el('button', {
      type: 'button',
      class: 'job-detail-drawer-backdrop',
      ariaLabel: t('common.close'),
      onclick: () => closeDetail(renderPage),
    }),
    el('aside', { class: 'job-detail-drawer' },
      el('header', { class: 'job-detail-drawer-header' },
        el('div', {},
          el('p', { class: 'eyebrow' }, t('jobs.detail')),
          el('h2', {}, detail ? `${kindLabel(detail)} · ${shortId(detail.job_id)}` : t('jobs.loading')),
          detail ? el('p', { class: 'card-subtitle' }, formatDate(detail.created_at)) : null,
        ),
        button(t('common.close'), { size: 'sm', onClick: () => closeDetail(renderPage) }),
      ),
      el('div', { class: 'job-detail-drawer-body' },
        state.loadingDetail ? loadingState(t('jobs.detailLoading')) : null,
        detail && !state.loadingDetail ? [
          detailSection(t('jobs.summary'), metaGrid([
            { label: t('jobs.status'), value: detail.status ? t(`jobs.${detail.status}`) : '-' },
            { label: t('jobs.stage'), value: safeText(detail.stage || '-', 80) },
            { label: t('jobs.provider'), value: safeText(detail.provider || '-', 80) },
            { label: t('jobs.model'), value: safeText(detail.model || '-', 80) },
            { label: t('jobs.duration'), value: formatDuration(detail.duration_ms) },
            { label: t('jobs.providerStatus'), value: safeText(detail.provider_status || '-', 80) },
          ])),
          detailSection(t('jobs.prompt'), el('p', { class: 'job-detail-line' }, safeText(detail.prompt_summary || '-', 260))),
          detailSection(t('jobs.diagnostics'), diagnosticBlock(detail)),
          detailSection(t('jobs.inputSummary'), summaryList(detail.input_summary)),
          detailSection(t('jobs.outputSummary'), summaryList(detail.output_summary)),
          detailSection(t('jobs.assets'), assetLinks(detail)),
          detailSection(t('jobs.generation'), summaryList(detail.generation)),
          detailSection(t('jobs.events'), eventList(detail.events, 'jobs.noEvents')),
          detailSection(t('jobs.attempts'), eventList(detail.attempts, 'jobs.noAttempts')),
          detailSection(t('jobs.controlActions'), el('p', { class: 'card-subtitle' }, t('jobs.controlsUnavailable'))),
        ] : null,
      ),
    ),
  );
}

function metrics() {
  const running = state.jobs.filter((job) => job.status === 'running').length;
  const failed = state.jobs.filter((job) => job.status === 'failed').length;
  const succeeded = state.jobs.filter((job) => job.status === 'succeeded').length;
  return el('div', { class: 'metric-grid' },
    metricCard({ label: t('jobs.all'), value: String(state.total), meta: t('jobs.title'), tone: 'teal', icon: 'Jobs' }),
    metricCard({ label: t('jobs.running'), value: String(running), meta: t('jobs.currentPage'), tone: 'blue', icon: 'Run' }),
    metricCard({ label: t('jobs.succeeded'), value: String(succeeded), meta: t('jobs.currentPage'), tone: 'violet', icon: 'OK' }),
    metricCard({ label: t('jobs.failed'), value: String(failed), meta: t('jobs.currentPage'), tone: 'gold', icon: 'Err' }),
  );
}

function renderJobs(content, reload) {
  const renderPage = () => renderJobs(content, reload);
  const hidden = hiddenIdSet(HIDDEN_JOBS_KEY);
  const visibleJobs = state.jobs.filter((job) => !hidden.has(String(job.id || '')));
  const hiddenCount = state.jobs.length - visibleJobs.length;
  const hideCurrentPage = () => {
    hideIds(HIDDEN_JOBS_KEY, state.jobs.map((job) => String(job.id || '')).filter(Boolean));
    renderJobs(content, reload);
  };
  const restoreHidden = () => {
    clearHiddenIds(HIDDEN_JOBS_KEY);
    renderJobs(content, reload);
  };
  const cleanableJobs = visibleJobs.filter((job) => ['succeeded', 'failed', 'canceled'].includes(job.status));
  const queuedJobs = visibleJobs.filter((job) => job.status === 'queued');
  const cleanupCurrentPage = () => doubleConfirmModal({
    title: t('jobs.cleanupTitle'),
    message: t('jobs.cleanupMessage'),
    secondMessage: t('jobs.cleanupSecondMessage'),
    confirmText: t('jobs.cleanupConfirmText'),
    confirmLabel: t('jobs.cleanupAction'),
    cancelLabel: t('common.cancel'),
    danger: true,
    onConfirm: async () => {
      await api.post('/admin/jobs/cleanup', {
        job_ids: cleanableJobs.map((job) => job.id).filter(Boolean),
        statuses: ['succeeded', 'failed', 'canceled'],
        limit: cleanableJobs.length || 1,
        confirm: t('jobs.cleanupConfirmText'),
      });
      await reload();
    },
  });
  const requeueStale = () => confirmModal({
    title: t('jobs.requeueStaleTitle'),
    message: t('jobs.requeueStaleMessage'),
    confirmLabel: t('jobs.requeueStaleAction'),
    cancelLabel: t('common.cancel'),
    onConfirm: async () => {
      const response = await api.post('/admin/jobs/requeue-stale', {
        job_ids: queuedJobs.map((job) => job.id).filter(Boolean),
        limit: queuedJobs.length || 1,
      });
      const data = response?.data || {};
      toast(`${t('jobs.requeueStaleDone')} ${Number(data.requeued_jobs || 0)} / ${Number(data.skipped_jobs || 0)}`, 'success');
      await reload();
    },
  });
  mount(content,
    pageHeader({
      kicker: t('jobs.kicker'),
      title: t('jobs.title'),
      subtitle: t('jobs.subtitle'),
      actions: [button(t('common.refresh'), { onClick: reload })],
    }),
    metrics(),
    panel({},
      el('div', { class: 'jobs-toolbar' },
        filterControls(reload),
        badge(`${state.jobs.length} / ${state.total}`, 'muted'),
        hiddenCount ? badge(`${t('jobs.hiddenLocal')} ${hiddenCount}`, 'warning') : null,
        button(t('jobs.cleanupAction'), { size: 'sm', variant: 'danger', onClick: cleanupCurrentPage, disabled: !cleanableJobs.length }),
        button(t('jobs.requeueStaleAction'), { size: 'sm', onClick: requeueStale, disabled: !queuedJobs.length }),
        button(t('jobs.hideCurrentPage'), { size: 'sm', onClick: hideCurrentPage, disabled: !state.jobs.length }),
        button(t('jobs.restoreHidden'), { size: 'sm', onClick: restoreHidden }),
      ),
      el('div', { class: 'jobs-content' },
        visibleJobs.length ? el('div', { class: 'job-list bounded-list' }, visibleJobs.map((job) => jobCard(job, reload, renderPage))) :
          emptyState(t('jobs.empty')),
      ),
      paginationBar({
        page: state.page,
        total: state.total,
        pageSize: JOB_PAGE_SIZE,
        labels: pagerLabels(),
        onPage: async (page) => {
          state.page = page;
          await reload();
        },
      }),
    ),
    detailDrawer(renderPage),
  );
}

export async function render() {
  const content = document.getElementById('content');

  async function reload() {
    mount(content, loadingState(t('jobs.loading')));
    try {
      await loadJobs();
      renderJobs(content, reload);
      const pendingJobId = sessionStorage.getItem('studio_open_job_id');
      if (pendingJobId) {
        sessionStorage.removeItem('studio_open_job_id');
        await loadDetail({ id: pendingJobId }, () => renderJobs(content, reload));
      }
    } catch (_) {
      mount(content,
        pageHeader({ kicker: t('jobs.kicker'), title: t('jobs.title'), subtitle: t('jobs.subtitle') }),
        errorState(t('jobs.error')),
      );
    }
  }

  await reload();
}

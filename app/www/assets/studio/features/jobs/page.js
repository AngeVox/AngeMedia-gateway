import { jobDetailContent } from '../../components/job-detail-content.js';
import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { badge, statusBadge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select } from '../../components/forms.js';
import { confirmModal, doubleConfirmModal } from '../../components/modal.js?v=web-studio-2j';
import { clampPage, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel, metricCard } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { formatDate, formatDuration, shortId, truncateText } from '../../lib/format.js';
import {
  displayJobErrorCategory,
  displayJobErrorHint,
  displayJobProvider,
  displayJobProviderStatus,
  displayJobStage,
  showJobErrorMessage,
} from '../../lib/job-display.js';
import { safeText } from '../../lib/security.js';
import { navigate, createRenderScope } from '../../router.js';
import { clearHiddenIds, hiddenIdSet, hideIds } from '../../lib/local-display-filters.js?v=web-studio-2j';

const JOB_PAGE_SIZE = 10;
const SORT_DEFAULT = 'created_at_desc';
const HIDDEN_JOBS_KEY = 'studio_jobs_hidden_ids';
let detailRequest = 0;

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
  return api.get(`/admin/jobs?${queryString()}`);
}

async function loadDetail(job, renderPage, isActive) {
  if (!isActive()) return;
  const jobId = job.id || job.job_id;
  if (!jobId) return;
  const request = ++detailRequest;
  const current = () => isActive() && request === detailRequest;
  state.loadingDetail = true;
  state.selectedJob = { job_id: jobId, status: job.status };
  renderPage();
  try {
    const result = await api.get(`/admin/jobs/${encodeURIComponent(jobId)}`);
    if (!current()) return;
    state.selectedJob = result?.data || null;
  } catch (error) {
    if (!current()) return;
    toast(safeErrorMessage(error, t('jobs.detailError')), 'error');
    state.selectedJob = null;
  } finally {
    if (current()) {
      state.loadingDetail = false;
      renderPage();
    }
  }
}

function closeDetail(renderPage) {
  const jobId = state.selectedJob?.job_id;
  detailRequest += 1;
  state.selectedJob = null;
  state.loadingDetail = false;
  renderPage();
  Array.from(document.querySelectorAll('[data-job-detail-trigger]'))
    .find((node) => node.dataset.jobDetailTrigger === jobId)?.focus();
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
  if (!trigger.isConnected || trigger.disabled) return;
  const originalLabel = trigger.textContent;
  trigger.disabled = true;
  trigger.textContent = t('jobs.refreshing');
  try {
    const response = await api.post(`/admin/jobs/${encodeURIComponent(job.id)}/refresh`, {});
    if (!trigger.isConnected) return;
    const data = response?.data || {};
    toast(refreshMessage(data), data.refresh_status === 'failed' ? 'error' : 'success');
    await reload();
  } catch (_) {
    if (trigger.isConnected) toast(t('jobs.refreshError'), 'error');
  } finally {
    if (trigger.isConnected) {
      trigger.disabled = false;
      trigger.textContent = originalLabel;
    }
  }
}

function diagnosticSummary(job) {
  const hint = displayJobErrorHint(job);
  if (job.status !== 'failed' && !hint && !job.error_category) return null;
  return el('div', { class: 'job-diagnostic-summary' },
    hint ? el('span', { class: 'job-hint truncate' }, safeText(hint, 120)) : null,
    el('div', { class: 'action-row' },
      job.error_category ? badge(displayJobErrorCategory(job.error_category), 'danger') : null,
      job.retryable === true ? badge(t('jobs.retryable'), 'warning') : null,
      job.retryable === false ? badge(t('jobs.notRetryable'), 'muted') : null,
      job.gateway_stage ? badge(displayJobStage(job.gateway_stage), 'info') : null,
    ),
  );
}

function jobActions(job, reload, openDetail) {
  const detailButton = button(t('jobs.detail'), {
    size: 'sm', variant: 'primary', onClick: () => openDetail(job),
  });
  detailButton.dataset.jobDetailTrigger = job.id || job.job_id;
  const actions = [detailButton];
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

function jobCard(job, reload, openDetail) {
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
        el('span', {}, safeText(displayJobProvider(job.provider) || '-', 80)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.model')),
        el('span', {}, safeText(job.model || '-', 80)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.providerStatus')),
        el('span', {}, safeText(displayJobProviderStatus(job.provider_status), 64)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.gatewayStage')),
        el('span', {}, safeText(displayJobStage(job.gateway_stage || job.stage), 80)),
      ),
      el('div', { class: 'kv' },
        el('b', {}, t('jobs.duration')),
        el('span', {}, formatDuration(job.duration_ms)),
      ),
    ),
    el('div', { class: 'job-side' },
      jobActions(job, reload, openDetail),
      showJobErrorMessage(job) ? el('p', { class: 'job-safe-message' },
        `${t('jobs.safeMessage')}: ${safeText(job.error_message, 180)}`) : null,
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
  const hasFilters = state.filters.status || state.filters.kind || state.filters.provider || state.filters.model || state.filters.sort !== SORT_DEFAULT;
  return el('div', { class: 'jobs-filters' },
    el('div', { class: 'jobs-primary-filters' },
      field(t('jobs.status'), statusSelect),
      field(t('jobs.kind'), kindSelect),
    ),
    el('details', { class: 'list-disclosure jobs-more-filters',
      open: Boolean(state.filters.provider || state.filters.model || state.filters.sort !== SORT_DEFAULT),
    },
      el('summary', {}, t('jobs.moreFilters')),
      el('div', { class: 'jobs-filter-grid' },
        field(t('jobs.provider'), providerInput),
        field(t('jobs.model'), modelInput),
        field(t('jobs.sortLabel'), sortSelect),
        button(t('jobs.applyFilters'), { variant: 'primary', onClick: reload }),
      ),
    ),
    hasFilters ? button(t('jobs.clearFilters'), { size: 'sm', onClick: () => {
      state.filters = { status: '', kind: '', provider: '', model: '', sort: SORT_DEFAULT };
      state.page = 1;
      reload();
    } }) : null,
  );
}

function detailDrawer(renderPage) {
  const detail = state.selectedJob;
  return el('dialog', {
    class: 'job-detail-drawer-layer',
    hidden: !detail && !state.loadingDetail,
    'aria-labelledby': 'job-detail-title',
    oncancel: (event) => { event.preventDefault(); closeDetail(renderPage); },
  },
    el('button', {
      type: 'button',
      class: 'job-detail-drawer-backdrop',
      tabIndex: -1,
      ariaHidden: 'true',
      onclick: () => closeDetail(renderPage),
    }),
    el('aside', { class: 'job-detail-drawer' },
      el('header', { class: 'job-detail-drawer-header' },
        el('div', {},
          el('p', { class: 'eyebrow' }, t('jobs.detail')),
          el('h2', { id: 'job-detail-title' }, detail ? `${kindLabel(detail)} · ${shortId(detail.job_id)}` : t('jobs.loading')),
          detail ? el('p', { class: 'card-subtitle' }, formatDate(detail.created_at)) : null,
        ),
        el('button', { class: 'btn btn-sm', type: 'button', autofocus: true, onclick: () => closeDetail(renderPage) }, t('common.close')),
      ),
      el('div', { class: 'job-detail-drawer-body' },
        state.loadingDetail ? loadingState(t('jobs.detailLoading')) : null,
        detail && !state.loadingDetail ? jobDetailContent(detail) : null,
      ),
    ),
  );
}

function metrics() {
  const running = state.jobs.filter((job) => job.status === 'running').length;
  const failed = state.jobs.filter((job) => job.status === 'failed').length;
  const succeeded = state.jobs.filter((job) => job.status === 'succeeded').length;
  return el('div', { class: 'metric-grid list-metrics' },
    metricCard({ label: t('jobs.all'), value: String(state.total), meta: t('jobs.title'), tone: 'teal', icon: 'Jobs' }),
    metricCard({ label: t('jobs.running'), value: String(running), meta: t('jobs.currentPage'), tone: 'blue', icon: 'Run' }),
    metricCard({ label: t('jobs.succeeded'), value: String(succeeded), meta: t('jobs.currentPage'), tone: 'violet', icon: 'OK' }),
    metricCard({ label: t('jobs.failed'), value: String(failed), meta: t('jobs.currentPage'), tone: 'gold', icon: 'Err' }),
  );
}

function renderJobs(content, reload, scope) {
  if (!scope.isActive()) return;
  const renderPage = () => renderJobs(content, reload, scope);
  const openDetail = (job) => loadDetail(job, renderPage, scope.isActive);
  const hidden = hiddenIdSet(HIDDEN_JOBS_KEY);
  const visibleJobs = state.jobs.filter((job) => !hidden.has(String(job.id || '')));
  const hiddenCount = state.jobs.length - visibleJobs.length;
  const hideCurrentPage = () => {
    hideIds(HIDDEN_JOBS_KEY, state.jobs.map((job) => String(job.id || '')).filter(Boolean));
    renderPage();
  };
  const restoreHidden = () => {
    clearHiddenIds(HIDDEN_JOBS_KEY);
    renderPage();
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
    onConfirm: async ({ isActive }) => {
      await api.post('/admin/jobs/cleanup', {
        job_ids: cleanableJobs.map((job) => job.id).filter(Boolean),
        statuses: ['succeeded', 'failed', 'canceled'],
        limit: cleanableJobs.length || 1,
        confirm: t('jobs.cleanupConfirmText'),
      });
      if (isActive()) await reload();
    },
  });
  const requeueStale = () => confirmModal({
    title: t('jobs.requeueStaleTitle'),
    message: t('jobs.requeueStaleMessage'),
    confirmLabel: t('jobs.requeueStaleAction'),
    cancelLabel: t('common.cancel'),
    onConfirm: async ({ isActive }) => {
      const response = await api.post('/admin/jobs/requeue-stale', {
        job_ids: queuedJobs.map((job) => job.id).filter(Boolean),
        limit: queuedJobs.length || 1,
      });
      if (!isActive()) return;
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
        el('details', { class: 'list-disclosure jobs-record-management' },
          el('summary', {}, t('jobs.recordManagement')),
          el('div', { class: 'action-row' },
            button(t('jobs.cleanupAction'), { size: 'sm', variant: 'danger', onClick: cleanupCurrentPage, disabled: !cleanableJobs.length }),
            button(t('jobs.requeueStaleAction'), { size: 'sm', onClick: requeueStale, disabled: !queuedJobs.length }),
            button(t('jobs.hideCurrentPage'), { size: 'sm', onClick: hideCurrentPage, disabled: !state.jobs.length }),
            button(t('jobs.restoreHidden'), { size: 'sm', onClick: restoreHidden }),
          ),
        ),
      ),
      el('div', { class: 'jobs-content' },
        visibleJobs.length ? el('div', { class: 'job-list bounded-list' }, visibleJobs.map((job) => jobCard(job, reload, openDetail))) :
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
  // 原生对话框负责模态焦点和背景隔离，避免另写键盘拦截器。
  const drawer = content.querySelector('.job-detail-drawer-layer');
  if (!drawer.hidden) drawer.showModal();
}

export async function render() {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  detailRequest += 1;
  state.selectedJob = null;
  state.loadingDetail = false;
  const renderPage = () => renderJobs(content, reload, scope);

  async function reload() {
    if (!scope.isActive()) return;
    const current = scope.nextRequest();
    detailRequest += 1;
    if (state.loadingDetail) { state.selectedJob = null; state.loadingDetail = false; }
    mount(content, loadingState(t('jobs.loading')));
    try {
      const result = await loadJobs();
      if (!current()) return;
      // 先判定归属再提交共享列表状态，避免旧结果污染后续详情、筛选和清理范围。
      state.jobs = dataArray(result);
      state.total = Number(result?.total || 0);
      state.page = clampPage(state.page, state.total, JOB_PAGE_SIZE);
      renderPage();
      const pendingJobId = sessionStorage.getItem('studio_open_job_id');
      if (pendingJobId) {
        sessionStorage.removeItem('studio_open_job_id');
        await loadDetail({ id: pendingJobId }, renderPage, current);
      }
    } catch (_) {
      if (!current()) return;
      mount(content,
        pageHeader({ kicker: t('jobs.kicker'), title: t('jobs.title'), subtitle: t('jobs.subtitle') }),
        errorState(t('jobs.error')),
      );
    }
  }

  await reload();
}

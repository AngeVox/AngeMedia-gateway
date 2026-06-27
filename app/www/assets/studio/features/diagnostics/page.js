import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { badge, statusBadge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { pageHeader, panel, metricCard } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { formatDate, shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';
import { navigate } from '../../router.js';

function data(result) {
  return result?.data && typeof result.data === 'object' ? result.data : {};
}

function countValue(map, key) {
  return Number(map?.[key] || 0);
}

function safeBadge(value, tone = 'muted') {
  return badge(safeText(value || '-', 80), tone);
}

function compactMeta(items) {
  return el('div', { class: 'diagnostics-kv-list' }, items.map((item) =>
    el('div', { class: 'diagnostics-kv' },
      el('span', {}, item.label),
      el('strong', {}, safeText(item.value, 96)),
    ),
  ));
}

function statusSummary(statusCounts) {
  return [
    `${t('jobs.queued')} ${countValue(statusCounts, 'queued')}`,
    `${t('jobs.running')} ${countValue(statusCounts, 'running')}`,
    `${t('jobs.succeeded')} ${countValue(statusCounts, 'succeeded')}`,
    `${t('jobs.failed')} ${countValue(statusCounts, 'failed')}`,
  ].join(' · ');
}

function providerList(items) {
  if (!Array.isArray(items) || !items.length) return emptyState(t('diagnostics.noProviders'));
  return el('div', { class: 'diagnostics-list' }, items.slice(0, 12).map((item) =>
    el('article', { class: 'diagnostics-row' },
      el('div', { class: 'truncate' },
        el('strong', {}, safeText(item.name || item.id || '-', 80)),
        el('p', { class: 'card-subtitle truncate' }, safeText(item.default_model || item.provider_type || '-', 96)),
      ),
      safeBadge(item.enabled ? t('providers.enabled') : t('providers.disabled'), item.enabled ? 'success' : 'muted'),
    ),
  ));
}

function failedJobs(items) {
  if (!Array.isArray(items) || !items.length) return emptyState(t('diagnostics.noFailedJobs'));
  return el('div', { class: 'diagnostics-list' }, items.slice(0, 6).map((job) =>
    el('article', { class: 'diagnostics-row diagnostics-failure-row' },
      el('div', { class: 'truncate' },
        el('strong', {}, `${safeText(job.kind || '-', 24)} · ${shortId(job.id)}`),
        el('p', { class: 'card-subtitle truncate' }, safeText(job.human_hint || job.error_message || '-', 180)),
      ),
      el('div', { class: 'action-row diagnostics-row-actions' },
        job.error_category ? safeBadge(job.error_category, 'danger') : null,
        button(t('jobs.detail'), {
          size: 'sm',
          onClick: () => {
            sessionStorage.setItem('studio_open_job_id', job.id);
            navigate('#/jobs');
          },
        }),
      ),
    ),
  ));
}

function dispatchErrors(dispatches) {
  const items = Array.isArray(dispatches?.recent_errors) ? dispatches.recent_errors : [];
  if (!items.length) return emptyState(t('diagnostics.noDispatchErrors'));
  return el('div', { class: 'diagnostics-list' }, items.map((item) =>
    el('article', { class: 'diagnostics-row diagnostics-failure-row' },
      el('div', { class: 'truncate' },
        el('strong', {}, `${safeText(item.status || '-', 32)} · ${shortId(item.job_id || item.id)}`),
        el('p', { class: 'card-subtitle truncate' }, safeText(item.last_error || '-', 180)),
      ),
      el('span', { class: 'card-subtitle' }, formatDate(item.updated_at)),
    ),
  ));
}

function renderDiagnostics(content, summary, reload) {
  const queue = summary.queue || {};
  const statusCounts = queue.status_counts || {};
  const providerSummary = summary.providers || {};
  const customProviders = Array.isArray(providerSummary.custom_providers) ? providerSummary.custom_providers : [];
  const custom = providerSummary.custom || {};

  mount(content,
    pageHeader({
      kicker: t('diagnostics.kicker'),
      title: t('diagnostics.title'),
      subtitle: t('diagnostics.subtitle'),
      actions: [
        button(t('common.refresh'), { onClick: reload }),
        button(t('diagnostics.viewJobs'), { onClick: () => navigate('#/jobs') }),
        button(t('diagnostics.viewProviders'), { onClick: () => navigate('#/providers') }),
      ],
    }),
    el('div', { class: 'metric-grid' },
      metricCard({
        label: t('diagnostics.health'),
        value: t(`diagnostics.${summary.health?.status || 'unknown'}`),
        meta: t('diagnostics.safeSummary'),
        tone: summary.health?.status === 'ok' ? 'teal' : 'gold',
      }),
      metricCard({
        label: t('diagnostics.queueActive'),
        value: String(queue.active_total || 0),
        meta: safeText(queue.backend || '-', 40),
        tone: 'blue',
      }),
      metricCard({
        label: t('diagnostics.failedJobs'),
        value: String(countValue(statusCounts, 'failed')),
        meta: t('diagnostics.recentWindow'),
        tone: 'gold',
      }),
      metricCard({
        label: t('diagnostics.customProviders'),
        value: String(custom.total || 0),
        meta: `${custom.enabled || 0} ${t('providers.enabled')}`,
        tone: 'violet',
      }),
    ),
    el('div', { class: 'diagnostics-grid' },
      panel({ title: t('diagnostics.runtime'), className: 'diagnostics-section' },
        compactMeta([
          { label: t('diagnostics.app'), value: safeText(summary.runtime?.app || '-', 80) },
          { label: t('diagnostics.version'), value: safeText(summary.runtime?.version || '-', 40) },
          { label: t('diagnostics.health'), value: summary.health?.status ? t(`diagnostics.${summary.health.status}`) : '-' },
          { label: t('diagnostics.summary'), value: t('diagnostics.safeSummary') },
        ]),
      ),
      panel({ title: t('diagnostics.queue'), className: 'diagnostics-section' },
        compactMeta([
          { label: t('diagnostics.backend'), value: safeText(queue.backend || '-', 48) },
          { label: t('diagnostics.taskQueue'), value: safeText(queue.task_queue || '-', 64) },
          { label: t('diagnostics.broker'), value: queue.healthy === true ? t('diagnostics.connected') : t('diagnostics.notConnected') },
          { label: t('jobs.status'), value: statusSummary(statusCounts) },
        ]),
      ),
      panel({ title: t('diagnostics.storage'), className: 'diagnostics-section' },
        compactMeta([
          { label: t('diagnostics.database'), value: t(`diagnostics.${summary.database?.state || 'unknown'}`) },
          { label: t('diagnostics.generated'), value: summary.media?.generated?.exists ? t('diagnostics.ok') : t('diagnostics.missing') },
          { label: t('diagnostics.uploads'), value: summary.media?.uploads?.exists ? t('diagnostics.ok') : t('diagnostics.missing') },
          { label: t('diagnostics.summary'), value: t('diagnostics.safeSummary') },
        ]),
      ),
      panel({ title: t('diagnostics.providers'), className: 'diagnostics-section' },
        el('div', { class: 'diagnostics-chip-row' },
          safeBadge(`${t('diagnostics.builtinProviders')} ${(providerSummary.builtin || []).length}`, 'info'),
          safeBadge(`${t('diagnostics.customProviders')} ${custom.total || 0}`, 'violet'),
        ),
        providerList(customProviders),
      ),
      panel({ title: t('diagnostics.recentFailedJobs'), className: 'diagnostics-section diagnostics-section-wide' },
        failedJobs(summary.recent_failed_jobs),
      ),
      panel({ title: t('diagnostics.dispatches'), className: 'diagnostics-section diagnostics-section-wide' },
        el('div', { class: 'diagnostics-chip-row' },
          Object.entries(summary.dispatches?.status_counts || {}).map(([key, value]) => statusBadge(key, `${safeText(key, 32)} ${value}`)),
        ),
        dispatchErrors(summary.dispatches),
      ),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');

  async function reload() {
    mount(content, loadingState(t('diagnostics.loading')));
    try {
      const result = await api.get('/admin/diagnostics/summary');
      renderDiagnostics(content, data(result), reload);
    } catch (_) {
      mount(content,
        pageHeader({ kicker: t('diagnostics.kicker'), title: t('diagnostics.title'), subtitle: t('diagnostics.subtitle') }),
        errorState(t('diagnostics.error')),
      );
    }
  }

  await reload();
}

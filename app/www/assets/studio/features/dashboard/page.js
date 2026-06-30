import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { pageHeader, panel, metricCard, metaGrid } from '../../components/page.js';
import { badge, statusBadge } from '../../components/badges.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { doubleConfirmModal } from '../../components/modal.js?v=web-studio-2h';
import { formatBytes, formatDate, shortId, truncateText } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';
import { navigate } from '../../router.js';
import { clearHiddenSince, hideOlderThanNow, isAfterHiddenSince } from '../../lib/local-display-filters.js?v=web-studio-2h';

const RECENT_JOBS_CLEAR_KEY = 'studio_dashboard_recent_jobs_hidden_since';
const RECENT_FAILURES_CLEAR_KEY = 'studio_dashboard_recent_failures_hidden_since';
const RECENT_ASSETS_CLEAR_KEY = 'studio_dashboard_recent_assets_hidden_since';

async function fetchHealth() {
  const res = await fetch('/health', { credentials: 'include' });
  if (!res.ok) throw new Error('health failed');
  return res.json();
}

function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

function recentJobCard(job) {
  return el('article', { class: `job-card ${job.status === 'failed' ? 'failed' : ''}` },
    el('div', { class: 'job-card-header' },
      el('div', {},
        el('p', { class: 'card-title' }, shortId(job.id)),
        el('p', { class: 'card-subtitle' }, truncateText(job.prompt || '-', 88)),
      ),
      statusBadge(job.status, t(`jobs.${job.status}`)),
    ),
    metaGrid([
      { label: t('jobs.kind'), value: t(`jobs.${job.kind}`) },
      { label: t('jobs.provider'), value: job.provider || '-' },
      { label: t('jobs.model'), value: job.model || '-' },
      { label: t('jobs.created'), value: formatDate(job.created_at) },
    ]),
  );
}

function providerSummaryCard(provider) {
  return el('article', { class: 'provider-row' },
    el('span', { class: 'provider-logo' }, safeText(provider.name || provider.id || '?', 1).slice(0, 1).toUpperCase()),
    el('div', { class: 'truncate' },
      el('b', { class: 'truncate' }, safeText(provider.name || provider.id || '-', 80)),
      el('p', { class: 'card-subtitle' }, safeText(provider.default_model || provider.provider_type || '-', 96)),
    ),
    el('div', { class: 'action-row' },
      provider.enabled ? badge(t('providers.enabled'), 'success') : badge(t('providers.disabled'), 'muted'),
      provider.api_key_configured ? badge(t('providers.configured'), 'success') : badge(t('providers.notConfigured'), 'warning'),
    ),
  );
}

function queuePanel(summary) {
  const counts = summary?.queue?.status_counts || {};
  const kinds = summary?.queue?.kind_counts || {};
  return panel({ title: t('dashboard.queue') },
    el('div', { class: 'queue-box dashboard-status-grid' },
      el('div', { class: 'queue-cell' }, el('span', {}, t('jobs.queued')), el('b', {}, String(counts.queued || 0))),
      el('div', { class: 'queue-cell' }, el('span', {}, t('jobs.running')), el('b', {}, String(counts.running || 0))),
      el('div', { class: 'queue-cell' }, el('span', {}, t('jobs.succeeded')), el('b', {}, String(counts.succeeded || 0))),
      el('div', { class: 'queue-cell' }, el('span', {}, t('jobs.failed')), el('b', {}, String(counts.failed || 0))),
    ),
    el('div', { class: 'dashboard-kind-strip' },
      badge(`${t('jobs.image')} ${kinds.image || 0}`, 'info'),
      badge(`${t('jobs.video')} ${kinds.video || 0}`, 'violet'),
    ),
  );
}

function storagePanel(summary) {
  const storage = summary.storage || {};
  const disk = storage.media_volume || {};
  const media = storage.media || {};
  const volumes = Array.isArray(storage.volumes) ? storage.volumes : [];
  const percent = Math.max(0, Math.min(100, Number(disk.used_percent || 0)));
  const ring = el('div', { class: 'disk-ring disk-ring-storage' },
    el('strong', {}, `${Math.round(percent)}%`),
  );
  ring.style.setProperty('--disk-used', `${percent}%`);
  return panel({ title: t('dashboard.storage'), actions: [button(t('dashboard.viewDiagnostics'), { size: 'sm', onClick: () => navigate('#/diagnostics') })] },
    el('div', { class: 'disk-card' },
      ring,
      el('div', {},
        el('div', { class: 'eyebrow' }, `${t('dashboard.mediaVolume')} ${safeText(disk.label || '-', 16)}`),
        el('p', { class: 'card-subtitle' }, `${t('dashboard.diskFree')} ${formatBytes(disk.free_bytes)} / ${formatBytes(disk.total_bytes)} · ${volumes.length} ${t('dashboard.volumes')}`),
        el('div', { class: 'storage-kv-grid' },
          el('span', {}, t('dashboard.generatedBytes')),
          el('strong', {}, formatBytes(media.generated_bytes)),
          el('span', {}, t('dashboard.uploadBytes')),
          el('strong', {}, formatBytes(media.uploads_bytes)),
          el('span', {}, t('dashboard.mediaBytes')),
          el('strong', {}, formatBytes(media.total_bytes)),
        ),
      ),
    ),
  );
}

function clearRecentButton(labelKey, storageKey, reload) {
  return button(t(labelKey), {
    size: 'sm',
    onClick: () => {
      hideOlderThanNow(storageKey);
      reload();
    },
  });
}

function restoreRecentButton(storageKey, reload) {
  return button(t('dashboard.restoreHidden'), {
    size: 'sm',
    onClick: () => {
      clearHiddenSince(storageKey);
      reload();
    },
  });
}

function cleanupJobsButton(items, reload) {
  const ids = (items || [])
    .filter((item) => ['succeeded', 'failed', 'canceled'].includes(item?.status))
    .map((item) => item.id || item.job_id)
    .filter(Boolean);
  return button(t('jobs.cleanupAction'), {
    size: 'sm',
    variant: 'danger',
    disabled: !ids.length,
    onClick: () => doubleConfirmModal({
      title: t('jobs.cleanupTitle'),
      message: t('jobs.cleanupMessage'),
      secondMessage: t('jobs.cleanupSecondMessage'),
      confirmText: t('jobs.cleanupConfirmText'),
      confirmLabel: t('jobs.cleanupAction'),
      cancelLabel: t('common.cancel'),
      danger: true,
      onConfirm: async () => {
        await api.post('/admin/jobs/cleanup', {
          job_ids: ids,
          statuses: ['succeeded', 'failed', 'canceled'],
          limit: ids.length,
        });
        reload();
      },
    }),
  });
}

function recentFailuresPanel(summary, reload) {
  const failures = (Array.isArray(summary?.recent_failed_jobs) ? summary.recent_failed_jobs : [])
    .filter((item) => isAfterHiddenSince(item, RECENT_FAILURES_CLEAR_KEY));
  return panel({
    title: t('dashboard.recentFailures'),
    actions: [
      clearRecentButton('dashboard.clearRecent', RECENT_FAILURES_CLEAR_KEY, reload),
      cleanupJobsButton(failures, reload),
      restoreRecentButton(RECENT_FAILURES_CLEAR_KEY, reload),
      button(t('common.viewMore'), { size: 'sm', onClick: () => navigate('#/jobs') }),
    ],
  },
    el('div', { class: 'panel-body error-list' },
      failures.length ? failures.map((job) => el('article', { class: 'error-row' },
        el('span', {}, formatDate(job.created_at)),
        el('span', { class: 'truncate' }, safeText(job.human_hint || job.error_message || job.prompt || job.id, 120)),
        badge(job.error_category || t('jobs.failed'), 'danger'),
      )) : emptyState(t('dashboard.noRecentFailures')),
    ),
  );
}

function recentAssetsPanel(summary, reload) {
  const assets = (Array.isArray(summary?.recent_assets) ? summary.recent_assets : [])
    .filter((item) => isAfterHiddenSince(item, RECENT_ASSETS_CLEAR_KEY));
  return panel({
    title: t('dashboard.recentAssets'),
    actions: [
      clearRecentButton('dashboard.clearRecent', RECENT_ASSETS_CLEAR_KEY, reload),
      restoreRecentButton(RECENT_ASSETS_CLEAR_KEY, reload),
      button(t('common.viewMore'), { size: 'sm', onClick: () => navigate('#/assets') }),
    ],
  },
    el('div', { class: 'panel-body dashboard-asset-list' },
      assets.length ? assets.map((asset) => el('article', { class: 'dashboard-asset-row' },
        el('div', { class: 'truncate' },
          el('strong', { class: 'truncate' }, safeText(asset.filename || asset.id || '-', 80)),
          el('p', { class: 'card-subtitle' },
            `${safeText(asset.media_type || '-', 24)} · ${safeText(asset.provider || '-', 48)} · ${formatDate(asset.created_at)}`,
          ),
        ),
        el('div', { class: 'action-row' },
          asset.job?.status ? statusBadge(asset.job.status, t(`jobs.${asset.job.status}`)) : badge(t('assets.legacy'), 'muted'),
          button(t('dashboard.reviewAssets'), { size: 'sm', onClick: () => navigate('#/assets') }),
        ),
      )) : emptyState(t('dashboard.noRecentAssets')),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');
  const reload = () => render();
  mount(content,
    pageHeader({
      kicker: t('dashboard.kicker'),
      title: t('dashboard.title'),
      subtitle: t('dashboard.subtitle'),
      actions: [
        button(t('dashboard.generateImageCta'), { variant: 'primary', onClick: () => navigate('#/generate/image') }),
        button(t('dashboard.reviewAssets'), { onClick: () => navigate('#/assets') }),
      ],
    }),
    loadingState(t('common.loading')),
  );

  try {
    const [health, session, summaryResult, providersResult, keysResult] = await Promise.all([
      fetchHealth().catch(() => ({ status: 'error' })),
      api.get('/admin/session').catch(() => ({ authenticated: false })),
      api.get('/admin/dashboard/summary').catch(() => ({ data: {} })),
      api.get('/admin/providers').catch(() => ({ data: [] })),
      api.get('/admin/gateway-keys').catch(() => ({ data: [] })),
    ]);

    const summary = summaryResult?.data || {};
    const jobs = (Array.isArray(summary.recent_jobs) ? summary.recent_jobs : [])
      .filter((item) => isAfterHiddenSince(item, RECENT_JOBS_CLEAR_KEY));
    const assetCounts = summary.assets || {};
    const queue = summary.queue || {};
    const providers = dataArray(providersResult);
    const keys = dataArray(keysResult).filter((item) => !item.revoked_at);
    const failedCount = queue.status_counts?.failed || 0;

    mount(content,
      pageHeader({
        kicker: t('dashboard.kicker'),
        title: t('dashboard.title'),
        subtitle: t('dashboard.subtitle'),
        actions: [
          button(t('dashboard.generateImageCta'), { variant: 'primary', onClick: () => navigate('#/generate/image') }),
          button(t('dashboard.reviewAssets'), { onClick: () => navigate('#/assets') }),
        ],
      }),
      el('div', { class: 'metric-grid' },
        metricCard({ label: t('dashboard.health'), value: health.status === 'ok' ? t('dashboard.ready') : t('dashboard.notConnected'), meta: t('topbar.gatewayOnline'), tone: 'teal', icon: '◎' }),
        metricCard({ label: t('dashboard.session'), value: session.authenticated ? t('dashboard.signedIn') : t('dashboard.notAuthenticated'), meta: session.username || '-', tone: 'blue', icon: '◇' }),
        metricCard({ label: t('dashboard.assets'), value: String(assetCounts.total || 0), meta: `${assetCounts.image || 0} ${t('assets.image')} · ${assetCounts.video || 0} ${t('assets.video')}`, tone: 'violet', icon: '▧' }),
        metricCard({ label: t('dashboard.jobs'), value: String(queue.active_total || 0), meta: `${failedCount} ${t('dashboard.failedJobs')}`, tone: 'gold', icon: '▤' }),
        metricCard({ label: t('dashboard.providers'), value: String(providers.length), meta: `${keys.length} ${t('dashboard.activeKeys')}`, tone: 'teal', icon: '✣' }),
      ),
      el('div', { class: 'dashboard-grid' },
        panel({
          title: t('dashboard.recentJobs'),
          actions: [
            clearRecentButton('dashboard.clearRecent', RECENT_JOBS_CLEAR_KEY, reload),
            cleanupJobsButton(jobs, reload),
            restoreRecentButton(RECENT_JOBS_CLEAR_KEY, reload),
            button(t('common.viewMore'), { size: 'sm', onClick: () => navigate('#/jobs') }),
          ],
        },
          el('div', { class: 'panel-body' },
            jobs.length ? el('div', { class: 'recent-strip' }, jobs.slice(0, 4).map(recentJobCard)) :
              emptyState(t('jobs.empty')),
          ),
        ),
        el('aside', { class: 'side-stack' },
          queuePanel(summary),
          storagePanel(summary),
        ),
      ),
      el('div', { class: 'dashboard-grid dashboard-grid-bottom' },
        recentFailuresPanel(summary, reload),
        recentAssetsPanel(summary, reload),
      ),
      el('div', { class: 'dashboard-grid dashboard-grid-bottom' },
        panel({
          title: t('dashboard.providerSummary'),
          actions: [button(t('common.viewMore'), { size: 'sm', onClick: () => navigate('#/providers') })],
        },
          el('div', { class: 'panel-body provider-summary' },
            providers.length ? providers.slice(0, 4).map(providerSummaryCard) : emptyState(t('dashboard.noProviders')),
          ),
        ),
      ),
    );
  } catch (_) {
    mount(content,
      pageHeader({ kicker: t('dashboard.kicker'), title: t('dashboard.title'), subtitle: t('dashboard.subtitle') }),
      errorState(t('dashboard.loadFailed')),
    );
  }
}

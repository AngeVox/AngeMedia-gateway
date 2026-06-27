import { api } from '../api.js';
import { t } from '../i18n.js';
import { badge } from '../components/badges.js';
import { button, linkButton } from '../components/buttons.js';
import { el, mount } from '../components/dom.js';
import { pageHeader, panel, metaGrid } from '../components/page.js';
import { emptyState, errorState, loadingState } from '../components/states.js';
import { assetDisplayName, buildAssetDownloadName, isImageAsset, isVideoAsset, safeAssetHref } from '../lib/asset-url.js';
import { formatBytes, formatDate, formatDuration, shortId } from '../lib/format.js';
import { safeText } from '../lib/security.js';
import { navigate } from '../router.js';

function previewNode(asset) {
  const href = safeAssetHref(asset.url_path);
  if (!href) {
    return el('div', { class: 'detail-preview detail-preview-empty' }, t('assets.unavailable'));
  }
  if (isImageAsset(asset)) {
    return el('a', { class: 'detail-preview', href, target: '_blank', rel: 'noopener noreferrer' },
      el('img', { src: href, alt: assetDisplayName(asset), loading: 'lazy' }),
    );
  }
  if (isVideoAsset(asset)) {
    return el('div', { class: 'detail-preview' },
      el('video', { src: href, controls: true, preload: 'metadata' }),
    );
  }
  return el('a', { class: 'detail-preview detail-preview-empty', href, target: '_blank', rel: 'noopener noreferrer' }, t('common.open'));
}

function openJob(asset) {
  const jobId = asset?.job?.job_id || asset?.job_id;
  if (!jobId) return;
  sessionStorage.setItem('studio_open_job_id', jobId);
  navigate('#/jobs');
}

function detailActions(asset) {
  const href = safeAssetHref(asset.url_path);
  const actions = [
    button(t('assets.title'), { onClick: () => navigate('#/assets') }),
  ];
  if (asset.job?.job_id || asset.job_id) {
    actions.push(button(t('assets.viewJob'), { variant: 'primary', onClick: () => openJob(asset) }));
  }
  if (href) {
    actions.push(linkButton(t('common.download'), href, { download: buildAssetDownloadName(asset), target: '_blank' }));
  }
  return actions;
}

function renderAsset(content, asset) {
  const job = asset.job || {};
  const generation = asset.generation || {};
  mount(content,
    pageHeader({
      kicker: t('assets.kicker'),
      title: assetDisplayName(asset),
      subtitle: formatDate(asset.created_at),
      actions: detailActions(asset),
    }),
    el('div', { class: 'detail-page asset-detail-page' },
      el('div', { class: 'detail-grid asset-detail-grid' },
        panel({ title: t('assets.preview'), className: 'detail-section' }, previewNode(asset)),
        panel({ title: t('jobs.summary'), className: 'detail-section' },
          el('div', { class: 'diagnostics-chip-row' },
            badge(safeText(asset.media_type || '-', 32), isVideoAsset(asset) ? 'violet' : 'info'),
            asset.source ? badge(safeText(asset.source, 40), 'muted') : null,
          ),
          metaGrid([
            { label: t('assets.jobId'), value: job.job_id ? shortId(job.job_id) : t('assets.legacy') },
            { label: t('jobs.status'), value: job.status ? t(`jobs.${job.status}`) : t('assets.unknownJob') },
            { label: t('assets.generation'), value: generation.id ? shortId(generation.id) : '-' },
            { label: t('assets.provider'), value: safeText(asset.provider || '-', 80) },
            { label: t('assets.model'), value: safeText(asset.model || '-', 80) },
            { label: t('assets.size'), value: formatBytes(asset.size) },
            { label: t('generateImage.duration'), value: formatDuration(asset.duration_ms) },
          ]),
        ),
        panel({ title: t('assets.prompt'), className: 'detail-section detail-section-wide' },
          el('p', { class: 'job-detail-line' }, safeText(asset.prompt || '-', 360)),
        ),
      ),
    ),
  );
}

export async function render(params = {}) {
  const content = document.getElementById('content');
  if (!params.id) {
    mount(content,
      pageHeader({ kicker: t('assets.kicker'), title: t('assets.title'), subtitle: t('detail.missingId') }),
      panel({ className: 'detail-section' }, emptyState(t('detail.missingId'), t('detail.missingIdCopy'))),
    );
    return;
  }
  mount(content, loadingState(t('assets.loading')));
  try {
    const result = await api.get(`/assets/${encodeURIComponent(params.id)}`);
    renderAsset(content, result?.data || {});
  } catch (_) {
    mount(content,
      pageHeader({
        kicker: t('assets.kicker'),
        title: t('assets.title'),
        subtitle: shortId(params.id),
        actions: [button(t('assets.title'), { onClick: () => navigate('#/assets') })],
      }),
      panel({ className: 'detail-section' }, errorState(t('assets.error'), t('detail.notFound'))),
    );
  }
}

import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button, linkButton } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { select } from '../../components/forms.js';
import { confirmModal } from '../../components/modal.js';
import { clampPage, pageSlice, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel, metricCard, metaGrid } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { assetDisplayName, buildAssetDownloadName, isImageAsset, isVideoAsset, safeAssetHref } from '../../lib/asset-url.js';
import { formatBytes, formatDate, formatDuration, shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

let allAssets = [];
let mediaFilter = '';
let sourceFilter = '';
let assetPage = 1;

const ASSET_MIN_CARD_WIDTH = 240;
const ASSET_GRID_GAP = 14;
const ASSET_ROWS_PER_PAGE = 2;

function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

function typeLabel(asset) {
  if (isImageAsset(asset)) return t('assets.image');
  if (isVideoAsset(asset)) return t('assets.video');
  return asset.media_type || '-';
}

function sourceLabel(asset) {
  if (asset.source === 'generated') return t('assets.generated');
  if (asset.source === 'upload') return t('assets.upload');
  return asset.source || '-';
}

function filteredAssets() {
  return allAssets.filter((asset) => {
    if (mediaFilter === 'image' && !isImageAsset(asset)) return false;
    if (mediaFilter === 'video' && !isVideoAsset(asset)) return false;
    if (sourceFilter && asset.source !== sourceFilter) return false;
    return true;
  });
}

function pagerLabels() {
  return {
    prev: t('common.prev'),
    next: t('common.next'),
    status: t('common.pageStatus'),
  };
}

function assetKindBadge(asset) {
  const node = badge(safeText(typeLabel(asset), 24), isImageAsset(asset) ? 'info' : 'violet');
  node.classList.add('asset-kind-badge');
  return node;
}

function assetSourceBadge(asset) {
  const node = badge(safeText(sourceLabel(asset), 24), 'muted');
  node.classList.add('asset-source-badge');
  return node;
}

function previewFallback() {
  return el('span', { class: 'asset-preview-fallback' }, t('assets.unavailable'));
}

function maxAssetColumns() {
  const contentWidth = document.getElementById('content')?.clientWidth || window.innerWidth || 1024;
  const gridWidth = Math.max(ASSET_MIN_CARD_WIDTH, contentWidth - 64);
  return Math.max(1, Math.floor((gridWidth + ASSET_GRID_GAP) / (ASSET_MIN_CARD_WIDTH + ASSET_GRID_GAP)));
}

function assetPageSize() {
  return maxAssetColumns() * ASSET_ROWS_PER_PAGE;
}

function assetGrid(items, reload) {
  const columns = maxAssetColumns();
  const node = el('div', { class: 'asset-grid' }, items.map((asset) => assetCard(asset, reload)));
  node.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  return node;
}

function previewNode(asset) {
  const href = safeAssetHref(asset.url_path);
  if (!href) {
    return el('div', { class: 'asset-thumb asset-thumb-unavailable' },
      previewFallback(),
      assetSourceBadge(asset),
      assetKindBadge(asset),
    );
  }
  if (isImageAsset(asset)) {
    let frame;
    const image = el('img', {
      src: href,
      alt: assetDisplayName(asset),
      loading: 'lazy',
      onerror: () => {
        image.remove();
        frame.classList.add('asset-thumb-unavailable');
        frame.prepend(previewFallback());
      },
    });
    frame = el('a', { class: 'asset-thumb', href, target: '_blank', rel: 'noopener noreferrer' },
      image,
      assetSourceBadge(asset),
      assetKindBadge(asset),
    );
    return frame;
  }
  if (isVideoAsset(asset)) {
    let frame;
    const video = el('video', {
      src: href,
      controls: true,
      preload: 'metadata',
      onerror: () => {
        video.remove();
        frame.classList.add('asset-thumb-unavailable');
        frame.prepend(previewFallback());
      },
    });
    frame = el('div', { class: 'asset-thumb' },
      video,
      assetSourceBadge(asset),
      assetKindBadge(asset),
    );
    return frame;
  }
  return el('div', { class: 'asset-thumb asset-thumb-unavailable' },
    previewFallback(),
    assetSourceBadge(asset),
    assetKindBadge(asset),
  );
}

function assetTags(asset) {
  const tags = [
    asset.provider ? { value: safeText(asset.provider, 96), tone: 'info', className: 'asset-provider-tag' } : null,
    asset.model ? { value: safeText(asset.model, 96), tone: 'violet', className: 'asset-model-tag' } : null,
  ].filter(Boolean);
  return el('div', { class: 'asset-tags' }, tags.map(({ value, tone, className }) => {
    const node = badge(value, tone);
    node.classList.add(className);
    node.title = value;
    return node;
  }));
}

function assetActions(asset, reload) {
  const href = safeAssetHref(asset.url_path);
  const actions = [];
  if (href) {
    actions.push(linkButton(t('common.download'), href, {
      size: 'sm',
      variant: 'primary',
      download: buildAssetDownloadName(asset),
    }));
    actions.push(linkButton(t('common.open'), href, { size: 'sm', target: '_blank' }));
  } else {
    actions.push(button(t('common.download'), {
      size: 'sm',
      onClick: () => toast(t('assets.downloadUnavailable'), 'error'),
    }));
  }
  actions.push(button(t('common.delete'), {
    size: 'sm',
    variant: 'danger',
    onClick: () => confirmModal({
      title: t('assets.deleteTitle'),
      message: t('assets.deleteMessage'),
      confirmLabel: t('common.delete'),
      cancelLabel: t('common.cancel'),
      danger: true,
      onConfirm: async () => {
        try {
          await api.delete(`/assets/${encodeURIComponent(asset.id)}`);
          toast(t('assets.deleteSuccess'), 'success');
          await reload();
        } catch (_) {
          toast(t('assets.deleteError'), 'error');
        }
      },
    }),
  }));
  return el('div', { class: 'action-row' }, actions);
}

function assetCard(asset, reload) {
  const title = assetDisplayName(asset);
  const prompt = asset.prompt ? safeText(asset.prompt, 600) : '';
  return el('article', { class: 'asset-card' },
    previewNode(asset),
    el('div', { class: 'asset-card-content' },
      el('div', { class: 'asset-card-header' },
        el('div', {},
          el('p', { class: 'card-title asset-card-title', title }, title),
          el('p', { class: 'card-subtitle asset-date' }, formatDate(asset.created_at)),
        ),
      ),
      assetTags(asset),
      metaGrid([
        { label: t('assets.size'), value: formatBytes(asset.size) },
        { label: t('assets.jobId'), value: asset.job_id ? shortId(asset.job_id) : '-' },
        { label: t('generateImage.duration'), value: asset.duration_ms ? formatDuration(asset.duration_ms) : '-' },
      ]),
      el('p', {
        class: 'card-subtitle prompt asset-prompt',
        title: prompt,
        'aria-hidden': prompt ? null : 'true',
      }, prompt),
    ),
    assetActions(asset, reload),
  );
}

function renderAssets(content, reload) {
  const assets = filteredAssets();
  const pageSize = assetPageSize();
  const paged = pageSlice(assets, assetPage, pageSize);
  assetPage = paged.current;
  const imageCount = allAssets.filter(isImageAsset).length;
  const videoCount = allAssets.filter(isVideoAsset).length;
  const totalBytes = allAssets.reduce((sum, item) => sum + (Number(item.size) || 0), 0);

  mount(content,
    pageHeader({
      kicker: t('assets.kicker'),
      title: t('assets.title'),
      subtitle: t('assets.subtitle'),
      actions: [button(t('common.refresh'), { onClick: reload })],
    }),
    el('div', { class: 'metric-grid' },
      metricCard({ label: t('assets.title'), value: String(allAssets.length), meta: t('assets.allTypes'), tone: 'teal' }),
      metricCard({ label: t('assets.image'), value: String(imageCount), meta: t('assets.generated'), tone: 'blue' }),
      metricCard({ label: t('assets.video'), value: String(videoCount), meta: t('assets.upload'), tone: 'violet' }),
      metricCard({ label: t('assets.size'), value: formatBytes(totalBytes), meta: t('assets.source'), tone: 'gold' }),
    ),
    panel({},
      el('div', { class: 'asset-toolbar' },
        el('div', { class: 'asset-filters' },
          select([
            { value: '', label: t('assets.allTypes') },
            { value: 'image', label: t('assets.image') },
            { value: 'video', label: t('assets.video') },
          ], {
            value: mediaFilter,
            onchange: (event) => {
              mediaFilter = event.target.value;
              assetPage = 1;
              renderAssets(content, reload);
            },
          }),
          select([
            { value: '', label: t('assets.allSources') },
            { value: 'generated', label: t('assets.generated') },
            { value: 'upload', label: t('assets.upload') },
          ], {
            value: sourceFilter,
            onchange: (event) => {
              sourceFilter = event.target.value;
              assetPage = 1;
              renderAssets(content, reload);
            },
          }),
        ),
        el('div', { class: 'action-row' },
          badge(`${assets.length} / ${allAssets.length}`, 'muted'),
          el('span', { class: 'asset-wip-note', title: t('assets.editUnavailable') }, badge('WIP', 'warning')),
        ),
      ),
      assets.length ?
        assetGrid(paged.items, reload) :
        el('div', { class: 'panel-body asset-empty' }, emptyState(t('assets.empty'))),
      paginationBar({
        page: assetPage,
        total: assets.length,
        pageSize,
        labels: pagerLabels(),
        onPage: (page) => {
          assetPage = page;
          renderAssets(content, reload);
        },
      }),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');

  async function reload() {
    mount(content, loadingState(t('assets.loading')));
    try {
      const result = await api.get('/assets?limit=100&offset=0');
      allAssets = dataArray(result);
      assetPage = clampPage(assetPage, filteredAssets().length, assetPageSize());
      renderAssets(content, reload);
    } catch (_) {
      mount(content,
        pageHeader({ kicker: t('assets.kicker'), title: t('assets.title'), subtitle: t('assets.subtitle') }),
        errorState(t('assets.error')),
      );
    }
  }

  await reload();
}

import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button, linkButton } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { select } from '../../components/forms.js';
import { layerGroupHref, layerLabel } from '../../components/layer-group.js';
import { confirmModal } from '../../components/modal.js?v=web-studio-2j';
import { clampPage, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel, metricCard, metaGrid } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { assetDisplayName, buildAssetDownloadName, isImageAsset, isVideoAsset, safeAssetHref } from '../../lib/asset-url.js';
import { formatBytes, formatDate, formatDuration, shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';
import { displayJobProvider, displayJobStage } from '../../lib/job-display.js';
import { navigate, createRenderScope } from '../../router.js';

let pageAssets = [];
let assetTotal = 0;
let assetSummary = {};
let mediaFilter = '';
let sourceFilter = '';
let jobFilter = '';
let assetPage = 1;

const ASSET_MIN_CARD_WIDTH = 240;
const ASSET_GRID_GAP = 14;
const ASSET_ROWS_PER_PAGE = 2;

function readAssetPage(result) {
  const summary = result?.summary;
  if (!Array.isArray(result?.data) || !summary ||
      !['total', 'image', 'video', 'size_bytes'].every(key => Number.isSafeInteger(summary[key]) && summary[key] >= 0) ||
      summary.total !== result.total || summary.image + summary.video !== result.total) {
    throw new Error('invalid_asset_page');
  }
  return result;
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
  const groupHref = layerGroupHref(asset.layer_group);
  if (groupHref) {
    return el('a', { class: 'asset-thumb asset-layer-entry', href: groupHref },
      el('strong', {}, layerLabel(asset.layer_group.z_index)),
      assetSourceBadge(asset), assetKindBadge(asset),
    );
  }
  const href = api.assetUrl(safeAssetHref(asset.url_path), { preview: true });
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
    asset.provider ? { value: safeText(displayJobProvider(asset.provider), 96), tone: 'info', className: 'asset-provider-tag' } : null,
    asset.model ? { value: safeText(asset.model, 96), tone: 'violet', className: 'asset-model-tag' } : null,
  ].filter(Boolean);
  return el('div', { class: 'asset-tags' }, tags.map(({ value, tone, className }) => {
    const node = badge(value, tone);
    node.classList.add(className);
    node.title = value;
    return node;
  }));
}

function openJobFromAsset(asset) {
  const jobId = asset?.job?.job_id || asset?.job_id;
  if (!jobId) return;
  sessionStorage.setItem('studio_open_job_id', jobId);
  navigate('#/jobs');
}

function assetJobStrip(asset) {
  const job = asset.job || null;
  const generation = asset.generation || null;
  const stage = ['queued', 'running'].includes(job?.status) && job.stage;
  return el('div', { class: 'asset-job-strip' },
    el('div', { class: 'asset-job-meta truncate' },
      el('span', { class: 'eyebrow' }, t('assets.linkedJob')),
      el('strong', { class: 'truncate' }, job?.job_id ? shortId(job.job_id) : t('assets.legacy')),
      stage ? el('p', { class: 'card-subtitle truncate' }, safeText(displayJobStage(stage), 80)) : null,
    ),
    el('div', { class: 'asset-job-badges' },
      job?.status ? badge(t(`jobs.${job.status}`), job.status === 'failed' ? 'danger' : 'muted') : badge(t('assets.legacy'), 'muted'),
      generation?.id ? badge(`${t('assets.generation')} ${shortId(generation.id)}`, 'info') : null,
    ),
  );
}

function assetActions(asset, reload) {
  const groupHref = layerGroupHref(asset.layer_group);
  const actions = [];
  if (asset.job?.job_id || asset.job_id) {
    actions.push(button(t('assets.viewJob'), {
      size: 'sm',
      variant: groupHref ? 'secondary' : 'primary',
      onClick: () => openJobFromAsset(asset),
    }));
  }
  if (groupHref) {
    return el('div', { class: 'action-row asset-card-actions' },
      linkButton(t('layers.open'), groupHref, { size: 'sm', variant: 'primary' }), actions,
    );
  }
  const href = api.assetUrl(safeAssetHref(asset.url_path));
  if (href) {
    actions.push(linkButton(t('common.download'), href, {
      size: 'sm',
      variant: asset.job?.job_id || asset.job_id ? 'secondary' : 'primary',
      download: buildAssetDownloadName(asset),
    }));
    const previewHref = api.assetUrl(safeAssetHref(asset.url_path), { preview: true });
    actions.push(linkButton(t('assets.openPreview'), previewHref, { size: 'sm', target: '_blank' }));
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
      errorMessage: t('assets.deleteError'),
      onConfirm: async ({ isActive }) => {
        await api.delete(`/assets/${encodeURIComponent(asset.id)}`);
        if (!isActive()) return;
        toast(t('assets.deleteSuccess'), 'success');
        await reload();
      },
    }),
  }));
  return el('div', { class: 'action-row asset-card-actions' }, actions);
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
      assetJobStrip(asset),
      metaGrid([
        { label: t('assets.size'), value: formatBytes(asset.size) },
        { label: t('assets.jobId'), value: asset.job_id ? shortId(asset.job_id) : '-' },
        { label: t('assets.generation'), value: asset.generation?.id ? shortId(asset.generation.id) : '-' },
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

function renderAssets(content, reload, pageSize) {
  const assets = pageAssets;
  const imageCount = assetSummary.image;
  const videoCount = assetSummary.video;
  const totalBytes = assetSummary.size_bytes;

  mount(content,
    pageHeader({
      kicker: t('assets.kicker'),
      title: t('assets.title'),
      subtitle: t('assets.subtitle'),
      actions: [button(t('common.refresh'), { onClick: reload })],
    }),
    el('div', { class: 'metric-grid list-metrics' },
      metricCard({ label: t('assets.title'), value: String(assetTotal), meta: t('assets.resultScope'), tone: 'teal' }),
      metricCard({ label: t('assets.image'), value: String(imageCount), meta: t('assets.resultScope'), tone: 'blue' }),
      metricCard({ label: t('assets.video'), value: String(videoCount), meta: t('assets.resultScope'), tone: 'violet' }),
      metricCard({ label: t('assets.size'), value: formatBytes(totalBytes), meta: t('assets.resultScope'), tone: 'gold' }),
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
            ariaLabel: t('assets.typeFilter'),
            onchange: (event) => {
              mediaFilter = event.target.value;
              assetPage = 1;
              reload();
            },
          }),
          select([
            { value: '', label: t('assets.allSources') },
            { value: 'generated', label: t('assets.generated') },
            { value: 'upload', label: t('assets.upload') },
          ], {
            value: sourceFilter,
            ariaLabel: t('assets.sourceFilter'),
            onchange: (event) => {
              sourceFilter = event.target.value;
              assetPage = 1;
              reload();
            },
          }),
        ),
        el('div', { class: 'action-row' },
          badge(t('assets.pageCount').replace('{count}', String(assets.length)).replace('{total}', String(assetTotal)), 'muted'),
          jobFilter ? badge(`${t('assets.jobFilter')} ${shortId(jobFilter)}`, 'info') : null,
          jobFilter ? button(t('common.clear'), {
            size: 'sm',
            onClick: async () => {
              jobFilter = '';
              assetPage = 1;
              await reload();
            },
          }) : null,
        ),
      ),
      assets.length ?
        assetGrid(assets, reload) :
        el('div', { class: 'panel-body asset-empty' }, emptyState(t('assets.empty'))),
      paginationBar({
        page: assetPage,
        total: assetTotal,
        pageSize,
        labels: pagerLabels(),
        onPage: (page) => {
          assetPage = page;
          reload();
        },
      }),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  const pendingJobFilter = sessionStorage.getItem('studio_asset_filter_job_id');
  if (pendingJobFilter) {
    sessionStorage.removeItem('studio_asset_filter_job_id');
    jobFilter = pendingJobFilter;
    assetPage = 1;
  }
  let pageSize = assetPageSize();
  let requestVersion = 0;
  let disposed = false;
  const active = () => !disposed && scope.isActive();
  // 观察稳定的页面容器，加载中也能响应旋转；离页后不渲染迟到结果。
  const observer = new ResizeObserver(() => {
    if (!active()) return;
    const nextSize = assetPageSize();
    if (nextSize !== pageSize) {
      assetPage = Math.floor((assetPage - 1) * pageSize / nextSize) + 1;
      pageSize = nextSize;
      reload();
    }
  });
  observer.observe(content);
  window.addEventListener('hashchange', () => {
    disposed = true;
    requestVersion += 1;
    observer.disconnect();
  }, { once: true });

  async function reload() {
    if (!active()) return;
    const version = ++requestVersion;
    const limit = pageSize;
    let page = assetPage;
    const filters = { media_type: mediaFilter, source: sourceFilter, job_id: jobFilter };
    mount(content, loadingState(t('assets.loading')));
    try {
      // 库存缩减导致页越界时最多补查一次，避免持续写入期间无限重试。
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const params = new URLSearchParams({ limit: String(limit), offset: String((page - 1) * limit) });
        Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
        const result = readAssetPage(await api.get(`/assets?${params.toString()}`));
        if (!active() || version !== requestVersion) return;
        const boundedPage = clampPage(page, result.total, limit);
        if (boundedPage !== page) {
          page = boundedPage;
          continue;
        }
        assetPage = page;
        pageAssets = result.data;
        assetTotal = result.total;
        assetSummary = result.summary;
        renderAssets(content, reload, limit);
        return;
      }
      throw new Error('asset_page_changed');
    } catch (_) {
      if (!active() || version !== requestVersion) return;
      mount(content,
        pageHeader({ kicker: t('assets.kicker'), title: t('assets.title'), subtitle: t('assets.subtitle'),
          actions: [button(t('common.refresh'), { onClick: reload })] }),
        errorState(t('assets.error')),
      );
    }
  }

  await reload();
}

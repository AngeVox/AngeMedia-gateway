import { api } from '../api.js';
import { t } from '../i18n.js';
import { buildAssetDownloadName, safeAssetHref } from '../lib/asset-url.js';
import { formatBytes } from '../lib/format.js';
import { safeText } from '../lib/security.js';
import { bindViewResources, createRenderScope } from '../router.js';
import { button, linkButton } from './buttons.js';
import { el, mount } from './dom.js';
import { confirmModal } from './modal.js?v=web-studio-2j';
import { emptyState, errorState, loadingState } from './states.js';

const idPattern = /^[a-f0-9]{32}$/;

export function layerGroupHref(group) {
  return idPattern.test(group?.job_id) && Number.isInteger(group.z_index) && group.z_index >= 0 && group.z_index < 100
    ? `#/assets/layers/${group.job_id}?layer=${group.z_index}`
    : '';
}

export function layerLabel(index) {
  return index === 0 ? t('layers.base') : t('layers.member').replace('{number}', String(index));
}

export function layerJobResult(job) {
  if (job.status === 'succeeded') return layerGroupPreview(job.job_id);
  const key = {
    queued: 'queued',
    running: job.stage === 'asset_import' ? 'saving' : 'generating',
    canceled: 'canceled',
    failed: {
      image_layer_submit_unknown: 'submitUnknown',
      image_layer_links_expired: 'expired',
    }[job.error_code] || 'failed',
  }[job.status] || 'unavailable';
  return el('p', { class: 'job-detail-line' }, t(`layers.${key}`));
}

function readMembers(data, jobId) {
  const members = data?.members;
  if (data?.version !== 1 || data.job_id !== jobId ||
      !Array.isArray(members) || members.length < 2 || members.length > 100) {
    throw new Error('invalid_layer_manifest');
  }
  // 组内链接只接收清单指定任务的本地成员，序号也用于排序和下载命名。
  return members.map((member, index) => {
    const format = member?.output_format;
    const extension = member?.output_format === 'jpeg' && index === 0 ? 'jpg' : 'png';
    const stem = String(index).padStart(3, '0');
    const href = safeAssetHref(member?.url);
    const expected = `/generated/.image-layers/${jobId}/${stem}.${extension}`;
    if (member?.z_index !== index || !idPattern.test(member.asset_id) || href !== expected ||
        (format !== 'png' && !(index === 0 && format === 'jpeg')) ||
        !Number.isSafeInteger(member.width) || member.width < 1 ||
        !Number.isSafeInteger(member.height) || member.height < 1) {
      throw new Error('invalid_layer_member');
    }
    return { ...member, href, label: layerLabel(index), filename: buildAssetDownloadName({
      url_path: href, display_name: `${jobId.slice(0, 8)}-layer-${stem}`,
    }) };
  });
}

function memberPreview(member, isCurrent) {
  const description = safeText(member.description, 360);
  const name = safeText(member.name, 120);
  const text = safeText(member.text_recognition?.text, 8000);
  const details = el('div', { class: 'layer-preview-details' },
    el('strong', {}, name || member.label),
    el('p', { class: 'card-subtitle' }, `${member.width} × ${member.height} · ${formatBytes(member.bytes)}`),
    description ? el('p', { class: 'job-detail-line' }, description) : null,
    text ? el('details', { class: 'layer-ocr' },
      el('summary', {}, t('layers.recognizedText')), el('p', { class: 'job-detail-line' }, text),
    ) : null,
  );
  const preview = el('div', { class: 'detail-preview layer-preview-image' });
  const actions = el('div', { class: 'action-row' });
  const root = el('div', { class: 'layer-preview' }, preview, details, actions);
  let controller, objectUrl;

  function release() {
    controller?.abort();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    mount(preview);
    mount(actions);
  }
  function unavailable() {
    release();
    mount(preview, el('p', { class: 'card-subtitle', role: 'status' }, t('assets.unavailable')));
  }
  async function load() {
    if (!resources.isActive()) return;
    controller = new AbortController();
    const { signal } = controller;
    mount(preview, loadingState(t('layers.previewLoading')));
    try {
      const blob = await api.assetBlob(member.href, { signal });
      if (signal.aborted || !resources.isActive()) return;
      if (blob?.type !== `image/${member.output_format}`) return unavailable();
      objectUrl = URL.createObjectURL(blob);
      const download = linkButton(t('common.download'), objectUrl, { download: member.filename, size: 'sm' });
      download.setAttribute('aria-label', t('layers.downloadMember').replace('{member}', member.label));
      const image = el('img', {
        src: objectUrl, alt: name || member.label,
        onload: () => { if (!signal.aborted && resources.isActive()) mount(actions, download); },
        onerror: () => { if (!signal.aborted && resources.isActive()) unavailable(); },
      });
      mount(preview, image);
    } catch (_) {
      if (!signal.aborted && resources.isActive()) unavailable();
    }
  }
  // 只取当前选中的一张图。切层或离开后中止旧请求并释放对象 URL。
  const resources = bindViewResources(() => root.isConnected && isCurrent(), {
    dispose: release, suspend: release, resume: load,
  });
  queueMicrotask(load);
  return root;
}

function gallery(members, selectedIndex, remove, isCurrent) {
  const preview = el('div', { class: 'layer-preview-slot' });
  const choices = members.map((member, index) => {
    const name = safeText(member.name, 80);
    const choice = button(name ? `${member.label} · ${name}` : member.label, {
      size: 'sm', onClick: () => selectMember(index),
    });
    return el('li', {}, choice);
  });
  function selectMember(index) {
    choices.forEach((choice, position) => choice.firstChild.setAttribute('aria-pressed', String(index === position)));
    mount(preview, memberPreview(members[index], isCurrent));
  }
  selectMember(Number.isInteger(selectedIndex) && members[selectedIndex] ? selectedIndex : 0);
  return [
    el('div', { class: 'layer-gallery' },
      el('ol', { class: 'layer-gallery-list', 'aria-label': t('layers.order') }, choices),
      preview,
    ),
    el('div', { class: 'action-row layer-group-actions' },
      button(t('layers.delete'), { variant: 'danger', size: 'sm', onClick: event => remove(event.currentTarget) }),
    ),
  ];
}

export function layerGroupPreview(jobId, selectedIndex = 0) {
  const root = el('div', { class: 'layer-group' });
  const scope = createRenderScope(root);
  const endpoint = `/admin/jobs/${encodeURIComponent(jobId)}/layers`;
  const unavailable = () => mount(root, emptyState(t('layers.unavailable')));
  const remove = (trigger) => confirmModal({
    title: t('layers.delete'), message: t('layers.deleteMessage'),
    confirmLabel: t('layers.delete'), cancelLabel: t('common.cancel'), danger: true,
    errorMessage: t('layers.deleteError'),
    onConfirm: async () => {
      if (!scope.isActive()) return;
      trigger.disabled = true;
      try {
        await api.delete(endpoint);
        // 确认窗口可提前关闭，服务端已执行时仍更新当前预览。
        if (scope.isActive()) {
          mount(root, emptyState(t('layers.deleted')));
          root.tabIndex = -1;
          requestAnimationFrame(() => { if (scope.isActive()) root.focus(); });
        }
      } finally {
        trigger.disabled = false;
      }
    },
  });
  async function load() {
    if (!scope.isActive()) return;
    if (!idPattern.test(jobId)) return unavailable();
    const current = scope.nextRequest();
    root.setAttribute('aria-busy', 'true');
    mount(root, loadingState(t('layers.loading')));
    try {
      const result = await api.get(endpoint, { cache: 'no-store' });
      if (current()) mount(root, gallery(readMembers(result?.data, jobId), selectedIndex, remove, current));
    } catch (error) {
      if (!current()) return;
      if (error?.safe?.code === 'layer_group_unavailable') return unavailable();
      mount(root, errorState(t('layers.loadError')), button(t('common.refresh'), { size: 'sm', onClick: load }));
    } finally {
      if (current()) root.removeAttribute('aria-busy');
    }
  }
  queueMicrotask(load);
  return root;
}

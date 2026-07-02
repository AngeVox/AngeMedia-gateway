import { api } from '../api.js';
import { t } from '../i18n.js';
import { badge } from '../components/badges.js';
import { button } from '../components/buttons.js';
import { el, mount } from '../components/dom.js';
import { pageHeader, panel, metaGrid } from '../components/page.js';
import { emptyState, errorState, loadingState } from '../components/states.js';
import { formatDate, formatDuration, shortId } from '../lib/format.js';
import {
  displayJobErrorCategory,
  displayJobEventType,
  displayJobProviderStatus,
  displayJobStage,
  displayJobSummaryKey,
  displayJobSummaryValue,
} from '../lib/job-display.js';
import { safeText } from '../lib/security.js';
import { navigate } from '../router.js';

function summaryList(summary) {
  const entries = Object.entries(summary || {}).filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!entries.length) return emptyState(t('jobs.noSummary'));
  return el('dl', { class: 'job-summary-list' }, entries.slice(0, 12).map(([key, value]) => [
    el('dt', {}, safeText(displayJobSummaryKey(key), 60)),
    el('dd', {}, safeText(displayJobSummaryValue(key, value), 140)),
  ]));
}

function eventList(items, emptyKey) {
  if (!Array.isArray(items) || !items.length) return emptyState(t(emptyKey));
  return el('div', { class: 'job-event-list' }, items.slice(-12).reverse().map((item) =>
    el('article', { class: 'job-event-item' },
      el('div', { class: 'job-event-title' },
        el('strong', {}, safeText(displayJobEventType(item.event_type || item.status), 80)),
        item.stage ? badge(displayJobStage(item.stage), 'muted') : null,
      ),
      el('p', { class: 'card-subtitle' }, formatDate(item.created_at || item.started_at)),
      item.error_message ? el('p', { class: 'job-detail-line' }, safeText(item.error_message, 180)) : null,
    ),
  ));
}

function diagnostics(detail) {
  if (!detail.error_message && !detail.human_hint && !detail.error_category) return emptyState(t('jobs.noDiagnostics'));
  return el('div', { class: 'job-diagnostics job-detail-diagnostics' },
    detail.human_hint ? el('p', { class: 'job-hint' }, safeText(detail.human_hint, 180)) : null,
    el('div', { class: 'action-row' },
      detail.error_category ? badge(displayJobErrorCategory(detail.error_category), 'danger') : null,
      detail.retryable === true ? badge(t('jobs.retryable'), 'warning') : null,
      detail.retryable === false ? badge(t('jobs.notRetryable'), 'muted') : null,
      detail.gateway_stage ? badge(displayJobStage(detail.gateway_stage), 'info') : null,
    ),
    detail.error_message ? el('p', { class: 'job-detail-line' }, safeText(detail.error_message, 260)) : null,
  );
}

function assetLinks(detail) {
  const assets = Array.isArray(detail.assets) ? detail.assets : [];
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

function renderDetail(content, detail) {
  mount(content,
    pageHeader({
      kicker: t('jobs.kicker'),
      title: `${t('jobs.detail')} · ${shortId(detail.job_id)}`,
      subtitle: formatDate(detail.created_at),
      actions: [
        button(t('jobs.title'), { onClick: () => navigate('#/jobs') }),
        button(t('assets.title'), {
          onClick: () => {
            sessionStorage.setItem('studio_asset_filter_job_id', detail.job_id);
            navigate('#/assets');
          },
        }),
      ],
    }),
    el('div', { class: 'detail-page' },
      panel({ title: t('jobs.summary'), className: 'detail-section' },
        metaGrid([
          { label: t('jobs.status'), value: detail.status ? t(`jobs.${detail.status}`) : '-' },
          { label: t('jobs.stage'), value: safeText(displayJobStage(detail.stage), 80) },
          { label: t('jobs.provider'), value: safeText(detail.provider || '-', 80) },
          { label: t('jobs.model'), value: safeText(detail.model || '-', 80) },
          { label: t('jobs.duration'), value: formatDuration(detail.duration_ms) },
          { label: t('jobs.providerStatus'), value: safeText(displayJobProviderStatus(detail.provider_status), 80) },
        ]),
      ),
      el('div', { class: 'detail-grid' },
        panel({ title: t('jobs.diagnostics'), className: 'detail-section' }, diagnostics(detail)),
        panel({ title: t('jobs.prompt'), className: 'detail-section' },
          el('p', { class: 'job-detail-line' }, safeText(detail.prompt_summary || '-', 260)),
        ),
        panel({ title: t('jobs.inputSummary'), className: 'detail-section' }, summaryList(detail.input_summary)),
        panel({ title: t('jobs.outputSummary'), className: 'detail-section' }, summaryList(detail.output_summary)),
        panel({ title: t('jobs.assets'), className: 'detail-section' }, assetLinks(detail)),
        panel({ title: t('jobs.generation'), className: 'detail-section' }, summaryList(detail.generation)),
        panel({ title: t('jobs.events'), className: 'detail-section' }, eventList(detail.events, 'jobs.noEvents')),
        panel({ title: t('jobs.attempts'), className: 'detail-section' }, eventList(detail.attempts, 'jobs.noAttempts')),
      ),
    ),
  );
}

export async function render(params = {}) {
  const content = document.getElementById('content');
  if (!params.id) {
    mount(content,
      pageHeader({ kicker: t('jobs.kicker'), title: t('jobs.detail'), subtitle: t('detail.missingId') }),
      panel({ className: 'detail-section' }, emptyState(t('detail.missingId'), t('detail.missingIdCopy'))),
    );
    return;
  }
  mount(content, loadingState(t('jobs.detailLoading')));
  try {
    const result = await api.get(`/admin/jobs/${encodeURIComponent(params.id)}`);
    renderDetail(content, result?.data || {});
  } catch (_) {
    mount(content,
      pageHeader({
        kicker: t('jobs.kicker'),
        title: t('jobs.detail'),
        subtitle: shortId(params.id),
        actions: [button(t('jobs.title'), { onClick: () => navigate('#/jobs') })],
      }),
      panel({ className: 'detail-section' }, errorState(t('jobs.detailError'), t('detail.notFound'))),
    );
  }
}

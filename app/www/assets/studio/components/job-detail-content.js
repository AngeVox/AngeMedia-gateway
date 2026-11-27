import { t } from '../i18n.js';
import { displayJobErrorCategory, displayJobErrorHint, displayJobEventType, displayJobProvider, displayJobProviderStatus,
  displayJobStage, displayJobSummaryKey, displayJobSummaryValue, showJobErrorMessage } from '../lib/job-display.js';
import { formatDate, formatDuration } from '../lib/format.js';
import { redactInlineSecret, safeText } from '../lib/security.js';
import { navigate } from '../router.js';
import { badge } from './badges.js';
import { button, linkButton } from './buttons.js';
import { el } from './dom.js';
import { metaGrid } from './page.js';
import { layerJobResult } from './layer-group.js';

function summaryList(summary) {
  const entries = Object.entries(summary || {}).filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!entries.length) return el('p', { class: 'card-subtitle' }, t('jobs.noSummary'));
  return el('dl', { class: 'job-summary-list' }, entries.slice(0, 12).flatMap(([key, value]) => [
    el('dt', {}, safeText(displayJobSummaryKey(key), 60)),
    el('dd', {}, safeText(displayJobSummaryValue(key, value), 140)),
  ]));
}

function eventList(items, emptyKey) {
  if (!Array.isArray(items) || !items.length) return el('p', { class: 'card-subtitle' }, t(emptyKey));
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
  const hint = displayJobErrorHint(detail);
  if (!detail.error_message && !hint && !detail.error_category) return null;
  return el('div', { class: 'job-diagnostics job-detail-diagnostics' },
    hint ? el('p', { class: 'job-hint' }, safeText(hint, 180)) : null,
    el('div', { class: 'action-row' },
      detail.error_category ? badge(displayJobErrorCategory(detail.error_category), 'danger') : null,
      detail.retryable === true ? badge(t('jobs.retryable'), 'warning') : null,
      detail.retryable === false ? badge(t('jobs.notRetryable'), 'muted') : null,
      detail.gateway_stage ? badge(displayJobStage(detail.gateway_stage), 'info') : null,
    ),
    showJobErrorMessage(detail) ? el('p', { class: 'job-detail-line' }, safeText(detail.error_message, 260)) : null,
  );
}

function assetLinks(detail) {
  const assets = Array.isArray(detail.assets) ? detail.assets : [];
  if (!assets.length) return el('p', { class: 'card-subtitle' }, t('jobs.noAssets'));
  return el('div', { class: 'job-asset-list' }, assets.map((asset) =>
    el('article', { class: 'job-asset-row' },
      el('div', {},
        el('strong', {}, safeText(asset.filename || asset.id || '-', 80)),
        el('p', { class: 'card-subtitle' }, `${safeText(displayJobSummaryValue('media_type', asset.media_type), 32)} · ${safeText(displayJobProvider(asset.provider) || '-', 60)}`),
      ),
      asset.id ? linkButton(t('jobs.viewAsset'), '#/assets/' + encodeURIComponent(asset.id), { size: 'sm', variant: 'primary' }) : button(t('jobs.viewAsset'), {
        size: 'sm', variant: 'primary',
        onClick: () => {
          sessionStorage.setItem('studio_asset_filter_job_id', detail.job_id);
          navigate('#/assets');
        },
      }),
    ),
  ));
}

// 抽屉和独立详情共用同一份展示；执行记录保留在原生折叠区，不复制业务判断。
export function jobDetailContent(detail, headingTag = 'h3') {
  const section = (title, body) => el('section', { class: 'job-detail-section' }, el(headingTag, {}, title), body);
  const layered = detail.operation_kind === 'image_layer_decomposition';
  const diagnostic = diagnostics(detail);
  const active = ['queued', 'running'].includes(detail.status);
  const processing = { stage: detail.stage, provider_status: detail.provider_status };
  return el('div', { class: 'job-detail-content' },
    section(t('jobs.summary'), metaGrid([
      { label: t('jobs.status'), value: detail.status ? t(`jobs.${detail.status}`) : '-' },
      { label: t('jobs.stage'), value: active && detail.stage ? safeText(displayJobStage(detail.stage), 80) : '' },
      { label: t('jobs.provider'), value: safeText(displayJobProvider(detail.provider) || '-', 80) },
      { label: t('jobs.model'), value: safeText(detail.model || '-', 80) },
      { label: t('jobs.duration'), value: formatDuration(detail.duration_ms) },
      { label: t('jobs.providerStatus'), value: active && detail.provider_status ? safeText(displayJobProviderStatus(detail.provider_status), 80) : '' },
    ])),
    detail.status === 'running' && detail.stage === 'video_submit' && !detail.provider_status
      ? el('p', { class: 'card-subtitle' }, t('jobs.waitingForVideoAcknowledgement')) : null,
    !layered && diagnostic ? section(t('jobs.diagnostics'), diagnostic) : null,
    layered ? section(t('layers.title'), layerJobResult(detail)) :
      (detail.assets?.length || detail.status === 'succeeded' ? section(t('jobs.assets'), assetLinks(detail)) : null),
    detail.prompt_summary ? section(t('jobs.promptExcerpt'), el('p', { class: 'job-detail-line job-prompt-excerpt' },
      redactInlineSecret(detail.prompt_summary))) : null,
    el('details', { class: 'job-technical-details' },
      el('summary', {}, t('jobs.technicalDetails')),
      layered && diagnostic ? section(t('jobs.diagnostics'), diagnostic) : null,
      section(t('jobs.inputSummary'), summaryList(detail.input_summary)),
      section(t('jobs.outputSummary'), summaryList(detail.output_summary)),
      section(t('jobs.generation'), summaryList(detail.generation)),
      !active && (processing.stage || processing.provider_status) ? section(t('jobs.processing'), summaryList(processing)) : null,
      section(t('jobs.events'), eventList(detail.events, 'jobs.noEvents')),
      section(t('jobs.attempts'), eventList(detail.attempts, 'jobs.noAttempts')),
    ),
  );
}

import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { linkButton } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { metaGrid } from '../../components/page.js';
import { emptyState, loadingState } from '../../components/states.js';
import { startJobResultTracker, stopJobResultTracker } from '../../components/job-result-tracker.js?v=web-studio-2j';
import { safeAssetHref, buildAssetDownloadName } from '../../lib/asset-url.js';
import { errorDiagnostics, safeErrorMessage } from '../../lib/safe-error.js';
import { formatDuration, truncateText } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

function imageUrlFromResult(result) {
  const item = Array.isArray(result?.data) ? result.data[0] : null;
  return item?.url || '';
}

export function renderResultEmpty(target) {
  stopJobResultTracker(target);
  mount(target, emptyState(t('generateImage.resultEmptyTitle')));
}

export function renderResultLoading(target) {
  stopJobResultTracker(target);
  mount(target, loadingState(t('generateImage.generating')));
}

export function renderResultQueued(target, result, prompt) {
  startJobResultTracker(target, {
    jobId: result.job_id,
    mediaType: 'image',
    prompt,
    initial: result,
  });
}

export function renderResultSuccess(target, result, prompt) {
  stopJobResultTracker(target);
  const imageUrl = safeAssetHref(imageUrlFromResult(result));
  const mediaUrl = api.assetUrl(imageUrl, { preview: true });
  const downloadUrl = api.assetUrl(imageUrl);
  const assetLike = {
    id: result.job_id || result.history_id || 'generated',
    prompt,
    filename: imageUrl,
    url_path: imageUrl,
    media_type: 'image',
  };

  const preview = mediaUrl ?
    el('div', { class: 'preview-box' }, el('img', { class: 'result-image', src: mediaUrl, alt: t('generateImage.previewAlt') })) :
    el('div', { class: 'preview-box' }, emptyState(t('generateImage.imageUnavailable')));

  mount(target,
    el('div', { class: 'result-success' },
      el('div', { class: 'job-card-header' },
        el('div', {},
          el('p', { class: 'card-title' }, t('generateImage.success')),
          el('p', { class: 'card-subtitle' }, truncateText(safeText(prompt, 240), 120)),
        ),
        badge(t('jobs.succeeded'), 'success'),
      ),
      preview,
      downloadUrl ? linkButton(t('generateImage.download'), downloadUrl, {
        variant: 'primary',
        download: buildAssetDownloadName(assetLike),
      }) : null,
      metaGrid([
        { label: t('generateImage.provider'), value: safeText(result.provider, 80) },
        { label: t('generateImage.model'), value: safeText(result.model, 80) },
        { label: t('generateImage.jobId'), value: result.job_id },
        { label: t('generateImage.historyId'), value: result.history_id },
        { label: t('generateImage.duration'), value: result.duration_ms ? formatDuration(result.duration_ms) : '' },
      ]),
    ),
  );
}

export function renderResultError(target, error) {
  stopJobResultTracker(target);
  const diagnostics = errorDiagnostics(error);
  const code = error?.safe?.code;
  const markupKey = typeof code === 'string' && code.startsWith('image_markup_') ? `generateImage.${code}` : '';
  const localized = markupKey ? t(markupKey) : '';
  const message = localized && localized !== markupKey ? localized
    : safeErrorMessage(error, error?.status === 409 ? t('generateImage.duplicate') : t('generateImage.error'));
  mount(target,
    el('div', { class: 'diagnostic-card' },
      el('p', { class: 'card-title' }, message),
      metaGrid([
        { label: t('jobs.errorCategory'), value: diagnostics.error_category },
        { label: t('jobs.gatewayStage'), value: diagnostics.gateway_stage },
        { label: t('jobs.retryable'), value: diagnostics.retryable === true ? t('jobs.retryable') : diagnostics.retryable === false ? t('jobs.notRetryable') : '' },
        { label: t('generateImage.jobId'), value: diagnostics.existing_job?.job_id },
      ]),
    ),
  );
}

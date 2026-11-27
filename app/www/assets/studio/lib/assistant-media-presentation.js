import { t } from '../i18n.js';
import { displayJobErrorHint } from './job-display.js';

export function isActionAsset(asset, job) {
  const path = asset?.url_path;
  return !!job && asset?.job_id === job.id && typeof path === 'string'
    && /^\/(?:generated|uploads)\/[A-Za-z0-9_./%\-]+$/.test(path)
    && !/(?:^|\/)\.{1,2}(?:\/|$)|%2e|%2f|%5c/i.test(path);
}

// 以当前任务事实选择一句说明，不凭时间推算百分比，也不把助手答复结束当成媒体完成。
export function mediaProgressKey(job, assets = []) {
  if (!job) return 'unknown';
  if (job.status === 'record_unavailable') return 'missing';
  if (job.status === 'succeeded') return assets.some((asset) => isActionAsset(asset, job)) ? 'saved' : 'completed';
  if (job.status === 'canceled') return 'canceled';
  const code = job.error_code;
  if (job.status === 'failed') {
    if (code === 'video_submit_queue_full') return 'queue_full';
    if (['ambiguous_submit', 'video_submit_ambiguous', 'video_submit_dispatch_pending'].includes(code) || job.error_category === 'ambiguous_submit') return 'uncertain';
    if (['video_poll_timeout', 'video_pipeline_timeout', 'image_poll_timeout', 'image_import_timeout'].includes(code)) return 'timeout';
    if (['video_asset_import_failed', 'video_result_missing_url', 'video_asset_url_rejected', 'asset_import_failed', 'download_failed'].includes(code)) return 'save_failed';
    if (code === 'action_execution_connection_changed') return 'connection';
    if (['action_execution_policy_denied', 'action_execution_reference_changed', 'action_execution_conditions_changed',
      'action_execution_receipt_changed', 'action_execution_job_changed', 'action_execution_target_changed',
      'action_execution_validation_failed'].includes(code) || job.error_category === 'action_authorization') return 'permission';
    const categories = { auth_failed: 'auth', model_unavailable: 'model', content_filtered: 'filtered',
      provider_rate_limited: 'quota', local_budget: 'budget' };
    return Object.hasOwn(categories, job.error_category) ? categories[job.error_category] : 'failed';
  }
  if (job.status === 'queued') return 'queued';
  if (job.status !== 'running') return 'unknown';
  if (job.stage === 'asset_import' || job.stage === 'image_import' || job.stage === 'download') return 'saving';
  if (job.stage === 'image_poll' || (job.stage === 'image_generate' && job.external_task_present)) return 'image_wait';
  if (job.stage === 'video_submit') return 'submitting';
  if (job.stage === 'video_poll') return code === 'video_poll_failed' ? 'poll_retry' : job.external_task_present ? 'video' : 'checking';
  return job.stage === 'image_generate' ? 'image' : 'running';
}

export function mediaProgress(job, assets) {
  const key = mediaProgressKey(job, assets);
  return key === 'queue_full' ? displayJobErrorHint(job) : t('assistant.media.' + key);
}

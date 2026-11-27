import { t } from '../i18n.js';
import { formatDate, formatDuration } from './format.js';

function humanize(value) {
  return String(value || '-').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim() || '-';
}

function label(group, value) {
  if (!value) return '-';
  const name = String(value).toLowerCase().replace(/\s+/g, '_');
  const key = `jobDisplay.${group}.${name}`;
  const translated = t(key);
  return translated === key ? humanize(value) : translated;
}

export function displayJobStage(value) {
  const key = String(value || '').toLowerCase();
  if (['image_poll', 'image_import'].includes(key)) return t('jobs.stage.' + key);
  return label('stages', value);
}

// 仅翻译显示名称，不改变路由使用的渠道标识或模型名称。
export function displayJobProvider(value) {
  const key = `jobDisplay.providers.${value}`;
  return t(key) === key ? value : t(key);
}

export function displayJobProviderStatus(value) {
  const key = String(value || '').toLowerCase();
  if (['succeed', 'pending', 'unknown'].includes(key)) return t('jobs.providerStatus.' + key);
  return label('stages', value);
}

export function displayJobEventType(value) {
  const key = String(value || '').toLowerCase();
  if (key === 'image_task_late_receipt') return t('jobs.event.' + key);
  return label('events', value);
}

export function displayJobErrorCategory(value) {
  const key = String(value || '').toLowerCase();
  if (['local_timeout', 'provider_identity'].includes(key)) return t('jobs.error.' + key);
  return label('errors', value);
}

export function displayJobErrorHint(job) {
  const key = `jobDisplay.hints.${job?.error_code || ''}`;
  const translated = t(key);
  return translated !== key ? translated : job?.human_hint || '';
}

export function showJobErrorMessage(job) {
  // 队列已满已有完整的本地化提示，技术记录仍保留诊断。
  return Boolean(job?.error_message) && job.error_code !== 'video_submit_queue_full';
}

export function displayJobSummaryKey(value) {
  if (['complexity', 'output_format', 'frame_count', 'frame_duration_ms', 'animation_duration_ms'].includes(value)) {
    return t('generateImage.param.' + value);
  }
  return label('summaryKeys', value);
}

export function displayJobSummaryValue(key, value) {
  if (value === null || value === undefined || value === '') return '-';
  const name = String(key || '').toLowerCase().replace(/\s+/g, '_');
  const text = String(value);
  if (name === 'complexity' && ['low', 'medium', 'high'].includes(text)) {
    return t(`generateImage.value.complexity.${text}`);
  }
  if (name === 'status') {
    if (['queued', 'running', 'succeeded', 'failed', 'canceled'].includes(text)) return t(`jobs.${text}`);
    if (text === 'completed') return label('values', text);
  }
  if (name === 'output_format' && ['gif', 'svg'].includes(text)) return t(`generateImage.value.output_format.${text}`);
  if (name === 'duration_ms' || name === 'animation_duration_ms') return formatDuration(value);
  if (['true', 'false'].includes(text)) return label('values', text);
  if (name === 'media_type') return label('media', text);
  if (name === 'input_mode' || name === 'operation') return label(text === 'auto' ? 'values' : 'inputModes', text);
  if (name === 'provider' || name === 'route_provider' || name === 'channel') return displayJobProvider(text);
  if (name === 'route_mode' || name === 'response_format') return label('values', text);
  if (name.endsWith('_at')) return formatDate(value);
  if (name.endsWith('_stage') || name === 'stage') return displayJobStage(text);
  if (name.endsWith('_status') || name === 'provider_status') return displayJobProviderStatus(text);
  if (name.endsWith('_category') || name === 'error_category') return displayJobErrorCategory(text);
  return text;
}

import { getLanguage, t } from '../i18n.js';

const LABELS = {
  zh: {
    summaryKeys: {
      id: '生成记录 ID',
      media_type: '媒体类型',
      status: '状态',
      provider: '渠道',
      request_model: '请求模型',
      model: '模型',
      input_mode: '输入模式',
      operation: '操作',
      duration_ms: '耗时',
      started_at: '开始时间',
      completed_at: '完成时间',
      created_at: '创建时间',
      updated_at: '更新时间',
      reference_count: '参考图数量',
      channel: '渠道',
      history_id: '历史记录 ID',
      image_count: '图片数量',
      has_url: '包含结果链接',
      filename: '文件名',
      url_path: '访问路径',
      job_id: '任务 ID',
      generation_id: '生成记录 ID',
    },
    stages: {
      image_generate: '图片生成',
      video_submit: '视频提交',
      video_poll: '视频轮询',
      asset_import: '资产导入',
      provider_request: '渠道请求',
      provider_response: '渠道响应',
      provider_poll: '渠道轮询',
      dedupe_admission: '重复请求检查',
      download: '下载本地化',
      finalize: '收尾',
      submitted: '已提交',
      queued: '排队中',
      running: '运行中',
      completed: '已完成',
      failed: '失败',
      poll_error: '轮询失败',
    },
    events: {
      admitted: '任务已接收',
      status_changed: '状态已更新',
      dispatch_claimed: '调度已领取',
      dispatch_published: '已投递 Worker',
      worker_attempt_started: 'Worker 开始处理',
      worker_attempt_succeeded: 'Worker 处理成功',
      worker_attempt_failed: 'Worker 处理失败',
      worker_attempt_resumed: 'Worker 恢复处理',
      worker_stage_scheduled: '已安排下一阶段',
      worker_stage_inflight: '阶段处理中',
      worker_duplicate_message: '重复消息已忽略',
      worker_terminal_message_rejected: '终态消息已拒绝',
      worker_video_finalized: '视频任务已完成',
      succeeded: '尝试成功',
      failed: '尝试失败',
      running: '尝试运行中',
    },
    errors: {
      ambiguous_submit: '提交结果不明确',
      unknown_provider_error: '未知渠道错误',
      provider_response: '渠道响应错误',
      provider_request: '渠道请求错误',
      provider_poll: '渠道轮询错误',
      provider_timeout: '渠道超时',
      timeout: '超时',
      validation_error: '参数校验失败',
      configuration_error: '配置错误',
      model_not_configured: '模型未配置',
      provider_not_configured: '渠道未配置',
      provider_disabled: '渠道已停用',
      download_failed: '下载失败',
      asset_import_failed: '资产导入失败',
      duplicate_request: '重复请求',
    },
    media: {
      image: '图片',
      video: '视频',
    },
    inputModes: {
      explicit_model: '显式选择模型',
      default_route: '默认路由',
      image_to_image: '图生图',
      text_to_image: '文生图',
      text_to_video: '文生视频',
      image_to_video: '图生视频',
    },
  },
  en: {
    summaryKeys: {
      id: 'Generation ID',
      media_type: 'Media type',
      status: 'Status',
      provider: 'Channel',
      request_model: 'Requested model',
      model: 'Model',
      input_mode: 'Input mode',
      operation: 'Operation',
      duration_ms: 'Duration',
      started_at: 'Started at',
      completed_at: 'Completed at',
      created_at: 'Created at',
      updated_at: 'Updated at',
      reference_count: 'Reference count',
      channel: 'Channel',
      history_id: 'History ID',
      image_count: 'Image count',
      has_url: 'Has result URL',
      filename: 'Filename',
      url_path: 'URL path',
      job_id: 'Job ID',
      generation_id: 'Generation ID',
    },
    stages: {},
    events: {},
    errors: {},
    media: {
      image: 'Image',
      video: 'Video',
    },
    inputModes: {
      explicit_model: 'Explicit model',
      default_route: 'Default route',
      image_to_image: 'Image to image',
      text_to_image: 'Text to image',
      text_to_video: 'Text to video',
      image_to_video: 'Image to video',
    },
  },
};

function lang() {
  return String(getLanguage() || '').startsWith('zh') ? 'zh' : 'en';
}

function table(name) {
  const current = LABELS[lang()]?.[name] || {};
  const fallback = LABELS.en[name] || {};
  return { ...fallback, ...current };
}

function humanize(value) {
  return String(value || '-')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim() || '-';
}

export function displayJobStage(value) {
  if (!value) return '-';
  const key = String(value).toLowerCase();
  return table('stages')[key] || humanize(key);
}

export function displayJobProviderStatus(value) {
  if (!value) return '-';
  const key = String(value).toLowerCase();
  return table('stages')[key] || humanize(key);
}

export function displayJobEventType(value) {
  if (!value) return '-';
  const key = String(value).toLowerCase();
  return table('events')[key] || humanize(key);
}

export function displayJobErrorCategory(value) {
  if (!value) return '-';
  const key = String(value).toLowerCase();
  return table('errors')[key] || humanize(key);
}

export function displayJobSummaryKey(value) {
  if (!value) return '-';
  const key = String(value).toLowerCase().replace(/\s+/g, '_');
  return table('summaryKeys')[key] || humanize(key);
}

export function displayJobSummaryValue(key, value) {
  if (value === null || value === undefined || value === '') return '-';
  const name = String(key || '').toLowerCase().replace(/\s+/g, '_');
  const text = String(value);
  if (name === 'status') {
    if (['queued', 'running', 'succeeded', 'failed', 'canceled'].includes(text)) return t(`jobs.${text}`);
    if (text === 'completed') return lang() === 'zh' ? '已完成' : 'Completed';
  }
  if (name === 'duration_ms') return `${text}ms`;
  if (text === 'true') return lang() === 'zh' ? '是' : 'Yes';
  if (text === 'false') return lang() === 'zh' ? '否' : 'No';
  if (name === 'media_type') return table('media')[text] || humanize(text);
  if (name === 'input_mode' || name === 'operation') return table('inputModes')[text] || humanize(text);
  if (name.endsWith('_stage') || name === 'stage') return displayJobStage(text);
  if (name.endsWith('_status') || name === 'provider_status') return displayJobProviderStatus(text);
  if (name.endsWith('_category') || name === 'error_category') return displayJobErrorCategory(text);
  return text;
}

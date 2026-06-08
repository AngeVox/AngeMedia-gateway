/**
 * 最小 i18n helper - zh-CN / en-US
 * localStorage 边界: 只允许 studio_lang (非敏感 UI 偏好)
 */

const STORAGE_KEY = 'studio_lang';

const zhCN = {
  // Navigation
  'nav.dashboard': '仪表盘',
  'nav.generateImage': '生成图片',
  'nav.generateVideo': '生成视频',
  'nav.jobs': '任务',
  'nav.assets': '资产',
  'nav.providers': '服务商',
  'nav.apiKeys': 'API 密钥',
  'nav.diagnostics': '诊断',

  // Topbar
  'topbar.studio': '工作台',
  'topbar.logout': '退出登录',

  // Login page
  'login.title': 'AngeMedia 工作台',
  'login.username': '用户名',
  'login.password': '密码',
  'login.button': '登录',
  'login.loggingIn': '登录中...',
  'login.failed': '登录失败',

  // Dashboard
  'dashboard.loading': '加载中...',
  'dashboard.health': '健康状态',
  'dashboard.session': '会话',
  'dashboard.statusPrefix': '状态：',
  'dashboard.loggedInPrefix': '当前用户：',
  'dashboard.notAuthenticated': '未登录',
  'dashboard.unableToLoadSession': '无法加载会话',
  'dashboard.error': '错误',
  'dashboard.unavailable': '不可用',
  'dashboard.unknown': '未知',

  // Generate Image
  'generateImage.title': '生成图片',
  'generateImage.prompt': '描述要生成的图片',
  'generateImage.promptPlaceholder': '例如：一只可爱的猫在花园里玩耍',
  'generateImage.promptRequired': '请输入图片描述',
  'generateImage.submit': '生成',
  'generateImage.generating': '生成中...',
  'generateImage.error': '生成失败',
  'generateImage.success': '生成成功',
  'generateImage.previewAlt': '生成的图片',
  'generateImage.imageUnavailable': '图片已生成，但预览不可用',
  'generateImage.duration': '耗时',
  'generateImage.provider': '服务商',
  'generateImage.model': '模型',

  // Jobs
  'jobs.title': '任务',
  'jobs.loading': '加载中...',
  'jobs.empty': '暂无任务',
  'jobs.error': '加载任务失败',
  'jobs.id': 'ID',
  'jobs.kind': '类型',
  'jobs.status': '状态',
  'jobs.created': '创建时间',
  'jobs.duration': '耗时',
  'jobs.provider': '服务商',
  'jobs.model': '模型',
  'jobs.errorCode': '错误码',
  'jobs.image': '图片',
  'jobs.video': '视频',
  'jobs.unknown': '未知',
  'jobs.queued': '排队中',
  'jobs.running': '运行中',
  'jobs.succeeded': '成功',
  'jobs.failed': '失败',
  'jobs.canceled': '已取消',

  // Assets
  'assets.title': '资产',
  'assets.loading': '加载中...',
  'assets.empty': '暂无资产',
  'assets.error': '加载资产失败',
  'assets.filename': '文件名',
  'assets.type': '类型',
  'assets.created': '创建时间',
  'assets.jobId': '任务 ID',
  'assets.size': '大小',
  'assets.source': '来源',
  'assets.preview': '预览',
  'assets.unavailable': '不可预览',
  'assets.image': '图片',
  'assets.video': '视频',
  'assets.unknown': '未知',
  'assets.generated': '生成',
  'assets.upload': '上传',

  // Gateway API Keys
  'apiKeys.title': 'API 密钥',
  'apiKeys.subtitle': '只读查看 Gateway API Key 元数据。完整密钥只会在创建时显示一次。',
  'apiKeys.loading': '加载中...',
  'apiKeys.empty': '暂无 API 密钥',
  'apiKeys.error': '加载 API 密钥失败',
  'apiKeys.securityError': 'API 返回了不应暴露的密钥字段，已停止展示。',
  'apiKeys.id': 'ID',
  'apiKeys.name': '名称',
  'apiKeys.note': '备注',
  'apiKeys.keyPrefix': 'Key 前缀',
  'apiKeys.status': '状态',
  'apiKeys.created': '创建时间',
  'apiKeys.lastUsed': '最近使用',
  'apiKeys.revokedAt': '吊销时间',
  'apiKeys.enabled': '启用',
  'apiKeys.disabled': '停用',
  'apiKeys.revoked': '已吊销',
};

const enUS = {
  // Navigation
  'nav.dashboard': 'Dashboard',
  'nav.generateImage': 'Generate Image',
  'nav.generateVideo': 'Generate Video',
  'nav.jobs': 'Jobs',
  'nav.assets': 'Assets',
  'nav.providers': 'Providers',
  'nav.apiKeys': 'API Keys',
  'nav.diagnostics': 'Diagnostics',

  // Topbar
  'topbar.studio': 'Studio',
  'topbar.logout': 'Logout',

  // Login page
  'login.title': 'AngeMedia Studio',
  'login.username': 'Username',
  'login.password': 'Password',
  'login.button': 'Login',
  'login.loggingIn': 'Logging in...',
  'login.failed': 'Login failed',

  // Dashboard
  'dashboard.loading': 'Loading...',
  'dashboard.health': 'Health',
  'dashboard.session': 'Session',
  'dashboard.statusPrefix': 'Status: ',
  'dashboard.loggedInPrefix': 'Logged in as: ',
  'dashboard.notAuthenticated': 'Not authenticated',
  'dashboard.unableToLoadSession': 'Unable to load session',
  'dashboard.error': 'error',
  'dashboard.unavailable': 'unavailable',
  'dashboard.unknown': 'unknown',

  // Generate Image
  'generateImage.title': 'Generate Image',
  'generateImage.prompt': 'Describe the image to generate',
  'generateImage.promptPlaceholder': 'e.g., A cute cat playing in the garden',
  'generateImage.promptRequired': 'Please enter a prompt',
  'generateImage.submit': 'Generate',
  'generateImage.generating': 'Generating...',
  'generateImage.error': 'Generation failed',
  'generateImage.success': 'Generation successful',
  'generateImage.previewAlt': 'Generated image',
  'generateImage.imageUnavailable': 'Image generated, but preview unavailable',
  'generateImage.duration': 'Duration',
  'generateImage.provider': 'Provider',
  'generateImage.model': 'Model',

  // Jobs
  'jobs.title': 'Jobs',
  'jobs.loading': 'Loading...',
  'jobs.empty': 'No jobs',
  'jobs.error': 'Failed to load jobs',
  'jobs.id': 'ID',
  'jobs.kind': 'Type',
  'jobs.status': 'Status',
  'jobs.created': 'Created',
  'jobs.duration': 'Duration',
  'jobs.provider': 'Provider',
  'jobs.model': 'Model',
  'jobs.errorCode': 'Error Code',
  'jobs.image': 'Image',
  'jobs.video': 'Video',
  'jobs.unknown': 'Unknown',
  'jobs.queued': 'Queued',
  'jobs.running': 'Running',
  'jobs.succeeded': 'Succeeded',
  'jobs.failed': 'Failed',
  'jobs.canceled': 'Canceled',

  // Assets
  'assets.title': 'Assets',
  'assets.loading': 'Loading...',
  'assets.empty': 'No assets',
  'assets.error': 'Failed to load assets',
  'assets.filename': 'Filename',
  'assets.type': 'Type',
  'assets.created': 'Created',
  'assets.jobId': 'Job ID',
  'assets.size': 'Size',
  'assets.source': 'Source',
  'assets.preview': 'Preview',
  'assets.unavailable': 'Preview unavailable',
  'assets.image': 'Image',
  'assets.video': 'Video',
  'assets.unknown': 'Unknown',
  'assets.generated': 'Generated',
  'assets.upload': 'Upload',

  // Gateway API Keys
  'apiKeys.title': 'API Keys',
  'apiKeys.subtitle': 'Read-only Gateway API Key metadata. Full keys are shown only once when created.',
  'apiKeys.loading': 'Loading...',
  'apiKeys.empty': 'No API keys yet',
  'apiKeys.error': 'Failed to load API keys',
  'apiKeys.securityError': 'The API returned sensitive key fields, so display was stopped.',
  'apiKeys.id': 'ID',
  'apiKeys.name': 'Name',
  'apiKeys.note': 'Note',
  'apiKeys.keyPrefix': 'Key Prefix',
  'apiKeys.status': 'Status',
  'apiKeys.created': 'Created',
  'apiKeys.lastUsed': 'Last Used',
  'apiKeys.revokedAt': 'Revoked At',
  'apiKeys.enabled': 'Enabled',
  'apiKeys.disabled': 'Disabled',
  'apiKeys.revoked': 'Revoked',
};

const locales = { 'zh-CN': zhCN, 'en-US': enUS };
const supportedLanguages = ['zh-CN', 'en-US'];
const defaultLanguage = 'zh-CN';

let currentLanguage = defaultLanguage;

function getLanguage() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && supportedLanguages.includes(stored)) {
      return stored;
    }
  } catch (_) {
    // localStorage 不可用或抛异常，忽略
  }
  return defaultLanguage;
}

function setLanguage(lang) {
  if (!supportedLanguages.includes(lang)) {
    lang = defaultLanguage;
  }
  currentLanguage = lang;
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch (_) {
    // localStorage 写入失败，忽略
  }
}

function t(key) {
  const dict = locales[currentLanguage] || locales[defaultLanguage];
  return dict[key] || locales[defaultLanguage][key] || key;
}

// 初始化语言
currentLanguage = getLanguage();

export { t, getLanguage, setLanguage, supportedLanguages, defaultLanguage };

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

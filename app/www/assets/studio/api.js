const BASE = '/v1';
import { createSseParser } from './lib/assistant-sse.js';
import { t } from './i18n.js';

let unauthorizedHandler = null;
let captureUnauthorizedScope = () => () => true;
let sessionContextProvider = null, waitForSession = null;
const CLIENT_REVISION = import.meta.url.match(/\/assets\/(r-[a-f0-9]{24})\//)?.[1] || '';
let upgradeRequired = false;

function upgradeError() {
  const message = t('auth.upgradeRequired');
  return new ApiError(message, { status: 409, safe: { code: 'studio_upgrade_required', message } });
}

function handleUpgrade(error, isCurrent) {
  if (!['session_context_required', 'studio_upgrade_required'].includes(error.safe.code)) return false;
  error.message = t('auth.upgradeRequired');
  if (isCurrent()) {
    upgradeRequired = true;
    unauthorizedHandler?.('studio_upgrade_required');
  }
  return true;
}

export function setSessionContextProvider(provider, ready) {
  sessionContextProvider = provider; waitForSession = ready;
}

function contextError() {
  const message = t('auth.sessionChanged');
  return new ApiError(message, { status: 409, safe: { code: 'session_context_changed', message } });
}

function contextHeaders() {
  if (!sessionContextProvider) return {};
  const context = sessionContextProvider();
  if (!context) throw contextError();
  return { 'X-Studio-Session-Context': context, ...(CLIENT_REVISION ? { 'X-Studio-Revision': CLIENT_REVISION } : {}) };
}

async function requestContext(path, isAuthRequest, isCurrent) {
  if (upgradeRequired) throw upgradeError();
  if (isAuthRequest || path === '/admin/session' || !sessionContextProvider) return {};
  await waitForSession?.();
  if (!isCurrent()) throw contextError();
  return contextHeaders();
}

export class ApiError extends Error {
  constructor(message, { status, detail, safe } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.safe = safe || {};
  }
}

// 由应用入口注入只读会话捕获器，避免API反向依赖认证模块。
export function setUnauthorizedHandler(handler, captureScope = () => () => true) {
  unauthorizedHandler = handler;
  captureUnauthorizedScope = captureScope;
}

function safeDetail(detail) {
  if (typeof detail === 'string') {
    return { message: detail };
  }
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return {};
  }
  const safe = {};
  ['message', 'code', 'error_category', 'human_hint', 'retryable', 'gateway_stage'].forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(detail, key)) {
      safe[key] = detail[key];
    }
  });
  if (detail.existing_job && typeof detail.existing_job === 'object') {
    safe.existing_job = {
      job_id: detail.existing_job.job_id,
      kind: detail.existing_job.kind,
      status: detail.existing_job.status,
      created_at: detail.existing_job.created_at,
    };
  }
  return safe;
}

async function parseErrorResponse(res) {
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    const safe = safeDetail(detail);
    const message = safe.human_hint || safe.message || res.statusText || `HTTP ${res.status}`;
    return new ApiError(message, { status: res.status, detail, safe });
  }
  return new ApiError(res.statusText || `HTTP ${res.status}`, { status: res.status });
}

async function request(method, path, body, isAuthRequest = false, headers = {},
  { signal, cache, redirect } = {}, { prefix = BASE, read = res => res.json() } = {}) {
  if (upgradeRequired) throw upgradeError();
  const isSessionCurrent = captureUnauthorizedScope();
  if (sessionContextProvider && path === '/admin/logout' && !headers['X-Studio-Session-Context']) throw contextError();
  const context = sessionContextProvider ? await requestContext(path, isAuthRequest, isSessionCurrent) : {};
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
  const opts = {
    method,
    credentials: 'include',
    headers: { ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(CLIENT_REVISION ? { 'X-Studio-Revision': CLIENT_REVISION } : {}), ...headers, ...context },
  };
  if (signal) opts.signal = signal;
  if (cache) opts.cache = cache;
  if (redirect) opts.redirect = redirect;
  if (body !== undefined) opts.body = isFormData ? body : JSON.stringify(body);
  const res = await fetch(`${prefix}${path}`, opts);
  if (!res.ok) {
    const error = await parseErrorResponse(res);
    if (handleUpgrade(error, isSessionCurrent)) throw error;
    const contextChanged = res.status === 409 && error.safe.code === 'session_context_changed';
    if (contextChanged) error.message = t('auth.sessionChanged');
    // 解析后再核对请求发起时的会话归属；旧401仍报错，但不能清除新登录。
    if ((contextChanged || (res.status === 401 && error.safe.code !== 'invalid_current_password')) && !isAuthRequest && unauthorizedHandler && isSessionCurrent()) {
      unauthorizedHandler(contextChanged ? 'session_context_changed' : undefined);
      if (contextChanged) throw error;
      throw new ApiError('Session expired', { status: 401 });
    }
    throw error;
  }
  if (res.status === 204) return null;
  return read(res);
}

function nativeResourceUrl(path) {
  if (upgradeRequired) throw upgradeError();
  const context = contextHeaders()['X-Studio-Session-Context'];
  const params = [context ? 'studio_session_context=' + encodeURIComponent(context) : '',
    CLIENT_REVISION ? 'studio_revision=' + CLIENT_REVISION : ''].filter(Boolean).join('&');
  return BASE + path + (params ? (path.includes('?') ? '&' : '?') + params : '');
}

function isLocalAssetPath(path) {
  return typeof path === 'string' && /^\/(?:generated|uploads)\/[^?#]+$/.test(path) &&
    new URL(path, 'https://studio.invalid').pathname === path;
}

export const api = {
  stream: async (path, { signal, onEvent }) => {
    if (upgradeRequired) throw upgradeError();
    const isSessionCurrent = captureUnauthorizedScope();
    const headers = sessionContextProvider ? await requestContext(path, false, isSessionCurrent) : {};
    const res = await fetch(`${BASE}${path}`, { credentials: 'include', cache: 'no-store', signal, headers });
    if (res.status === 401) {
      if (unauthorizedHandler && isSessionCurrent()) unauthorizedHandler();
      throw new ApiError('Session expired', { status: 401 });
    }
    if (!res.ok) {
      const error = await parseErrorResponse(res);
      if (handleUpgrade(error, isSessionCurrent)) throw error;
      if (res.status === 409 && error.safe.code === 'session_context_changed') {
        error.message = t('auth.sessionChanged');
        if (unauthorizedHandler && isSessionCurrent()) unauthorizedHandler('session_context_changed');
      }
      throw error;
    }
    if (res.status === 204) return;
    if (!res.headers.get('content-type')?.includes('text/event-stream') || !res.body) throw new ApiError('Invalid event stream');
    const reader = res.body.getReader(), decoder = new TextDecoder();
    const feed = createSseParser(onEvent);
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) { feed(decoder.decode()); break; }
        feed(decoder.decode(value, { stream: true }));
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  },
  eventUrl: nativeResourceUrl,
  validateSessionContext: (value) => {
    if (sessionContextProvider && value?.authenticated !== false && !value?.session_context) {
      const error = upgradeError(); handleUpgrade(error, () => true); throw error;
    }
  },
  get: (path, options = {}) => request('GET', path, undefined, false, {}, options),
  // 仅供浏览器预览和下载；提交任务仍传递原始资产路径。
  assetUrl: (path, { preview = false } = {}) => {
    if (!isLocalAssetPath(path)) return '';
    const query = preview && /\.svg$/i.test(path) ? '?preview=true' : '';
    try { return nativeResourceUrl('/admin/media' + path + query); }
    catch (error) {
      if (error instanceof ApiError) return '';
      throw error;
    }
  },
  assetBlob: async (path, { signal } = {}) => {
    if (!isLocalAssetPath(path)) {
      throw new ApiError('Invalid asset path');
    }
    const isCurrent = captureUnauthorizedScope();
    const blob = await request('GET', path, undefined, false, {}, { signal, cache: 'no-store', redirect: 'error' },
      { prefix: '', read: res => res.blob() });
    if (!isCurrent()) throw contextError();
    return blob;
  },
  post: (path, body, isAuthRequest = false, headers = {}, options = {}) => request('POST', path, body, isAuthRequest, headers, options),
  upload: (path, formData) => request('POST', path, formData),
  patch: (path, body) => request('PATCH', path, body),
  delete: (path) => request('DELETE', path),
};

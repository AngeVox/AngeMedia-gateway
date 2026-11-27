import { api } from './api.js';

let session = null;
let sessionEpoch = 0;
let sessionRequest = 0;
let sessionChange = null;
const LOGOUT_STORAGE_KEY = 'studio_logout_recovery';
const LOGOUT_CONTEXT_KEY = 'studio_logout_context';
export const SESSION_CHANGE_KEY = 'studio_session_change';
let verification = null, verificationError = null;
let sessionChangedNotice = false;
let logoutContext = readLogoutContext();
let logoutStatus = readLogoutStatus();
let logoutAttempt = null;
let canRestoreSession = logoutStatus === 'idle';
let validatedNotice = readSessionNotice();

function readLogoutContext() {
  try { return sessionStorage.getItem(LOGOUT_CONTEXT_KEY) || null; } catch (_) { return null; }
}

function saveLogoutContext(value) {
  logoutContext = value || null;
  try {
    if (logoutContext) sessionStorage.setItem(LOGOUT_CONTEXT_KEY, logoutContext);
    else sessionStorage.removeItem(LOGOUT_CONTEXT_KEY);
  } catch (_) { /* 本页仍保留退出归属。 */ }
}

export function readSessionNotice() {
  try { return localStorage.getItem(SESSION_CHANGE_KEY); } catch (_) { return null; }
}

export function announceSessionChange() {
  try { localStorage.setItem(SESSION_CHANGE_KEY, Date.now() + ':' + Math.random()); } catch (_) { /* 请求标记与聚焦复核仍有效。 */ }
  validatedNotice = readSessionNotice();
}

export function isSessionNoticeCurrent() { return validatedNotice === readSessionNotice(); }

export function getSessionContext() { return session?.session_context || null; }

export function invalidateExternalSession() {
  clearSession(); canRestoreSession = false; sessionChangedNotice = true;
}

export function consumeSessionChangedNotice() {
  const value = sessionChangedNotice; sessionChangedNotice = false; return value;
}

export async function waitForSessionReady() {
  while (sessionChange || verification) await (sessionChange || verification);
  if (verificationError) throw verificationError;
}

function readLogoutStatus() {
  try {
    const saved = sessionStorage.getItem(LOGOUT_STORAGE_KEY);
    return ['unconfirmed', 'confirmed'].includes(saved) ? saved : 'idle';
  } catch (_) { return 'idle'; }
}

function setLogoutStatus(status) {
  logoutStatus = status;
  try {
    // 刷新会中断观察，发出前只记录未确认事实；不保存身份、令牌或密码。
    if (status === 'idle') sessionStorage.removeItem(LOGOUT_STORAGE_KEY);
    else sessionStorage.setItem(LOGOUT_STORAGE_KEY, status === 'pending' ? 'unconfirmed' : status);
  } catch (_) { /* 存储受限时保留当前页恢复能力。 */ }
}

export function getLogoutRecovery() {
  return { status: logoutStatus, completion: logoutAttempt?.promise || null };
}

// Cookie由浏览器处理响应头；本页认证变更必须按发送顺序完成，不能仅靠回调代次。
function changeSession(operation) {
  const request = sessionChange ? sessionChange.then(operation) : operation();
  const settled = request.then(() => {}, () => {});
  sessionChange = settled;
  settled.then(() => { if (sessionChange === settled) sessionChange = null; });
  return request;
}

async function postAuth(path, body, context = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try { return await api.post(path, body, true, context ? { 'X-Studio-Session-Context': context } : {}, { signal: controller.signal }); }
  finally { clearTimeout(timer); }
}

// 只暴露会话归属判定，不让业务窗口持有或修改会话代次。
export function captureSessionScope() {
  const epoch = sessionEpoch;
  const notice = readSessionNotice();
  return () => epoch === sessionEpoch && notice === readSessionNotice();
}

export async function login(username, password) {
  const epoch = ++sessionEpoch;
  verification = null; verificationError = null;
  session = null;
  canRestoreSession = false;
  return changeSession(async () => {
    if (epoch !== sessionEpoch) return null;
    try {
      const res = await postAuth('/admin/login', { username, password });
      if (epoch !== sessionEpoch) {
        if (logoutAttempt && !logoutContext && res.session_context) saveLogoutContext(res.session_context);
        return null;
      }
      api.validateSessionContext(res);
      session = { authenticated: true, username: res.username };
      if (res.session_context) session.session_context = res.session_context;
      validatedNotice = readSessionNotice();
      canRestoreSession = true;
      verificationError = null;
      sessionChangedNotice = false;
      saveLogoutContext(null);
      setLogoutStatus('idle');
      sessionEpoch += 1;
      announceSessionChange();
      return res;
    } catch (error) {
      if (epoch !== sessionEpoch) return null;
      throw error;
    }
  });
}

export async function logout() {
  if (logoutAttempt?.epoch === sessionEpoch) return logoutAttempt.promise;
  saveLogoutContext(getSessionContext() || logoutContext);
  clearSession();
  announceSessionChange();
  canRestoreSession = false;
  const epoch = sessionEpoch;
  const attempt = { epoch, promise: null };
  logoutAttempt = attempt;
  setLogoutStatus('pending');
  attempt.promise = changeSession(async () => {
    let confirmed = false;
    // 认证变更自行处理失败；旧退出的401不能取消后续排队的新登录。
    try {
      const result = await postAuth('/admin/logout', undefined, logoutContext);
      confirmed = result?.ok === true;
    } catch (_) { /* 传输失败不能确认服务器已退出；本页仍保持退出。 */ }
    if (logoutAttempt === attempt) setLogoutStatus(confirmed ? 'confirmed' : 'unconfirmed');
    return confirmed;
  }).then((confirmed) => {
    if (logoutAttempt === attempt) logoutAttempt = null;
    if (epoch === sessionEpoch) location.hash = '#/login';
    return confirmed;
  });
  return attempt.promise;
}

export async function getSession({ refresh = false } = {}) {
  if (refresh) return verifySession();
  // 不从登录/退出之间的临时Cookie读取身份；明确退出后等待下一次成功登录。
  while (sessionChange) await sessionChange;
  if (session) return session;
  if (!canRestoreSession) return null;
  const epoch = sessionEpoch;
  const request = ++sessionRequest;
  try {
    const res = await api.get('/admin/session');
    if (epoch !== sessionEpoch || request !== sessionRequest) return session;
    api.validateSessionContext(res);
    if (res.authenticated) {
      session = { authenticated: true, username: res.username || '' };
      if (res.session_context) session.session_context = res.session_context;
      validatedNotice = readSessionNotice();
      return session;
    }
  } catch (_) { /* ignore */ }
  // 清会话、重新登录或较新的检查，均不能被旧响应或旧异常覆盖。
  if (epoch === sessionEpoch && request === sessionRequest) session = null;
  return session;
}

function verifySession() {
  if (verification) return verification;
  const epoch = sessionEpoch, expected = getSessionContext(), notice = readSessionNotice();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  const request = (async () => {
    while (sessionChange) await sessionChange;
    if (epoch !== sessionEpoch) throw new DOMException('Session check superseded', 'AbortError');
    if (!canRestoreSession) return null;
    try {
      const res = await api.get('/admin/session', { signal: controller.signal, cache: 'no-store' });
      if (epoch !== sessionEpoch) throw new DOMException('Session check superseded', 'AbortError');
      api.validateSessionContext(res);
      verificationError = null;
      if (notice !== readSessionNotice() || !res.authenticated || (expected && (res.session_context !== expected || res.username !== session?.username))) {
        invalidateExternalSession(); return null;
      }
      session = { authenticated: true, username: res.username || '' };
      if (res.session_context) session.session_context = res.session_context;
      validatedNotice = readSessionNotice();
      return session;
    } catch (error) {
      if (epoch !== sessionEpoch) throw new DOMException('Session check superseded', 'AbortError');
      verificationError = error; throw error;
    }
  })().finally(() => {
    clearTimeout(timer);
    if (verification === request) verification = null;
  });
  verification = request;
  return request;
}

export function requireSession() {
  if (!session) {
    location.hash = '#/login';
    return false;
  }
  return true;
}

export function clearSession() {
  session = null;
  verification = null;
  verificationError = null;
  sessionEpoch += 1;
}

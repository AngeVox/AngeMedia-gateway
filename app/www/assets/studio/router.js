const routes = [];
let notFoundHandler = null;
let navigationVersion = 0;
const viewOwners = new WeakMap();
let resumeGuard = null;

export function setResumeGuard(guard) { resumeGuard = guard; }

// 路由代次隔离往返切页，容器所有权隔离同页重建，请求代次隔离同页重读。
export function createRenderScope(target) {
  const navigation = navigationVersion;
  const hash = location.hash;
  const owner = {};
  if (target) viewOwners.set(target, owner);
  let requestVersion = 0;
  const isActive = () => navigation === navigationVersion && location.hash === hash &&
    (!target || (target.isConnected && viewOwners.get(target) === owner));
  return { isActive, nextRequest() {
    const request = ++requestVersion;
    return () => isActive() && request === requestVersion;
  } };
}

export function register(hash, handler) {
  routes.push({ pattern: hash, handler });
}

// 在途准备拥有自己的离页监听；结束后释放，不取消服务端已受理的任务。
export function createPendingScope(isCurrent) {
  let closed = false;
  const close = () => { closed = true; window.removeEventListener('pagehide', close); };
  window.addEventListener('pagehide', close, { once: true });
  return { isActive: () => !closed && isCurrent(), close };
}

// 页面拥有资源；控件在页面内部重挂不算销毁，返回缓存只暂停预览。
export function bindViewResources(isCurrent, { dispose, suspend, resume }) {
  let closed = false, suspended = false;
  const observer = typeof MutationObserver === 'function' ? new MutationObserver(check) : null;
  function close() {
    if (closed) return;
    closed = true;
    observer?.disconnect();
    window.removeEventListener('hashchange', check);
    window.removeEventListener('pagehide', hide);
    window.removeEventListener('pageshow', show);
    dispose();
  }
  function check() { if (!closed && !isCurrent()) close(); }
  function hide(event) {
    check();
    if (closed) return;
    if (!event?.persisted) { close(); return; }
    if (!suspended) { suspended = true; suspend(); }
  }
  function show(event) {
    check();
    if (closed || !suspended || !event.persisted) return;
    const finish = allowed => {
      check();
      if (!closed && suspended && allowed) { suspended = false; resume(); }
    };
    if (resumeGuard) Promise.resolve(resumeGuard()).then(finish).catch(() => {});
    else finish(true);
  }
  observer?.observe(document.body, { childList: true, subtree: true });
  window.addEventListener('hashchange', check);
  window.addEventListener('pagehide', hide);
  window.addEventListener('pageshow', show);
  // 构建函数返回后才挂载，首次检查放到同轮挂载之后。
  queueMicrotask(check);
  return { isActive: () => !closed && !suspended && isCurrent(), close };
}

export function onNotFound(handler) {
  notFoundHandler = handler;
}

function matchRoute(hash) {
  const path = hash || '#/dashboard';
  for (const route of routes) {
    if (route.pattern === path) return { handler: route.handler, params: {} };
    const re = new RegExp('^' + route.pattern.replace(/:([^/]+)/g, '([^/]+)') + '$');
    const m = path.match(re);
    if (m) {
      const keys = (route.pattern.match(/:([^/]+)/g) || []).map(k => k.slice(1));
      const params = {};
      keys.forEach((k, i) => { params[k] = m[i + 1]; });
      return { handler: route.handler, params };
    }
  }
  return null;
}

export function navigate(hash) {
  location.hash = hash;
}

export function start() {
  window.addEventListener('hashchange', handleHash);
  handleHash();
}

function handleHash() {
  navigationVersion += 1;
  const scope = createRenderScope();
  const result = matchRoute(location.hash);
  if (result) {
    result.handler(result.params, scope);
  } else if (notFoundHandler) {
    notFoundHandler({}, scope);
  }
}

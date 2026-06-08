const routes = [];
let notFoundHandler = null;

export function register(hash, handler) {
  routes.push({ pattern: hash, handler });
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
  const result = matchRoute(location.hash);
  if (result) {
    result.handler(result.params);
  } else if (notFoundHandler) {
    notFoundHandler();
  }
}

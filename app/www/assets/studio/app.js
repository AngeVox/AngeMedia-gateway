import { renderShell, setChromeVisible, guard } from './layout.js?v=web-studio-2j';
import * as router from './router.js';
import { clearSession, captureSessionScope, getSessionContext, invalidateExternalSession, waitForSessionReady } from './auth.js';
import { setUnauthorizedHandler, setSessionContextProvider } from './api.js';
import { startSessionLifecycle } from './components/session-lifecycle.js';
import { el, mount } from './components/dom.js';
import { initTheme } from './lib/theme.js';
import { render as renderLogin } from './pages/login.js?v=web-studio-2j';
import { render as renderDashboard } from './pages/dashboard.js?v=web-studio-2j';
import { render as renderGenImage } from './pages/generate-image.js?v=web-studio-2j';
import { render as renderGenVideo } from './pages/generate-video.js?v=web-studio-2j';
import { render as renderJobsList } from './pages/jobs-list.js?v=web-studio-2j';
import { render as renderJobsDetail } from './pages/jobs-detail.js';
import { render as renderAssetsList } from './pages/assets-list.js?v=web-studio-2j';
import { render as renderAssetsDetail } from './pages/assets-detail.js';
import { render as renderLayersDetail } from './pages/layers-detail.js';
import { render as renderProviders } from './pages/providers.js?v=web-studio-2j';
import { render as renderGatewayKeys } from './pages/gateway-keys.js?v=web-studio-2j';
import { render as renderSystem } from './pages/system.js?v=web-studio-2j';
import { render as renderDiagnostics } from './pages/diagnostics.js';

// 旧HTML的入口转到新图时，样式也跟随实际模块版本；尚未创建页面或草稿。
const assetRevision = import.meta.url.match(/\/assets\/(r-[a-f0-9]{24})\//)?.[1];
if (assetRevision) {
  document.documentElement.dataset.studioRevision = assetRevision;
  await Promise.allSettled(Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(link => {
    const url = new URL(link.href);
    if (url.origin !== location.origin || !/^\/assets\/r-[a-f0-9]{24}\//.test(url.pathname)) return null;
    const current = url.pathname.replace(/^\/assets\/r-[a-f0-9]{24}\//, '/assets/' + assetRevision + '/');
    if (current === url.pathname) return null;
    url.pathname = current;
    return new Promise((resolve, reject) => {
      link.addEventListener('load', resolve, { once: true });
      link.addEventListener('error', reject, { once: true });
      link.href = url.href;
    });
  }));
}

function content() { return document.getElementById('content'); }

function wrapLogin(fn) {
  return async (params, scope) => {
    if (!scope.isActive()) return;
    setChromeVisible(false);
    document.body.classList.add('is-login');
    mount(content());
    await fn(params);
  };
}

function wrapAuth(fn) {
  return async (params, scope) => {
    const authenticated = await guard(scope.isActive);
    if (!scope.isActive()) return;
    if (!authenticated) {
      setChromeVisible(false);
      return;
    }
    document.body.classList.remove('is-login');
    renderShell();
    setChromeVisible(true);
    mount(content());
    await fn(params);
  };
}

async function notFound(_params, scope) {
  const authenticated = await guard(scope.isActive);
  if (!scope.isActive()) return;
  if (!authenticated) {
    setChromeVisible(false);
    return;
  }
  document.body.classList.remove('is-login');
  renderShell();
  setChromeVisible(true);
  mount(content(),
    el('section', { class: 'panel' },
      el('div', { class: 'panel-body' },
        el('h2', {}, '404 Not Found'),
        el('p', { class: 'text-muted' }, 'The page you requested does not exist.'),
      ),
    ),
  );
}

setUnauthorizedHandler(reason => {
  if (reason === 'studio_upgrade_required') { sessionLifecycle.upgrade(); return; }
  if (reason === 'session_context_changed') invalidateExternalSession();
  else clearSession();
  setChromeVisible(false);
  location.hash = '#/login';
}, captureSessionScope);
setSessionContextProvider(getSessionContext, waitForSessionReady);
const sessionLifecycle = startSessionLifecycle(() => {
  setChromeVisible(false);
  router.createRenderScope(content());
  mount(content());
  if (location.hash === '#/login') renderLogin();
  else router.navigate('#/login');
});
router.setResumeGuard(sessionLifecycle.check);

router.register('#/login',           wrapLogin(renderLogin));
router.register('#/dashboard',       wrapAuth(renderDashboard));
router.register('#/generate/image',  wrapAuth(renderGenImage));
router.register('#/generate/video',  wrapAuth(renderGenVideo));
router.register('#/jobs',            wrapAuth(renderJobsList));
router.register('#/jobs/:id',        wrapAuth(renderJobsDetail));
router.register('#/assets',          wrapAuth(renderAssetsList));
router.register('#/assets/:id',      wrapAuth(renderAssetsDetail));
router.register('#/assets/layers/:id', wrapAuth(renderLayersDetail));
router.register('#/providers',       wrapAuth(renderProviders));
router.register('#/gateway-keys',    wrapAuth(renderGatewayKeys));
router.register('#/system',          wrapAuth(renderSystem));
router.register('#/diagnostics',     wrapAuth(renderDiagnostics));
router.onNotFound(notFound);

if (!location.hash) location.hash = '#/dashboard';

initTheme();
renderShell();
router.start();

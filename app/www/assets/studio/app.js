import { renderShell, setChromeVisible, guard } from './layout.js?v=web-studio-2f';
import * as router from './router.js';
import { clearSession } from './auth.js';
import { setUnauthorizedHandler } from './api.js';
import { el, mount } from './components/dom.js';
import { initTheme } from './lib/theme.js';
import { render as renderLogin } from './pages/login.js?v=web-studio-2f';
import { render as renderDashboard } from './pages/dashboard.js?v=web-studio-2f';
import { render as renderGenImage } from './pages/generate-image.js?v=web-studio-2f';
import { render as renderGenVideo } from './pages/generate-video.js?v=web-studio-2f';
import { render as renderJobsList } from './pages/jobs-list.js?v=web-studio-2f';
import { render as renderJobsDetail } from './pages/jobs-detail.js';
import { render as renderAssetsList } from './pages/assets-list.js?v=web-studio-2f';
import { render as renderAssetsDetail } from './pages/assets-detail.js';
import { render as renderProviders } from './pages/providers.js?v=web-studio-2f';
import { render as renderGatewayKeys } from './pages/gateway-keys.js?v=web-studio-2f';
import { render as renderDiagnostics } from './pages/diagnostics.js';

function content() { return document.getElementById('content'); }

function wrapLogin(fn) {
  return async (params) => {
    setChromeVisible(false);
    document.body.classList.add('is-login');
    mount(content());
    await fn(params);
  };
}

function wrapAuth(fn) {
  return async (params) => {
    if (!(await guard())) {
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

async function notFound() {
  if (!(await guard())) {
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

setUnauthorizedHandler(() => {
  clearSession();
  setChromeVisible(false);
  location.hash = '#/login';
});

router.register('#/login',           wrapLogin(renderLogin));
router.register('#/dashboard',       wrapAuth(renderDashboard));
router.register('#/generate/image',  wrapAuth(renderGenImage));
router.register('#/generate/video',  wrapAuth(renderGenVideo));
router.register('#/jobs',            wrapAuth(renderJobsList));
router.register('#/jobs/:id',        wrapAuth(renderJobsDetail));
router.register('#/assets',          wrapAuth(renderAssetsList));
router.register('#/assets/:id',      wrapAuth(renderAssetsDetail));
router.register('#/providers',       wrapAuth(renderProviders));
router.register('#/gateway-keys',    wrapAuth(renderGatewayKeys));
router.register('#/diagnostics',     wrapAuth(renderDiagnostics));
router.onNotFound(notFound);

if (!location.hash) location.hash = '#/dashboard';

initTheme();
renderShell();
router.start();

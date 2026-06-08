import { renderShell, setChromeVisible, guard } from './layout.js';
import * as router from './router.js';
import { render as renderLogin } from './pages/login.js';
import { render as renderDashboard } from './pages/dashboard.js';
import { render as renderGenImage } from './pages/generate-image.js';
import { render as renderGenVideo } from './pages/generate-video.js';
import { render as renderJobsList } from './pages/jobs-list.js';
import { render as renderJobsDetail } from './pages/jobs-detail.js';
import { render as renderAssetsList } from './pages/assets-list.js';
import { render as renderAssetsDetail } from './pages/assets-detail.js';
import { render as renderProviders } from './pages/providers.js';
import { render as renderGatewayKeys } from './pages/gateway-keys.js';
import { render as renderDiagnostics } from './pages/diagnostics.js';

function content() { return document.getElementById('content'); }

function wrapLogin(fn) {
  return async (params) => {
    setChromeVisible(false);
    content().innerHTML = '';
    await fn(params);
  };
}

function wrapAuth(fn) {
  return async (params) => {
    if (!(await guard())) {
      setChromeVisible(false);
      return;
    }
    renderShell();
    setChromeVisible(true);
    content().innerHTML = '';
    await fn(params);
  };
}

async function notFound() {
  if (!(await guard())) {
    setChromeVisible(false);
    return;
  }
  renderShell();
  setChromeVisible(true);
  content().innerHTML = '<div class="card"><h2>404 Not Found</h2><p>The page you requested does not exist.</p></div>';
}

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

renderShell();
router.start();

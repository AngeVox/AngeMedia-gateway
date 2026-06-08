import { logout, getSession } from './auth.js';
import { navigate } from './router.js';
import { t, getLanguage, setLanguage, supportedLanguages } from './i18n.js';

const NAV = [
  { hash: '#/dashboard',          key: 'nav.dashboard' },
  { hash: '#/generate/image',     key: 'nav.generateImage' },
  { hash: '#/generate/video',     key: 'nav.generateVideo' },
  { hash: '#/jobs',               key: 'nav.jobs' },
  { hash: '#/assets',             key: 'nav.assets' },
  { hash: '#/providers',          key: 'nav.providers' },
  { hash: '#/gateway-keys',       key: 'nav.apiKeys' },
  { hash: '#/diagnostics',        key: 'nav.diagnostics' },
];

let shellRendered = false;

function renderNav() {
  const nav = document.querySelector('.sidebar-nav');
  if (!nav) return;
  nav.innerHTML = NAV.map(n =>
    `<a class="nav-item" href="${n.hash}">${t(n.key)}</a>`
  ).join('');
}

export function renderShell() {
  if (shellRendered) return;
  shellRendered = true;

  const sidebar = document.getElementById('sidebar');
  const topbar = document.getElementById('topbar');

  sidebar.innerHTML = `
    <div class="sidebar-brand">AngeMedia</div>
    <nav class="sidebar-nav"></nav>
  `;
  renderNav();

  const langOptions = supportedLanguages.map(lang =>
    `<option value="${lang}">${lang}</option>`
  ).join('');

  topbar.innerHTML = `
    <div class="topbar-left">
      <span class="topbar-title">${t('topbar.studio')}</span>
      <select id="studio-lang" style="margin-left: 12px; padding: 2px 4px; font-size: 12px;">
        ${langOptions}
      </select>
    </div>
    <div class="topbar-right">
      <button class="btn btn-sm" id="logout-btn">${t('topbar.logout')}</button>
    </div>
  `;

  const langSelect = document.getElementById('studio-lang');
  langSelect.value = getLanguage();
  langSelect.addEventListener('change', (e) => {
    setLanguage(e.target.value);
    location.reload();
  });

  document.getElementById('logout-btn').addEventListener('click', () => logout());
  updateActiveNav();
  window.addEventListener('hashchange', updateActiveNav);
}

export function setChromeVisible(visible) {
  const sidebar = document.getElementById('sidebar');
  const topbar = document.getElementById('topbar');
  if (sidebar) sidebar.style.display = visible ? '' : 'none';
  if (topbar) topbar.style.display = visible ? '' : 'none';
}

export function updateActiveNav() {
  const current = location.hash || '#/dashboard';
  document.querySelectorAll('.nav-item').forEach(a => {
    a.classList.toggle('active', a.getAttribute('href') === current);
  });
}

export async function guard() {
  const s = await getSession();
  if (!s) {
    navigate('#/login');
    return false;
  }
  return true;
}

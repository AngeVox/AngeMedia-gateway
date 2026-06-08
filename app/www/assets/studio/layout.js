import { logout, getSession } from './auth.js';
import { navigate } from './router.js';

const NAV = [
  { hash: '#/dashboard',          label: 'Dashboard' },
  { hash: '#/generate/image',     label: 'Generate Image' },
  { hash: '#/generate/video',     label: 'Generate Video' },
  { hash: '#/jobs',               label: 'Jobs' },
  { hash: '#/assets',             label: 'Assets' },
  { hash: '#/providers',          label: 'Providers' },
  { hash: '#/gateway-keys',       label: 'API Keys' },
  { hash: '#/diagnostics',        label: 'Diagnostics' },
];

let shellRendered = false;

export function renderShell() {
  if (shellRendered) return;
  shellRendered = true;

  const sidebar = document.getElementById('sidebar');
  const topbar = document.getElementById('topbar');

  sidebar.innerHTML = `
    <div class="sidebar-brand">AngeMedia</div>
    <nav class="sidebar-nav">
      ${NAV.map(n => `<a class="nav-item" href="${n.hash}">${n.label}</a>`).join('')}
    </nav>
  `;

  topbar.innerHTML = `
    <div class="topbar-left"><span class="topbar-title">Studio</span></div>
    <div class="topbar-right">
      <button class="btn btn-sm" id="logout-btn">Logout</button>
    </div>
  `;

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

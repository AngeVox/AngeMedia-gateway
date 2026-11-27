import { openAccountModal } from './components/account-settings.js?v=web-studio-2j';
import { logout, getSession } from './auth.js';
import { navigate } from './router.js';
import { t } from './i18n.js';
import { el } from './components/dom.js';
import { languageSwitch } from './components/language-switch.js';
import { openAssistantChat } from './components/assistant-chat.js?v=assistant-conversation-1';
import { getTheme, toggleTheme } from './lib/theme.js';

const NAV = [
  { hash: '#/dashboard', key: 'nav.dashboard', group: 'studio' },
  { hash: '#/generate/image', key: 'nav.generateImage', group: 'create' },
  { hash: '#/generate/video', key: 'nav.generateVideo', group: 'create' },
  { hash: '#/jobs', key: 'nav.jobs', group: 'manage' },
  { hash: '#/assets', key: 'nav.assets', group: 'manage' },
  { hash: '#/providers', key: 'nav.providers', group: 'config' },
  { hash: '#/system', key: 'nav.system', group: 'config' },
  { hash: '#/gateway-keys', key: 'nav.apiKeys', group: 'config' },
];

let shellRendered = false;
const COMPACT_NAV = '(max-width: 1100px)';

function closeCompactNav() {
  const menu = document.querySelector('.nav-disclosure');
  if (!menu || !window.matchMedia(COMPACT_NAV).matches) return;
  const returnFocus = menu.contains(document.activeElement);
  menu.open = false;
  if (returnFocus) menu.querySelector('summary').focus();
}

function topAction({ label, icon, onClick, title = '', className = '', wip = false }) {
  return el('button', {
    type: 'button',
    class: ['top-action', className].filter(Boolean).join(' '),
    title: title || label,
    ariaLabel: title || label,
    onclick: onClick,
  },
    icon ? el('span', { class: 'top-action-icon' }, icon) : null,
    el('span', { class: 'top-action-label' }, label),
    wip ? el('span', { class: 'top-wip-badge' }, 'WIP') : null,
  );
}

function renderNav() {
  const nav = document.querySelector('.sidebar-nav');
  if (!nav) return;
  nav.textContent = '';
  let lastGroup = '';
  NAV.forEach((item) => {
    if (item.group !== lastGroup) {
      nav.appendChild(el('p', { class: 'nav-group' }, t(`navGroup.${item.group}`)));
      lastGroup = item.group;
    }
    nav.appendChild(el('a', { class: 'nav-item', href: item.hash, dataset: { route: item.hash } },
      el('span', { class: 'nav-marker' }),
      el('span', { class: 'nav-label' }, t(item.key)),
    ));
  });
}

export function renderShell() {
  if (shellRendered) return;
  shellRendered = true;

  const sidebar = document.getElementById('sidebar');
  const topbar = document.getElementById('topbar');

  sidebar.textContent = '';
  sidebar.append(
    el('div', { class: 'sidebar-brand' },
      el('strong', {}, 'AngeMedia'),
      el('span', {}, 'Studio'),
    ),
    el('details', { class: 'nav-disclosure', open: !window.matchMedia(COMPACT_NAV).matches,
      onkeydown: (event) => {
        if (event.key === 'Escape') closeCompactNav();
      },
    },
      el('summary', { class: 'nav-menu-summary' },
        el('span', { class: 'nav-menu-label' }, t('nav.navigation')),
        ' ',
        el('span', { class: 'nav-current' }),
      ),
      el('nav', { class: 'sidebar-nav', ariaLabel: t('nav.navigation'), onclick: (event) => {
        if (event.target.closest('a')) closeCompactNav();
      } }),
    ),
    el('div', { class: 'sidebar-footer' },
      el('p', {}, t('shell.localMode')),
      el('span', { class: 'soft-pill' }, t('shell.selfHosted')),
    ),
  );
  renderNav();
  // 同一份导航在窄屏展开，宽屏平铺；切换宽度时不保留隐藏的焦点。
  const compactNav = window.matchMedia(COMPACT_NAV);
  const navMenu = sidebar.querySelector('.nav-disclosure');
  let wasCompact = compactNav.matches;
  let navFocused = false;
  navMenu.addEventListener('focusin', () => { navFocused = true; });
  navMenu.addEventListener('focusout', (event) => {
    // CSS可能先隐藏控件再通知宽度变化，此时保留切换前的导航焦点。
    if (wasCompact === compactNav.matches) navFocused = navMenu.contains(event.relatedTarget);
  });
  compactNav.addEventListener('change', (event) => {
    const restoreFocus = navFocused;
    wasCompact = event.matches;
    navMenu.open = !event.matches;
    if (restoreFocus) {
      const target = event.matches ? navMenu.querySelector('summary') :
        navMenu.querySelector('.nav-item.active') || navMenu.querySelector('.nav-item');
      target.focus();
    }
  });

  topbar.textContent = '';
  const themeButton = topAction({
    label: t('topbar.theme'),
    icon: 'Aa',
    title: getTheme() === 'dark' ? t('theme.toLight') : t('theme.toDark'),
    onClick: () => {
      const next = toggleTheme();
      const title = next === 'dark' ? t('theme.toLight') : t('theme.toDark');
      themeButton.title = title;
      themeButton.setAttribute('aria-label', title);
    },
  });

  const assistantButton = topAction({
    label: t('topbar.assistant'),
    icon: 'AI',
    title: t('topbar.assistantHint'),
    onClick: () => openAssistantChat(),
  });

  const diagnosticsButton = topAction({
    label: t('topbar.diagnostics'),
    icon: 'DX',
    title: t('topbar.diagnostics'),
    onClick: () => navigate('#/diagnostics'),
  });

  const logoutButton = topAction({
    label: t('topbar.logout'),
    icon: 'OUT',
    onClick: () => { const leaving = logout(); navigate('#/login'); return leaving; },
  });

  const accountButton = topAction({
    label: t('topbar.account'),
    icon: 'ID',
    onClick: () => openAccountModal(),
  });

  topbar.appendChild(el('div', { class: 'topbar-inner' },
    el('div', { class: 'brand' },
      el('div', { class: 'logo' }, 'Ange', el('em', {}, 'Media')),
      el('div', { class: 'brand-line' }),
      el('div', { class: 'title-block' },
        el('div', { class: 'eyebrow' }, 'ANGEMEDIA STUDIO'),
        el('h1', {}, t('topbar.studio')),
      ),
    ),
    el('div', { class: 'top-actions' },
      el('span', { class: 'top-action top-action-status', title: t('topbar.gatewayOnline') },
        el('span', { class: 'top-action-icon' }, 'ON'),
        el('span', { class: 'top-action-label' }, t('topbar.gatewayOnline')),
      ),
      assistantButton,
      diagnosticsButton,
      themeButton,
      languageSwitch(),
      accountButton,
      logoutButton,
    ),
  ));

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
  let currentLabel = current === '#/diagnostics' ? t('topbar.diagnostics') : t('nav.navigation');
  document.querySelectorAll('.nav-item').forEach(a => {
    const route = a.getAttribute('href') || '';
    const active = route === current || (route !== '#/dashboard' && current.startsWith(`${route}/`));
    a.classList.toggle('active', active);
    if (active) {
      a.setAttribute('aria-current', 'page');
      currentLabel = a.textContent;
    } else a.removeAttribute('aria-current');
  });
  const label = document.querySelector('.nav-current');
  if (label) label.textContent = currentLabel;
  closeCompactNav();
}

export async function guard(isActive = () => true) {
  const s = await getSession();
  if (!isActive()) return false;
  if (!s) {
    navigate('#/login');
    return false;
  }
  return true;
}

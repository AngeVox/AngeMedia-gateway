import { clearSession, logout, getSession } from './auth.js';
import { api } from './api.js';
import { navigate } from './router.js';
import { t } from './i18n.js';
import { el } from './components/dom.js';
import { button } from './components/buttons.js';
import { field, input } from './components/forms.js';
import { languageSwitch } from './components/language-switch.js';
import { safeErrorMessage } from './lib/safe-error.js';
import { openAssistantPlanner } from './components/assistant-planner.js?v=web-studio-2h';
import { toast } from './components/toast.js';
import { getTheme, toggleTheme } from './lib/theme.js';

const NAV = [
  { hash: '#/dashboard', key: 'nav.dashboard', group: 'studio' },
  { hash: '#/generate/image', key: 'nav.generateImage', group: 'create' },
  { hash: '#/generate/video', key: 'nav.generateVideo', group: 'create' },
  { hash: '#/jobs', key: 'nav.jobs', group: 'manage' },
  { hash: '#/assets', key: 'nav.assets', group: 'manage' },
  { hash: '#/providers', key: 'nav.providers', group: 'config' },
  { hash: '#/gateway-keys', key: 'nav.apiKeys', group: 'config' },
];

let shellRendered = false;

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

async function openAccountModal() {
  let account = await getSession();
  try {
    account = await api.get('/admin/account');
  } catch (_) {
    /* Session summary is enough to prefill the current username. */
  }

  const overlay = el('div', { class: 'modal-overlay' });
  const close = () => overlay.remove();
  const requireRelogin = (message) => {
    toast(message, 'success');
    close();
    setTimeout(() => {
      clearSession();
      navigate('#/login');
    }, 650);
  };

  const currentUsername = input({ type: 'text', value: account?.username || '', disabled: true });
  const newUsername = input({ type: 'text', autocomplete: 'username', maxLength: 64, placeholder: t('account.newUsernamePlaceholder') });
  const currentPassword = input({ type: 'password', autocomplete: 'current-password' });
  const newPassword = input({ type: 'password', autocomplete: 'new-password', minLength: 8 });
  const confirmPassword = input({ type: 'password', autocomplete: 'new-password', minLength: 8 });
  const accountError = el('p', { class: 'form-error', hidden: true });

  function showError(target, message) {
    target.textContent = message;
    target.hidden = false;
  }

  const accountSubmit = button(t('account.saveAccount'), {
    variant: 'primary',
    onClick: async () => {
      accountError.hidden = true;

      const payload = {
        current_password: currentPassword.value,
      };
      const trimmedUsername = newUsername.value.trim();
      if (trimmedUsername) {
        payload.new_username = trimmedUsername;
      }
      if (newPassword.value || confirmPassword.value) {
        if (newPassword.value !== confirmPassword.value) {
          showError(accountError, t('account.passwordMismatch'));
          return;
        }
        payload.new_password = newPassword.value;
        payload.confirm_new_password = confirmPassword.value;
      }
      if (!payload.new_username && !payload.new_password) {
        showError(accountError, t('account.noChanges'));
        return;
      }

      accountSubmit.disabled = true;
      try {
        await api.patch('/admin/account', payload);
        requireRelogin(t('account.accountUpdated'));
      } catch (error) {
        showError(accountError, safeErrorMessage(error, t('account.updateFailed')));
      } finally {
        accountSubmit.disabled = false;
      }
    },
  });

  overlay.appendChild(el('div', { class: 'modal account-modal', role: 'dialog', ariaModal: 'true' },
    el('h2', {}, t('account.title')),
    el('p', { class: 'modal-copy' }, t('account.copy')),
    el('section', { class: 'form-subsection' },
      el('div', { class: 'form-subsection-header' },
        el('span', {}, t('account.combinedSection')),
      ),
      field(t('account.currentUsername'), currentUsername),
      field(t('account.newUsername'), newUsername),
      field(t('account.currentPassword'), currentPassword),
      field(t('account.newPassword'), newPassword),
      field(t('account.confirmPassword'), confirmPassword),
      accountError,
      el('div', { class: 'action-row' }, accountSubmit),
    ),
    el('div', { class: 'action-row' }, button(t('common.close'), { onClick: close })),
  ));
  document.body.appendChild(overlay);
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
    el('nav', { class: 'sidebar-nav', ariaLabel: 'Studio navigation' }),
    el('div', { class: 'sidebar-footer' },
      el('p', {}, t('shell.localMode')),
      el('span', { class: 'soft-pill' }, t('shell.selfHosted')),
    ),
  );
  renderNav();

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
    onClick: () => openAssistantPlanner({ currentPage: 'topbar', mediaType: 'auto' }),
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
    onClick: () => logout(),
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
  document.querySelectorAll('.nav-item').forEach(a => {
    const route = a.getAttribute('href') || '';
    const active = route === current || (route !== '#/dashboard' && current.startsWith(`${route}/`));
    a.classList.toggle('active', active);
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

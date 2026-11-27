import { consumeSessionChangedNotice, login, logout, getLogoutRecovery } from '../auth.js';
import { navigate, createRenderScope } from '../router.js';
import { t } from '../i18n.js';
import { button } from '../components/buttons.js';
import { el, mount } from '../components/dom.js';
import { field, input } from '../components/forms.js';
import { getTheme, toggleTheme } from '../lib/theme.js';

export async function render() {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  const username = input({ type: 'text', autocomplete: 'username', required: true });
  const password = input({ type: 'password', autocomplete: 'current-password', required: true });
  const error = el('p', { class: 'error-text', hidden: true });
  const submit = button(t('login.button'), { variant: 'primary', type: 'submit' });
  let signing = false, retrying = false;
  const recoveryMessage = el('p', { class: 'text-muted' });
  const retry = button(t('login.logoutRetry'), { onClick: retryLogout });
  const recovery = el('section', { class: 'login-recovery', role: 'status', ariaLive: 'polite', hidden: true }, recoveryMessage, retry);

  function refreshRecovery() {
    if (!scope.isActive()) return;
    const { status } = getLogoutRecovery();
    recovery.hidden = status === 'idle';
    const keys = { pending: 'login.logoutPending', unconfirmed: 'login.logoutUnconfirmed', confirmed: 'login.logoutConfirmed' };
    recoveryMessage.textContent = keys[status] ? t(keys[status]) : '';
    retry.hidden = status !== 'unconfirmed' && !retrying;
    retry.disabled = signing || retrying;
    submit.disabled = signing || retrying;
  }

  async function retryLogout() {
    if (!scope.isActive() || signing || retrying) return;
    retrying = true;
    password.value = '';
    const completion = logout();
    refreshRecovery();
    await completion;
    if (!scope.isActive()) return;
    retrying = false;
    refreshRecovery();
    if (getLogoutRecovery().status === 'confirmed' &&
        (document.activeElement === retry || document.activeElement === document.body)) username.focus();
  }
  const theme = button(getTheme() === 'dark' ? t('theme.toLight') : t('theme.toDark'), {
    onClick: () => {
      const next = toggleTheme();
      theme.textContent = next === 'dark' ? t('theme.toLight') : t('theme.toDark');
    },
  });
  theme.classList.add('login-theme');
  const form = el('form', { class: 'form-stack' },
    field(t('login.username'), username),
    field(t('login.password'), password),
    error,
    submit,
  );

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!scope.isActive() || submit.disabled) return;
    error.hidden = true;
    signing = true;
    refreshRecovery();
    submit.textContent = t('login.loggingIn');
    try {
      const signingIn = login(username.value, password.value);
      password.value = '';
      const result = await signingIn;
      if (!scope.isActive()) return;
      if (result) navigate('#/dashboard');
    } catch (err) {
      if (!scope.isActive()) return;
      error.textContent = err?.message || t('login.failed');
      error.hidden = false;
    } finally {
      if (scope.isActive()) {
        signing = false;
        submit.textContent = t('login.button');
        refreshRecovery();
      }
    }
  });

  mount(content,
    el('div', { class: 'login-page' },
      el('section', { class: 'login-card' },
        el('h1', {}, t('login.title')),
        el('p', { class: 'login-copy' }, t('login.copy')),
        consumeSessionChangedNotice() ? el('p', { class: 'field-help', role: 'status', textContent: t('auth.sessionChanged') }) : null,
        recovery,
        form,
        theme,
      ),
    ),
  );
  refreshRecovery();
  // 只等待当前在途观察；离页后由既有scope阻止更新，不增加订阅或轮询。
  const { completion } = getLogoutRecovery();
  if (completion) completion.then(refreshRecovery);
}

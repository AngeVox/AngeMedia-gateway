import { login } from '../auth.js';
import { navigate } from '../router.js';
import { t } from '../i18n.js';

export async function render() {
  const el = document.getElementById('content');
  el.innerHTML = `
    <div class="login-page">
      <div class="login-card card">
        <h2>${t('login.title')}</h2>
        <form id="login-form">
          <label class="field-label">${t('login.username')}
            <input id="login-user" type="text" autocomplete="username" required>
          </label>
          <label class="field-label">${t('login.password')}
            <input id="login-pass" type="password" autocomplete="current-password" required>
          </label>
          <div id="login-error" class="error-text" hidden></div>
          <button id="login-btn" class="btn btn-primary" type="submit">${t('login.button')}</button>
        </form>
      </div>
    </div>`;
  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-btn');
    errEl.hidden = true;
    btn.disabled = true;
    btn.textContent = t('login.loggingIn');
    try {
      await login(
        document.getElementById('login-user').value,
        document.getElementById('login-pass').value,
      );
      navigate('#/dashboard');
    } catch (err) {
      errEl.textContent = err.message || t('login.failed');
      errEl.hidden = false;
      btn.disabled = false;
      btn.textContent = t('login.button');
    }
  });
}

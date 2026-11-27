import { api } from '../api.js';
import { announceSessionChange, captureSessionScope, clearSession } from '../auth.js';
import { navigate } from '../router.js';
import { t } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { field, input } from './forms.js';
import { createModal } from './modal.js?v=web-studio-2j';
import { toast } from './toast.js';

let activeAccount = null, pendingSave = null;

export function openAccountModal() {
  if (activeAccount?.modal.isActive()) {
    if (activeAccount.isSessionCurrent()) { activeAccount.focus(); return activeAccount.modal.overlay; }
    activeAccount.modal.close();
  }
  if (pendingSave?.isSessionCurrent()) { toast(t('common.actionPending')); return; }
  const isSessionCurrent = captureSessionScope();
  let loaded = false, busy = false;
  const currentUsername = input({ type: 'text', disabled: true });
  const newUsername = input({ type: 'text', name: 'new_username', autocomplete: 'username', maxLength: 64, placeholder: t('account.newUsernamePlaceholder') });
  const currentPassword = input({ type: 'password', name: 'current_password', autocomplete: 'current-password', required: true });
  const newPassword = input({ type: 'password', name: 'new_password', autocomplete: 'new-password', minLength: 8 });
  const confirmPassword = input({ type: 'password', name: 'confirm_new_password', autocomplete: 'new-password', minLength: 8 });
  const passwords = [currentPassword, newPassword, confirmPassword];
  const clearPasswords = () => passwords.forEach(node => { node.value = ''; });
  const modal = createModal({ title: t('account.title'), className: 'account-modal', onClose: () => {
    clearPasswords(); currentUsername.value = ''; newUsername.value = ''; loaded = false;
    if (activeAccount?.modal === modal) activeAccount = null;
  } });
  activeAccount = { modal, isSessionCurrent, focus: () => close.focus() };
  const active = () => modal.isActive() && isSessionCurrent();
  const close = button(t('common.close'), { onClick: modal.close });
  const status = el('p', { class: 'field-help', role: 'status' });
  const error = el('p', { class: 'form-error', role: 'alert', hidden: true });
  const retry = button(t('account.reload'), { onClick: load });
  retry.hidden = true;
  const save = button(t('account.saveAccount'), { variant: 'primary', type: 'submit', disabled: true });

  function syncControls() {
    [newUsername, ...passwords].forEach(node => { node.disabled = busy || !loaded; });
    save.disabled = busy || !loaded;
    retry.disabled = busy;
    modal.body.setAttribute('aria-busy', String(busy));
  }

  function showError(message) { error.textContent = message; error.hidden = false; }

  async function load() {
    if (!active() || busy) return;
    busy = true; loaded = false; error.hidden = true; retry.hidden = true;
    status.textContent = t('common.loading'); syncControls();
    try {
      const account = await api.get('/admin/account');
      if (!active()) return;
      if (typeof account?.username !== 'string' || !account.username.trim()) throw new Error();
      currentUsername.value = account.username; loaded = true; status.textContent = '';
    } catch (cause) {
      if (active()) { showError(safeErrorMessage(cause, t('account.loadFailed'))); status.textContent = ''; retry.hidden = false; }
    } finally {
      busy = false;
      if (!isSessionCurrent()) modal.close();
      if (active()) { syncControls(); if (loaded) newUsername.focus(); }
    }
  }

  const form = el('form', { class: 'form-stack' },
    field(t('account.currentUsername'), currentUsername), field(t('account.newUsername'), newUsername),
    field(t('account.currentPassword'), currentPassword), field(t('account.newPassword'), newPassword),
    field(t('account.confirmPassword'), confirmPassword), error, status, retry,
    el('div', { class: 'action-row' }, save),
  );
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!isSessionCurrent()) { modal.close(); return; }
    if (!active() || busy || !loaded) return;
    error.hidden = true;
    const payload = { current_password: currentPassword.value };
    const username = newUsername.value.trim();
    if (username) payload.new_username = username;
    if (newPassword.value || confirmPassword.value) {
      if (newPassword.value !== confirmPassword.value) { showError(t('account.passwordMismatch')); return; }
      payload.new_password = newPassword.value; payload.confirm_new_password = confirmPassword.value;
    }
    if (!payload.new_username && !payload.new_password) { showError(t('account.noChanges')); return; }
    if (!payload.current_password) { showError(t('account.currentPasswordRequired')); return; }
    const operation = { isSessionCurrent };
    pendingSave = operation; busy = true; status.textContent = t('common.actionPending'); syncControls();
    try {
      const request = api.patch('/admin/account', payload);
      // 请求已经序列化；清空界面中的密码，失败后由用户明确重新输入。
      clearPasswords();
      const result = await request;
      if (result?.ok !== true || result?.requires_relogin !== true) throw new Error();
      // 服务端成功会使原会话失效，关窗不撤销结果；新登录的会话不能被旧回调清除。
      if (isSessionCurrent()) {
        if (modal.isActive()) toast(t('account.accountUpdated'), 'success');
        clearSession(); announceSessionChange(); modal.close(); navigate('#/login');
      }
    } catch (cause) {
      if (active()) {
        const message = cause?.safe?.code === 'invalid_current_password' ? t('account.currentPasswordInvalid') :
          safeErrorMessage(cause, t('account.updateUnconfirmed'));
        showError(message); status.textContent = '';
      }
    } finally {
      if (pendingSave === operation) pendingSave = null;
      busy = false; clearPasswords();
      if (!isSessionCurrent()) modal.close();
      if (active()) { syncControls(); currentPassword.focus(); }
    }
  });
  modal.body.append(el('h2', {}, t('account.title')), el('p', { class: 'modal-copy' }, t('account.copy')),
    el('section', { class: 'form-subsection' },
      el('div', { class: 'form-subsection-header' }, el('span', {}, t('account.combinedSection'))), form),
    el('div', { class: 'action-row' }, close));
  syncControls(); modal.show(close); load();
  return modal.overlay;
}

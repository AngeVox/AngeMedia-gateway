import { button, actions } from './buttons.js';
import { el } from './dom.js';
import { t } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { input } from './forms.js';

// 只统一Tab边界；窗口呈现、关闭及业务状态仍由各调用方管理。
export function containModalFocus(root, event) {
  if (event.key !== 'Tab') return;
  const nodes = [...root.querySelectorAll('button,textarea,input,select,summary,a[href],[tabindex="0"]')]
    .filter((node) => !node.disabled && !node.hidden && node.tabIndex >= 0 && node.getClientRects().length);
  const first = nodes[0], last = nodes.at(-1);
  const current = document.activeElement;
  if (!nodes.includes(current) || (event.shiftKey && current === first) || (!event.shiftKey && current === last)) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
  }
}

let scrollUsers = 0, previousOverflow = '';

// 引用计数使嵌套窗口按任意顺序关闭时，都只在最后一层恢复原滚动状态。
export function lockModalScroll() {
  if (scrollUsers++ === 0) previousOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (--scrollUsers === 0) document.body.style.overflow = previousOverflow;
  };
}

export function createModal({ title, className = '', layerClassName = '', onClose }) {
  const overlay = el('div', { class: `modal-overlay native-modal-layer ${layerClassName}`.trim() });
  const body = el('dialog', { class: `modal ${className}`.trim(), ariaLabel: title });
  const previousFocus = document.activeElement;
  let closed = false, release = () => {};
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener('hashchange', close);
    window.removeEventListener('pagehide', close);
    try { onClose?.(); } finally {
      body.close(); overlay.remove(); release();
      if (previousFocus?.isConnected) previousFocus.focus();
    }
  };
  body.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  body.addEventListener('close', close);
  body.addEventListener('keydown', (event) => containModalFocus(body, event));
  overlay.append(body);
  return { overlay, body, close, isActive: () => !closed && overlay.isConnected,
    show(initialFocus) {
      if (closed || overlay.isConnected) return;
      document.body.append(overlay); release = lockModalScroll();
      window.addEventListener('hashchange', close, { once: true });
      window.addEventListener('pagehide', close, { once: true });
      try { body.showModal(); initialFocus?.focus(); } catch (error) { close(); throw error; }
    },
  };
}

export function noticeModal({ title, message, actionLabel, tone = '' }) {
  const modal = createModal({ title, className: tone ? `modal-${tone}` : '' });
  const close = button(actionLabel, { variant: 'primary', onClick: modal.close });
  modal.body.append(el('h2', {}, title), el('p', {}, message), actions(close));
  modal.show(close);
  return modal.overlay;
}

function confirmation(modal, { confirmLabel, danger, onConfirm, errorMessage }, allowed = () => true, cancel) {
  let busy = false;
  const cancelLabel = cancel.textContent;
  const error = el('p', { class: 'form-error', hidden: true, role: 'alert' });
  const status = el('p', { class: 'field-help', hidden: true, role: 'status' }, t('common.actionPending'));
  const confirm = button(confirmLabel, {
    variant: danger ? 'danger' : 'primary',
    disabled: !allowed(),
    onClick: async () => {
      if (busy || !modal.isActive() || !allowed()) return;
      busy = true; confirm.disabled = true; error.hidden = true;
      status.hidden = false; cancel.textContent = t('common.close');
      modal.body.setAttribute('aria-busy', 'true');
      try {
        await onConfirm?.({ isActive: modal.isActive });
        if (modal.isActive()) modal.close();
      } catch (cause) {
        if (modal.isActive()) {
          error.textContent = safeErrorMessage(cause, errorMessage || t('common.actionUnconfirmed'));
          error.hidden = false;
        }
      } finally {
        busy = false;
        if (modal.isActive()) {
          confirm.disabled = !allowed(); modal.body.removeAttribute('aria-busy');
          status.hidden = true; cancel.textContent = cancelLabel;
        }
      }
    },
  });
  return { confirm, error, status, refresh: () => { confirm.disabled = busy || !allowed(); } };
}

export function confirmModal({ title, message, confirmLabel, cancelLabel, danger = false, onConfirm, errorMessage }) {
  const modal = createModal({ title });
  const cancel = button(cancelLabel, { onClick: modal.close });
  const { confirm, error, status } = confirmation(modal, { confirmLabel, danger, onConfirm, errorMessage }, undefined, cancel);
  modal.body.append(
    el('h2', {}, title),
    el('p', {}, message),
    error,
    status,
    actions(cancel, confirm),
  );
  modal.show(cancel);
  return modal.overlay;
}

export function doubleConfirmModal({
  title,
  message,
  secondMessage,
  confirmText,
  confirmLabel,
  cancelLabel,
  danger = true,
  onConfirm,
  errorMessage,
}) {
  const modal = createModal({ title });
  const { body, close } = modal;
  let step = 1;

  function render() {
    body.textContent = '';
    if (step === 1) {
      body.append(
        el('h2', {}, title),
        el('p', {}, message),
        actions(
          button(cancelLabel, { onClick: close }),
          button(confirmLabel, {
            variant: danger ? 'danger' : 'primary',
            onClick: () => {
              step = 2;
              render();
              body.querySelector('input')?.focus();
            },
          }),
        ),
      );
      return;
    }

    const typed = input({
      class: 'form-input',
      type: 'text',
      autocomplete: 'off',
      placeholder: confirmText,
    });
    const cancel = button(cancelLabel, { onClick: close });
    const { confirm, error, status, refresh } = confirmation(modal, { confirmLabel, danger, onConfirm, errorMessage },
      () => typed.value.trim() === confirmText, cancel);
    typed.addEventListener('input', refresh);
    body.append(
      el('h2', {}, title),
      el('p', {}, secondMessage),
      typed,
      error,
      status,
      actions(
        cancel,
        confirm,
      ),
    );
  }

  render();
  modal.show(body.querySelector('button'));
  return modal.overlay;
}

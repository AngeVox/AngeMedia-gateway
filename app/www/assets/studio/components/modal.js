import { button, actions } from './buttons.js';
import { el } from './dom.js';

export function noticeModal({ title, message, actionLabel, tone = '' }) {
  const overlay = el('div', { class: 'modal-overlay' });
  const close = () => overlay.remove();
  overlay.appendChild(el('div', {
    class: ['modal', tone ? `modal-${tone}` : ''].filter(Boolean).join(' '),
    role: 'dialog',
    ariaModal: 'true',
  },
    el('h2', {}, title),
    el('p', {}, message),
    actions(button(actionLabel, { variant: 'primary', onClick: close })),
  ));
  document.body.appendChild(overlay);
  return overlay;
}

export function confirmModal({ title, message, confirmLabel, cancelLabel, danger = false, onConfirm }) {
  const overlay = el('div', { class: 'modal-overlay' });
  const close = () => overlay.remove();
  const confirm = button(confirmLabel, {
    variant: danger ? 'danger' : 'primary',
    onClick: async () => {
      await onConfirm?.();
      close();
    },
  });
  overlay.appendChild(el('div', { class: 'modal', role: 'dialog', ariaModal: 'true' },
    el('h2', {}, title),
    el('p', {}, message),
    actions(
      button(cancelLabel, { onClick: close }),
      confirm,
    ),
  ));
  document.body.appendChild(overlay);
  return overlay;
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
}) {
  const overlay = el('div', { class: 'modal-overlay' });
  const close = () => overlay.remove();
  const body = el('div', { class: 'modal', role: 'dialog', ariaModal: 'true' });
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
              typedFocusLater(body);
            },
          }),
        ),
      );
      return;
    }

    const typed = el('input', {
      class: 'form-input',
      type: 'text',
      autocomplete: 'off',
      placeholder: confirmText,
    });
    const confirm = button(confirmLabel, {
      variant: danger ? 'danger' : 'primary',
      disabled: true,
      onClick: async () => {
        confirm.disabled = true;
        await onConfirm?.();
        close();
      },
    });
    typed.addEventListener('input', () => {
      confirm.disabled = typed.value.trim() !== confirmText;
    });
    body.append(
      el('h2', {}, title),
      el('p', {}, secondMessage),
      typed,
      actions(
        button(cancelLabel, { onClick: close }),
        confirm,
      ),
    );
  }

  render();
  overlay.appendChild(body);
  document.body.appendChild(overlay);
  typedFocusLater(body);
  return overlay;
}

function typedFocusLater(root) {
  setTimeout(() => {
    const input = root.querySelector('input');
    if (input) input.focus();
  }, 0);
}

import { t } from '../i18n.js';
import { button } from './buttons.js';
import { toast } from './toast.js';

async function writeClipboard(text, trigger, isActive) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // HTTPS 权限受限时与局域网 HTTP 使用同一个兼容分支。
    }
  }
  if (!trigger.isConnected || !isActive()) return false;
  const selection = document.createElement('textarea');
  selection.value = text;
  selection.readOnly = true;
  selection.tabIndex = -1;
  selection.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
  (trigger.closest('dialog') || document.body).append(selection);
  try {
    selection.focus({ preventScroll: true });
    selection.select();
    return document.execCommand('copy');
  } catch (_) {
    return false;
  } finally {
    selection.remove();
    if (trigger.isConnected) trigger.focus({ preventScroll: true });
  }
}

export function copyTextButton(text, { label = t('common.copy'), variant, isActive = () => true } = {}) {
  return button(label, {
    variant,
    disabled: !text,
    onClick: async (event) => {
      const trigger = event.currentTarget;
      if (!isActive()) return;
      const copied = await writeClipboard(text, trigger, isActive);
      if (trigger.isConnected && isActive()) {
        toast(t(copied ? 'common.copied' : 'common.copyFailed'), copied ? 'success' : 'error');
      }
    },
  });
}

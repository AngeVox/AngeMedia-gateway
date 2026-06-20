import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';


let activeDrawer = null;


export function closeProviderDrawer() {
  activeDrawer?.close();
}


export function openProviderDrawer({
  title,
  identity = '',
  identityMeta = '',
  description = '',
  status = null,
  trigger = null,
  onClose = null,
  build,
}) {
  closeProviderDrawer();

  const layer = el('div', { class: 'provider-config-drawer-layer' });
  const backdrop = el('div', { class: 'provider-config-drawer-backdrop' });
  const slot = el('div', { class: 'provider-config-drawer-slot' });
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    layer.remove();
    document.documentElement.classList.remove('provider-drawer-open');
    document.removeEventListener('keydown', onKeydown);
    window.removeEventListener('hashchange', close);
    if (activeDrawer?.close === close) activeDrawer = null;
    onClose?.();
    if (trigger?.isConnected) trigger.focus();
  };

  const onKeydown = (event) => {
    if (event.key === 'Escape') close();
  };
  const built = build(close) || {};
  const footerItems = Array.isArray(built.footer) ? built.footer : [built.footer].filter(Boolean);
  const drawer = el('aside', {
    class: 'provider-config-drawer',
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': identity ? `${title}: ${identity}` : title,
  },
    el('header', { class: 'provider-config-drawer-header' },
      el('div', { class: 'truncate' },
        el('div', { class: 'provider-config-title-line' },
          el('h2', {}, title),
          status,
        ),
        identity ? el('p', { class: 'card-title truncate', title: identity }, identity) : null,
        identityMeta ? el('p', { class: 'card-subtitle truncate', title: identityMeta }, identityMeta) : null,
        description ? el('p', { class: 'provider-config-description' }, description) : null,
      ),
      button(t('providers.closeConfig'), { size: 'sm', variant: 'secondary', onClick: close }),
    ),
    el('div', { class: 'provider-config-drawer-body' }, built.content),
    footerItems.length ? el('footer', { class: 'provider-config-drawer-footer' }, footerItems) : null,
  );

  backdrop.addEventListener('click', close);
  slot.appendChild(drawer);
  layer.append(backdrop, slot);
  document.body.appendChild(layer);
  document.documentElement.classList.add('provider-drawer-open');
  document.addEventListener('keydown', onKeydown);
  window.addEventListener('hashchange', close, { once: true });
  activeDrawer = { close };
  (built.initialFocus || drawer.querySelector('input, select, textarea, button'))?.focus();
  return close;
}

import { t } from '../../i18n.js';
import { captureSessionScope } from '../../auth.js';
import { bindViewResources, createPendingScope, createRenderScope } from '../../router.js';
import { createModal } from '../../components/modal.js?v=web-studio-2j';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';

let activeDrawer = null, drawerVersion = 0;

export function closeProviderDrawer() {
  drawerVersion += 1;
  activeDrawer?.close();
}

// 详情尚未返回时也登记打开意图，创建/另一渠道/离页都能淘汰旧读取。
export function beginProviderDrawerRequest(trigger) {
  closeProviderDrawer();
  const version = drawerVersion, view = createRenderScope(trigger), session = captureSessionScope();
  return createPendingScope(() => version === drawerVersion && view.isActive() && session());
}

export function openProviderDrawer({ title, identity = '', identityMeta = '', description = '',
  status = null, trigger = null, onClose = null, build }) {
  closeProviderDrawer();
  const view = createRenderScope(trigger), session = captureSessionScope();
  let built = null, resources = null;
  const modal = createModal({
    title: identity ? `${title}: ${identity}` : title,
    className: 'provider-config-drawer', layerClassName: 'provider-config-drawer-layer',
    onClose: () => {
      resources?.close();
      modal.body.querySelectorAll('input[type="password"]').forEach(node => { node.value = ''; });
      if (activeDrawer === modal) activeDrawer = null;
      built?.dispose?.(); onClose?.();
    },
  });
  const isActive = () => modal.isActive() && view.isActive() && session();
  built = build(modal.close, { isActive }) || {};
  const footer = Array.isArray(built.footer) ? built.footer : [built.footer].filter(Boolean);
  modal.body.append(
    el('header', { class: 'provider-config-drawer-header' },
      el('div', { class: 'truncate' },
        el('div', { class: 'provider-config-title-line' }, el('h2', {}, title), status),
        identity ? el('p', { class: 'card-title truncate', title: identity }, identity) : null,
        identityMeta ? el('p', { class: 'card-subtitle truncate', title: identityMeta }, identityMeta) : null,
        description ? el('p', { class: 'provider-config-description' }, description) : null,
      ),
      button(t('providers.closeConfig'), { size: 'sm', variant: 'secondary', onClick: modal.close }),
    ),
    el('div', { class: 'provider-config-drawer-body' }, built.content),
    footer.length ? el('footer', { class: 'provider-config-drawer-footer' }, footer) : null,
  );
  modal.body.addEventListener('click', event => {
    if (event.target !== modal.body) return;
    const box = modal.body.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) modal.close();
  });
  modal.show(built.initialFocus || modal.body.querySelector('input, select, textarea, button'));
  activeDrawer = modal;
  resources = bindViewResources(() => view.isActive() && session(), { dispose: modal.close, suspend: modal.close, resume() {} });
  return modal.close;
}

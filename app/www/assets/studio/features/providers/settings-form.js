import { captureSessionScope } from '../../auth.js';
import { createRenderScope, bindViewResources } from '../../router.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';

// 每组设置有一个写入入口。同会话重建时，先等旧写入结束，再读取当前值。
const writes = new Map();

export function bindSettingsForm(section, {
  load, write, apply, sync, status, saveError, clearSecrets = () => {},
}) {
  const view = createRenderScope(section);
  const session = captureSessionScope();
  const retry = button(t('common.refresh'), { size: 'sm', onClick: () => { if (!retryRow.hidden) return read(); } });
  const retryRow = el('div', { hidden: true }, retry);
  let ready = false;
  const resources = bindViewResources(() => view.isActive() && session(), {
    dispose: clearSecrets,
    suspend: () => { view.nextRequest(); ready = false; sync(true); clearSecrets(); },
    resume: read,
  });

  async function read() {
    if (!resources.isActive()) return;
    const current = view.nextRequest();
    const active = () => current() && resources.isActive();
    ready = false;
    sync(true);
    retryRow.hidden = true;
    const previous = writes.get(load);
    status.textContent = t(previous?.session() ? 'common.actionPending' : 'providers.loadingConnectionSettings');
    if (previous?.session()) await previous.done.catch(() => {});
    if (!active()) return;
    status.textContent = t('providers.loadingConnectionSettings');
    try {
      const result = await load();
      if (!active()) return;
      status.textContent = apply(result);
      ready = true;
      sync(false);
    } catch (error) {
      if (!active()) return;
      status.textContent = safeErrorMessage(error, t('providers.connectionSettingsLoadError'));
      retryRow.hidden = false;
    }
  }

  async function save(payload, success, accept = apply) {
    if (!ready || !resources.isActive() || writes.get(load)?.session()) return false;
    const current = view.nextRequest();
    const active = () => current() && resources.isActive();
    const lease = { session };
    writes.set(load, lease);
    sync(true);
    clearSecrets();
    try {
      lease.done = write(payload);
      const result = await lease.done;
      if (!active()) return false;
      status.textContent = accept(result);
      toast(t(success), 'success');
      return true;
    } catch (error) {
      if (active()) toast(safeErrorMessage(error, t(saveError)), 'error');
      return false;
    } finally {
      if (writes.get(load) === lease) writes.delete(load);
      if (active()) sync(!ready);
    }
  }

  section.append(retryRow);
  sync(true);
  queueMicrotask(read);
  return { save, isLocked: () => !ready || !resources.isActive() || !!writes.get(load)?.session() };
}

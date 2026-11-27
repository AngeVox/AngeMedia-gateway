import { api } from '../../api.js';
import { captureSessionScope } from '../../auth.js';
import { createPendingScope } from '../../router.js';
import { t } from '../../i18n.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { prepareConnectionEvidence } from './connection-evidence.js';
import { hasProviderSecretField } from './provider-validation.js';
import { createProviderActionGate } from './provider-action-gate.js';

const { action, acquire, subscribe: subscribeActions } = createProviderActionGate();

export function createBuiltinState(providers, owner) {
  const snapshots = new Map(providers.map(provider => [provider.provider_id, prepare(provider)]));
  const subscribers = new Map();
  let readVersion = 0, disposed = false, suspended = false;
  const isActive = () => !disposed && !suspended && owner();
  function prepare(provider) {
    return { ...provider, connection_evidence: prepareConnectionEvidence(provider.connection_evidence) };
  }
  function subscribe(id, render, { isCurrent = () => true, dispose = () => {} } = {}) {
    const entries = subscribers.get(id) || new Set();
    subscribers.set(id, entries);
    const entry = { render, isCurrent, dispose, close: () => { if (entries.delete(entry)) dispose(); } };
    entries.add(entry); render(snapshots.get(id), action(id));
    return entry.close;
  }
  function notify(id) {
    if (!isActive()) return;
    subscribers.get(id)?.forEach(entry => {
      if (!entry.isCurrent()) entry.close();
      else entry.render(snapshots.get(id), action(id));
    });
  }
  function setEvidence(id, evidence) {
    readVersion += 1;
    snapshots.set(id, { ...snapshots.get(id), connection_evidence: prepareConnectionEvidence(evidence) });
    notify(id);
  }
  function accept(id, provider) {
    if (provider?.provider_id !== id || hasProviderSecretField(provider)) return false;
    readVersion += 1; snapshots.set(id, prepare(provider)); notify(id); return true;
  }
  async function refresh(id, isCurrent = () => true) {
    if (!isActive() || !isCurrent()) return null;
    const request = ++readVersion;
    const current = () => isActive() && isCurrent() && request === readVersion;
    try {
      const response = await api.get('/admin/provider-configs');
      if (!current()) return null;
      if (hasProviderSecretField(response) || !Array.isArray(response?.data)
          || !response.data.some(provider => provider.provider_id === id)) throw new Error();
      response.data.forEach(provider => {
        if (snapshots.has(provider.provider_id)) snapshots.set(provider.provider_id, prepare(provider));
      });
      snapshots.forEach((_provider, providerId) => notify(providerId));
      return true;
    } catch (_) {
      if (!current()) return null;
      setEvidence(id, { status: 'not_tested', message: t('providers.connectionRetestHint') });
      return false;
    }
  }
  async function perform(id, owner, kind, task) {
    if (!isActive() || !owner()) return;
    if (action(id)) { toast(t('common.actionPending')); return; }
    const release = acquire(id, kind, changed);
    const session = captureSessionScope();
    const pending = createPendingScope(() => isActive() && owner() && session());
    try { await task(pending.isActive); } finally {
      pending.close();
      // 窗口关闭不撤回已发操作；只给仍属原会话的列表补读事实，不回填旧窗口。
      if (isActive() && session() && !owner()) await refresh(id, session);
      release();
    }
  }
  function test(id, owner) {
    return perform(id, owner, 'test', async current => {
      try {
        const response = await api.post(`/admin/provider-configs/${encodeURIComponent(id)}/test`, {});
        if (!current()) return;
        if (hasProviderSecretField(response)) {
          setEvidence(id, { status: 'failed', message: t('providers.securityError') }); return;
        }
        setEvidence(id, response?.data || {});
        await refresh(id, current);
      } catch (error) {
        if (current()) setEvidence(id, { status: 'failed', message: safeErrorMessage(error, t('providers.builtinTestError')) });
      }
    });
  }
  function changed(id, settled, source) {
    readVersion += 1;
    if (settled && source !== changed && isActive()) refresh(id);
    else notify(id);
  }
  function pause() {
    suspended = true; readVersion += 1;
    subscribers.forEach(entries => entries.forEach(entry => entry.dispose()));
  }
  function resume() {
    suspended = false;
    snapshots.forEach((_provider, id) => notify(id));
    const id = snapshots.keys().next().value;
    if (id) refresh(id);
  }
  function dispose() {
    if (disposed) return;
    disposed = true; readVersion += 1; unsubscribeActions();
    subscribers.forEach(entries => [...entries].forEach(entry => entry.close()));
    subscribers.clear();
  }
  const unsubscribeActions = subscribeActions(changed);
  return { action, subscribe, refresh, perform, test, accept, pause, resume, dispose, get: id => snapshots.get(id) };
}

import { captureSessionScope } from '../../auth.js';

// 在途动作属于发起会话和渠道，重建卡片或抽屉不会提前解锁。
export function createProviderActionGate() {
  const pending = new Map();
  const listeners = new Set();

  function action(id) {
    const lease = pending.get(id);
    return lease?.session() ? lease.kind : null;
  }

  function acquire(id, kind, source) {
    if (action(id)) return null;
    const lease = { kind, session: captureSessionScope() };
    pending.set(id, lease);
    listeners.forEach(listener => listener(id, false, source));
    return function release(reconcile = true) {
      if (pending.get(id) !== lease) return;
      pending.delete(id);
      if (lease.session()) listeners.forEach(listener => listener(id, reconcile, source));
    };
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  return { action, acquire, subscribe };
}

export const customProviderActions = createProviderActionGate();

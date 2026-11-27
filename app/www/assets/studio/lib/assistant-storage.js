const PREFIX = 'studio_assistant_private_v1:';
const uuid = /^[a-f0-9]{32}$/;

export function assistantStorage(storage, owner) {
  const key = PREFIX + encodeURIComponent(owner);
  return {
    read() {
      try {
        const value = JSON.parse(storage.getItem(key));
        if (!value || value.owner !== owner) return {};
        const sessionId = uuid.test(value.sessionId || '') ? value.sessionId : null;
        const pending = value.pending;
        if (pending && (!uuid.test(pending.key) || !uuid.test(pending.sessionId)
            || typeof pending.message !== 'string' || !pending.message.trim() || pending.message.length > 8000)) return { sessionId };
        return { sessionId, pending };
      } catch (_) { return {}; }
    },
    write(value) {
      try { storage.setItem(key, JSON.stringify({ ...value, owner })); } catch (_) { /* 浏览器禁用存储时仍可在当前窗口恢复。 */ }
    },
    clear() { try { storage.removeItem(key); } catch (_) { /* 存储不可用不影响关闭。 */ } },
  };
}

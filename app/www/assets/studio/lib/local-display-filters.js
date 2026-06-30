const MAX_HIDDEN_IDS = 500;

function readJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || '') || fallback;
  } catch (_) {
    return fallback;
  }
}

function writeJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

export function hiddenIdSet(key) {
  const items = readJson(key, []);
  return new Set(Array.isArray(items) ? items.filter(Boolean) : []);
}

export function hideIds(key, ids) {
  const merged = [...hiddenIdSet(key), ...ids.filter(Boolean)];
  writeJson(key, merged.slice(-MAX_HIDDEN_IDS));
}

export function clearHiddenIds(key) {
  localStorage.removeItem(key);
}

export function hiddenSince(key) {
  const value = Number(localStorage.getItem(key) || 0);
  return Number.isFinite(value) ? value : 0;
}

export function hideOlderThanNow(key) {
  localStorage.setItem(key, String(Date.now()));
}

export function clearHiddenSince(key) {
  localStorage.removeItem(key);
}

export function isAfterHiddenSince(item, key) {
  const since = hiddenSince(key);
  if (!since) return true;
  const raw = item?.created_at || item?.updated_at;
  const time = raw ? Date.parse(raw) : 0;
  return Number.isFinite(time) && time > since;
}

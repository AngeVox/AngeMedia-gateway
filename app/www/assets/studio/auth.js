import { api } from './api.js';

let session = null;

export async function login(username, password) {
  const res = await api.post('/admin/login', { username, password });
  session = { authenticated: true, username: res.username };
  return res;
}

export async function logout() {
  try { await api.post('/admin/logout'); } catch (_) { /* ignore */ }
  session = null;
  location.hash = '#/login';
}

export async function getSession() {
  if (session) return session;
  try {
    const res = await api.get('/admin/session');
    if (res.authenticated) {
      session = { authenticated: true, username: res.username || '' };
      return session;
    }
  } catch (_) { /* ignore */ }
  session = null;
  return null;
}

export function requireSession() {
  if (!session) {
    location.hash = '#/login';
    return false;
  }
  return true;
}

export function clearSession() {
  session = null;
}

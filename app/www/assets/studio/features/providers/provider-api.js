import { api } from '../../api.js';

export function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

export async function loadProviders() {
  return api.get('/admin/providers');
}

export async function loadCatalog() {
  return api.get('/admin/catalog').catch(() => ({ providers: [] }));
}

export async function loadBuiltinProviderConfigs() {
  return api.get('/admin/provider-configs');
}

export async function loadAdminConfig() {
  return api.get('/admin/config');
}
export async function loadGlobalProviderTransport() {
  return api.get('/admin/provider-transport');
}

export async function saveGlobalProviderTransport(payload) {
  return api.post('/admin/provider-transport', payload);
}

export async function loadProviderTransport(providerId) {
  return api.get(`/admin/provider-transport/`);
}

export async function saveProviderTransport(providerId, payload) {
  return api.post(`/admin/provider-transport/`, payload);
}

export async function loadReferenceRelay() {
  return api.get('/admin/reference-relay');
}

export async function saveReferenceRelay(payload) {
  return api.post('/admin/reference-relay', payload);
}

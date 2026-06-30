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

import { api } from '../api.js';

export async function loadAssistantConfig() {
  return api.get('/admin/config');
}

function assistantRuntimePayload({ baseUrl, apiKey, model = '', useEmptyApiKey = false }) {
  const payload = {
    base_url: String(baseUrl || '').trim(),
  };
  const key = String(apiKey || '').trim();
  if (key) payload.api_key = key;
  if (useEmptyApiKey) payload.use_empty_api_key = true;
  if (model) payload.model = String(model).trim();
  return payload;
}

export async function fetchAssistantModels(form) {
  return api.post('/admin/assistant/models', assistantRuntimePayload(form));
}

export async function testAssistantConnection(form) {
  return api.post('/admin/assistant/test', assistantRuntimePayload(form));
}

export async function saveAssistantSettings({ enabled, baseUrl, apiKey, model }) {
  const settings = {
    ANGE_ASSISTANT_ENABLED: enabled ? 'true' : 'false',
    ANGE_LLM_BASE_URL: String(baseUrl || '').trim(),
    ANGE_LLM_MODEL: String(model || '').trim(),
  };
  const key = String(apiKey || '').trim();
  if (key) settings.ANGE_LLM_API_KEY = key;
  return api.post('/admin/config', { settings });
}

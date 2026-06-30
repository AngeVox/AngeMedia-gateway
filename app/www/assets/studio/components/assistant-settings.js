import { button } from './buttons.js';
import { el } from './dom.js';
import { field, input, select, toggle } from './forms.js';
import { t } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { safeText } from '../lib/security.js';
import { toast } from './toast.js';
import {
  fetchAssistantModels,
  loadAssistantConfig,
  saveAssistantSettings,
  testAssistantConnection,
} from './assistant-settings-api.js?v=web-studio-2h';

function closeOverlay(overlay) {
  overlay.remove();
}

function modelOptions(models, current) {
  const values = [...new Set(models.filter(Boolean))];
  if (current && values.includes(current)) {
    values.sort((left, right) => (left === current ? -1 : right === current ? 1 : left.localeCompare(right)));
  }
  return values.map((value) => ({ value, label: value, selected: value === current }));
}

function setStatus(node, text, kind = 'muted') {
  node.className = `assistant-settings-status assistant-settings-status-${kind}`;
  node.textContent = text;
}

export function openAssistantSettings() {
  const overlay = el('div', { class: 'modal-overlay assistant-settings-layer' });
  const enabledToggle = toggle(t('assistantSettings.enabled'), { checked: false });
  const enabledInput = enabledToggle.querySelector('input');
  const baseUrlInput = input({ type: 'url', autocomplete: 'url', placeholder: 'https://api.openai.com/v1' });
  const keyInput = input({ type: 'password', autocomplete: 'new-password', placeholder: t('assistantSettings.keyPlaceholder') });
  const emptyKeyToggle = toggle(t('assistantSettings.noApiKey'), { checked: false });
  const emptyKeyInput = emptyKeyToggle.querySelector('input');
  const modelInput = input({ type: 'text', autocomplete: 'off', placeholder: t('assistantSettings.modelPlaceholder') });
  const modelSelectWrap = el('div', { class: 'assistant-model-select-wrap' });
  const status = el('p', { class: 'assistant-settings-status assistant-settings-status-muted' }, t('assistantSettings.loading'));
  const save = button(t('assistantSettings.save'), { variant: 'primary' });
  const fetchModels = button(t('assistantSettings.fetchModels'), { size: 'sm' });
  const test = button(t('assistantSettings.test'), { size: 'sm' });

  async function load() {
    try {
      const config = await loadAssistantConfig();
      const assistant = config?.assistant || {};
      enabledInput.checked = assistant.enabled === true;
      baseUrlInput.value = assistant.llm_base_url || '';
      modelInput.value = assistant.llm_model || '';
      setStatus(status, assistant.configured ? t('assistantSettings.configured') : t('assistantSettings.notConfigured'), assistant.configured ? 'success' : 'warning');
    } catch (error) {
      setStatus(status, safeErrorMessage(error, t('assistantSettings.loadError')), 'danger');
    }
  }

  async function loadModels() {
    const baseUrl = baseUrlInput.value.trim();
    if (!baseUrl) {
      setStatus(status, t('assistantSettings.baseUrlRequired'), 'danger');
      return;
    }
    fetchModels.disabled = true;
    fetchModels.textContent = t('assistantSettings.fetchingModels');
    try {
      const result = await fetchAssistantModels({
        baseUrl,
        apiKey: keyInput.value,
        useEmptyApiKey: emptyKeyInput.checked,
      });
      const models = Array.isArray(result?.data) ? result.data.map((item) => String(item)).filter(Boolean) : [];
      if (models.length && !models.includes(modelInput.value.trim())) {
        modelInput.value = models[0];
      }
      const selector = select(modelOptions(models, modelInput.value.trim()), {
        onchange: () => {
          modelInput.value = selector.value;
        },
      });
      modelSelectWrap.replaceChildren(field(t('assistantSettings.modelList'), selector, { help: t('assistantSettings.modelListHelp') }));
      setStatus(status, `${t('assistantSettings.modelsLoaded')} ${models.length} · ${t(`assistantSettings.keySource.${result?.key_source || 'saved'}`)}`, 'success');
    } catch (error) {
      setStatus(status, safeErrorMessage(error, t('assistantSettings.modelsError')), 'danger');
    } finally {
      fetchModels.disabled = false;
      fetchModels.textContent = t('assistantSettings.fetchModels');
    }
  }

  async function testConnection() {
    const baseUrl = baseUrlInput.value.trim();
    const model = modelInput.value.trim();
    if (!baseUrl || !model) {
      setStatus(status, t('assistantSettings.required'), 'danger');
      return;
    }
    test.disabled = true;
    test.textContent = t('assistantSettings.testing');
    try {
      const result = await testAssistantConnection({
        baseUrl,
        apiKey: keyInput.value,
        model,
        useEmptyApiKey: emptyKeyInput.checked,
      });
      setStatus(status, `${t('assistantSettings.testOk')} · ${t(`assistantSettings.keySource.${result?.key_source || 'saved'}`)} · ${safeText(result?.preview || result?.model || '', 120)}`, 'success');
    } catch (error) {
      setStatus(status, safeErrorMessage(error, t('assistantSettings.testFailed')), 'danger');
    } finally {
      test.disabled = false;
      test.textContent = t('assistantSettings.test');
    }
  }

  async function saveSettings() {
    const baseUrl = baseUrlInput.value.trim();
    const model = modelInput.value.trim();
    if (!baseUrl || !model) {
      setStatus(status, t('assistantSettings.required'), 'danger');
      return;
    }
    save.disabled = true;
    try {
      await saveAssistantSettings({
        enabled: enabledInput.checked,
        baseUrl,
        apiKey: keyInput.value,
        model,
      });
      keyInput.value = '';
      emptyKeyInput.checked = false;
      setStatus(status, t('assistantSettings.saved'), 'success');
      toast(t('assistantSettings.saved'), 'success');
    } catch (error) {
      setStatus(status, safeErrorMessage(error, t('assistantSettings.saveError')), 'danger');
    } finally {
      save.disabled = false;
    }
  }

  fetchModels.addEventListener('click', loadModels);
  test.addEventListener('click', testConnection);
  save.addEventListener('click', saveSettings);

  overlay.appendChild(el('div', { class: 'modal assistant-settings-modal', role: 'dialog', ariaModal: 'true' },
    el('div', { class: 'prompt-copilot-header' },
      el('div', {},
        el('p', { class: 'kicker' }, 'ANGE ASSISTANT'),
        el('h2', {}, t('assistantSettings.title')),
      ),
      button(t('common.close'), { onClick: () => closeOverlay(overlay) }),
    ),
    el('p', { class: 'modal-copy' }, t('assistantSettings.copy')),
    enabledToggle,
    field(t('assistantSettings.baseUrl'), baseUrlInput, { help: t('assistantSettings.baseUrlHelp') }),
    field(t('assistantSettings.apiKey'), keyInput, { help: t('assistantSettings.keyHelp') }),
    emptyKeyToggle,
    field(t('assistantSettings.model'), modelInput, { help: t('assistantSettings.modelHelp') }),
    modelSelectWrap,
    status,
    el('div', { class: 'action-row assistant-settings-actions' },
      fetchModels,
      test,
      button(t('common.close'), { onClick: () => closeOverlay(overlay) }),
      save,
    ),
  ));
  document.body.appendChild(overlay);
  load();
}

import { button } from './buttons.js';
import { el } from './dom.js';
import { field, input, select, toggle } from './forms.js';
import { t } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { safeText } from '../lib/security.js';
import { toast } from './toast.js';
import { containModalFocus, lockModalScroll } from './modal.js?v=web-studio-2j';
import {
  fetchAssistantModels,
  loadAssistantConfig,
  saveAssistantSettings,
  testAssistantConnection,
} from './assistant-settings-api.js?v=web-studio-2j';

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

function keySourceText(source = 'saved') {
  const key = 'assistantSettings.keySource.' + source;
  const translated = t(key);
  return translated === key ? t('assistantSettings.keySource.unknown') : translated;
}

export function createAssistantSettings({ onSaved, onClose, closeLabel = t('common.close') } = {}) {
  let disposed = false, ready = false, busy = false;
  const enabledToggle = toggle(t('assistantSettings.enabled'), { checked: false });
  const enabledInput = enabledToggle.querySelector('input');
  const baseUrlInput = input({ type: 'url', autocomplete: 'url', placeholder: 'https://api.openai.com/v1' });
  const keyInput = input({ type: 'password', autocomplete: 'new-password', placeholder: t('assistantSettings.keyPlaceholder') });
  const emptyKeyToggle = toggle(t('assistantSettings.noApiKey'), { checked: false });
  const emptyKeyInput = emptyKeyToggle.querySelector('input');
  const modelInput = input({ type: 'text', autocomplete: 'off', placeholder: t('assistantSettings.modelPlaceholder') });
  const modelSelectWrap = el('div', { class: 'assistant-model-select-wrap' });
  const status = el('p', { class: 'assistant-settings-status assistant-settings-status-muted',
    role: 'status', ariaLive: 'polite', ariaAtomic: 'true' }, t('assistantSettings.loading'));
  const save = button(t('assistantSettings.save'), { variant: 'primary' });
  const fetchModels = button(t('assistantSettings.fetchModels'), { size: 'sm' });
  const test = button(t('assistantSettings.test'), { size: 'sm' });
  const retryLoad = button(t('assistantSettings.retryLoad'), { size: 'sm', onClick: load });

  function syncControls() {
    if (disposed) return;
    node.querySelectorAll('input,select,textarea').forEach((control) => { control.disabled = busy || !ready; });
    [save, fetchModels, test].forEach((control) => { control.disabled = busy || !ready; });
    retryLoad.hidden = ready || busy;
  }

  async function load() {
    if (disposed || busy) return;
    busy = true; syncControls();
    setStatus(status, t('assistantSettings.loading'));
    try {
      const config = await loadAssistantConfig();
      if (disposed) return;
      const assistant = config?.assistant || {};
      enabledInput.checked = assistant.enabled === true;
      baseUrlInput.value = assistant.llm_base_url || '';
      modelInput.value = assistant.llm_model || '';
      ready = true;
      setStatus(status, assistant.configured ? t('assistantSettings.configured') : t('assistantSettings.notConfigured'), assistant.configured ? 'success' : 'warning');
    } catch (error) {
      if (!disposed) setStatus(status, safeErrorMessage(error, t('assistantSettings.loadError')), 'danger');
    } finally {
      busy = false; syncControls();
    }
  }

  async function loadModels() {
    if (disposed || busy || !ready) return;
    const baseUrl = baseUrlInput.value.trim();
    if (!baseUrl) {
      setStatus(status, t('assistantSettings.baseUrlRequired'), 'danger');
      return;
    }
    busy = true; syncControls();
    fetchModels.textContent = t('assistantSettings.fetchingModels');
    try {
      const result = await fetchAssistantModels({
        baseUrl,
        apiKey: keyInput.value,
        useEmptyApiKey: emptyKeyInput.checked,
      });
      if (disposed) return;
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
      setStatus(status, t('assistantSettings.modelsLoaded')
        .replace('{count}', models.length).replace('{source}', keySourceText(result?.key_source)), 'success');
    } catch (error) {
      if (!disposed) setStatus(status, safeErrorMessage(error, t('assistantSettings.modelsError')), 'danger');
    } finally {
      busy = false; syncControls();
      fetchModels.textContent = t('assistantSettings.fetchModels');
    }
  }

  async function testConnection() {
    if (disposed || busy || !ready) return;
    const baseUrl = baseUrlInput.value.trim();
    const model = modelInput.value.trim();
    if (!baseUrl || !model) {
      setStatus(status, t('assistantSettings.required'), 'danger');
      return;
    }
    busy = true; syncControls();
    test.textContent = t('assistantSettings.testing');
    try {
      const result = await testAssistantConnection({
        baseUrl,
        apiKey: keyInput.value,
        model,
        useEmptyApiKey: emptyKeyInput.checked,
      });
      if (disposed) return;
      setStatus(status, `${t('assistantSettings.testOk')} · ${keySourceText(result?.key_source)} · ${safeText(result?.preview || result?.model || '', 120)}`, 'success');
    } catch (error) {
      if (!disposed) setStatus(status, safeErrorMessage(error, t('assistantSettings.testFailed')), 'danger');
    } finally {
      busy = false; syncControls();
      test.textContent = t('assistantSettings.test');
    }
  }

  async function saveSettings() {
    if (disposed || busy || !ready) return;
    const baseUrl = baseUrlInput.value.trim();
    const model = modelInput.value.trim();
    if (!baseUrl || !model) {
      setStatus(status, t('assistantSettings.required'), 'danger');
      return;
    }
    busy = true; syncControls();
    try {
      await saveAssistantSettings({
        enabled: enabledInput.checked,
        baseUrl,
        apiKey: keyInput.value,
        model,
      });
      if (!disposed) { keyInput.value = ''; emptyKeyInput.checked = false; }
      // 保存已经到达服务端；即使用户返回对话，也应刷新仍在使用的运行配置。
      await onSaved?.();
      if (disposed) return;
      setStatus(status, t('assistantSettings.saved'), 'success');
      toast(t('assistantSettings.saved'), 'success');
    } catch (error) {
      if (!disposed) setStatus(status, safeErrorMessage(error, t('assistantSettings.saveError')), 'danger');
    } finally {
      busy = false; syncControls();
    }
  }

  fetchModels.addEventListener('click', loadModels);
  test.addEventListener('click', testConnection);
  save.addEventListener('click', saveSettings);

  const node = el('div', { class: 'assistant-settings-form form-stack' },
    el('div', { class: 'prompt-copilot-header' },
      el('div', {},
        el('p', { class: 'kicker' }, t('assistantSettings.kicker')),
        el('h2', {}, t('assistantSettings.title')),
      ),
      button(closeLabel, { onClick: onClose }),
    ),
    el('p', { class: 'modal-copy' }, t('assistantSettings.copy')),
    enabledToggle,
    field(t('assistantSettings.baseUrl'), baseUrlInput, { help: t('assistantSettings.baseUrlHelp') }),
    field(t('assistantSettings.apiKey'), keyInput, { help: t('assistantSettings.keyHelp') }),
    emptyKeyToggle,
    field(t('assistantSettings.model'), modelInput, { help: t('assistantSettings.modelHelp') }),
    modelSelectWrap,
    status,
    retryLoad,
    el('div', { class: 'action-row assistant-settings-actions' },
      fetchModels,
      test,
      button(closeLabel, { onClick: onClose }),
      save,
    ),
  );
  load();
  return { node, focus: () => node.querySelector('button')?.focus(), dispose: () => {
    disposed = true; keyInput.value = ''; emptyKeyInput.checked = false;
  } };
}

export function openAssistantSettings({ onSaved } = {}) {
  const existing = document.querySelector('dialog.assistant-settings-modal');
  if (existing) { existing.querySelector('button')?.focus(); return; }
  const previousFocus = document.activeElement;
  const releaseScroll = lockModalScroll();
  const dialog = el('dialog', { class: 'modal assistant-settings-modal', ariaLabel: t('assistantSettings.title') });
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true; view.dispose(); dialog.close(); dialog.remove();
    window.removeEventListener('hashchange', close);
    releaseScroll();
    if (previousFocus?.isConnected) previousFocus.focus();
  };
  const view = createAssistantSettings({ onSaved, onClose: close });
  dialog.append(view.node);
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); close(); });
  dialog.addEventListener('keydown', (event) => containModalFocus(dialog, event));
  dialog.addEventListener('close', close);
  window.addEventListener('hashchange', close, { once: true });
  document.body.append(dialog); dialog.showModal(); view.focus();
  return close;
}

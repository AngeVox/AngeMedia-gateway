import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, select } from '../../components/forms.js';
import { panel } from '../../components/page.js';
import { bindSettingsForm } from './settings-form.js';
import { transportStateText } from './provider-transport.js?v=provider-connectivity-1';
import {
  loadGlobalProviderTransport,
  loadReferenceRelay,
  saveGlobalProviderTransport,
  saveReferenceRelay,
} from './provider-api.js';

function configuredText(value) {
  return value ? t('providers.savedValueConfigured') : t('providers.savedValueEmpty');
}

function connectionBlock(title, help, save, ...fields) {
  return el('section', { class: 'provider-connectivity-block' },
    el('div', { class: 'provider-config-section-heading' },
      el('div', {}, el('h3', {}, t(title)), el('p', { class: 'field-help' }, t(help))),
      save,
    ),
    fields,
  );
}

function transportBlock(onSaved) {
  const mode = select([
    { value: 'direct', label: t('providers.transportDirect') },
    { value: 'explicit_proxy', label: t('providers.transportProxy') },
  ]);
  const proxy = input({
    type: 'password',
    autocomplete: 'new-password',
    placeholder: t('providers.proxyPlaceholder'),
  });
  const status = el('p', { class: 'field-help', role: 'status' });
  const save = button(t('providers.transportSave'), {
    size: 'sm', variant: 'primary',
    onClick: async () => {
      const payload = { transport_mode: mode.value };
      const proxyValue = proxy.value.trim();
      if (mode.value === 'explicit_proxy' && proxyValue) payload.proxy_url = proxyValue;
      if (await form.save(payload, 'providers.transportSaved')) onSaved();
    },
  });
  const section = connectionBlock('providers.globalTransportTitle', 'providers.globalTransportHelp', save,
    field(t('providers.transportMode'), mode),
    field(t('providers.proxyUrl'), proxy, { help: t('providers.proxyWriteOnlyHelp') }),
    status,
  );
  const form = bindSettingsForm(section, {
    load: loadGlobalProviderTransport,
    write: saveGlobalProviderTransport,
    saveError: 'providers.transportSaveError',
    status,
    clearSecrets: () => { proxy.value = ''; },
    apply: ({ data }) => {
      if (!['direct', 'explicit_proxy'].includes(data?.effective_mode)) throw new Error();
      mode.value = data.transport_mode || data.effective_mode;
      return transportStateText(data);
    },
    sync: locked => {
      save.disabled = mode.disabled = locked;
      proxy.disabled = locked || mode.value !== 'explicit_proxy';
    },
  });
  mode.addEventListener('change', () => {
    proxy.value = '';
    proxy.disabled = form.isLocked() || mode.value !== 'explicit_proxy';
  });
  return section;
}

function relayStatus(data) {
  if (!['disabled', 'external_http'].includes(data?.mode)) throw new Error();
  return t('providers.relayEndpointState') + ': ' + configuredText(data.upload_url_configured)
    + ' · ' + t('providers.relayTokenState') + ': ' + configuredText(data.token_configured);
}

function relayBlock() {
  const mode = select([
    { value: 'disabled', label: t('providers.relayDisabled') },
    { value: 'external_http', label: t('providers.relayExternalHttp') },
  ]);
  const uploadUrl = input({ type: 'password', autocomplete: 'new-password', placeholder: t('providers.relayUrlPlaceholder') });
  const token = input({ type: 'password', autocomplete: 'new-password', placeholder: t('providers.relayTokenPlaceholder') });
  const status = el('p', { class: 'field-help', role: 'status' });
  const save = button(t('providers.relaySave'), {
    size: 'sm', variant: 'primary',
    onClick: () => {
      const payload = { mode: mode.value };
      if (mode.value === 'external_http') {
        const urlValue = uploadUrl.value.trim();
        const tokenValue = token.value.trim();
        if (urlValue) payload.upload_url = urlValue;
        if (tokenValue) payload.token = tokenValue;
      }
      return form.save(payload, 'providers.relaySaved');
    },
  });
  const clearToken = button(t('providers.relayClearToken'), {
    size: 'sm',
    // 清令牌只修改令牌，保留用户还没保存的模式草稿。
    onClick: () => form.save({ token: '' }, 'providers.relayTokenCleared', ({ data }) => relayStatus(data)),
  });
  const section = connectionBlock('providers.referenceRelayTitle', 'providers.referenceRelayHelp', save,
    field(t('providers.relayMode'), mode),
    field(t('providers.relayUploadUrl'), uploadUrl, { help: t('providers.relayUrlWriteOnlyHelp') }),
    field(t('providers.relayToken'), token, { help: t('providers.relayTokenHelp') }),
    el('div', { class: 'action-row provider-config-inline-actions' }, clearToken),
    status,
  );
  function clearSecrets() { uploadUrl.value = ''; token.value = ''; }
  function sync(locked) {
    save.disabled = clearToken.disabled = mode.disabled = locked;
    uploadUrl.disabled = token.disabled = locked || mode.value !== 'external_http';
  }
  const form = bindSettingsForm(section, {
    load: loadReferenceRelay, write: saveReferenceRelay, saveError: 'providers.relaySaveError',
    status, clearSecrets, sync,
    apply: ({ data }) => {
      const summary = relayStatus(data);
      mode.value = data.mode;
      return summary;
    },
  });
  mode.addEventListener('change', () => { clearSecrets(); sync(form.isLocked()); });
  return section;
}

export function renderConnectivitySettingsPanel({ onTransportSaved = () => {} } = {}) {
  return panel({
    title: t('providers.connectivityTitle'),
    subtitle: t('providers.connectivitySubtitle'),
    className: 'provider-connectivity-panel',
  },
    el('div', { class: 'provider-connectivity-grid' }, transportBlock(onTransportSaved), relayBlock()),
  );
}

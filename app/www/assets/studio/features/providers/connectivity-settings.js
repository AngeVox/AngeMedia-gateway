import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, select } from '../../components/forms.js';
import { panel } from '../../components/page.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import {
  loadGlobalProviderTransport,
  loadReferenceRelay,
  saveGlobalProviderTransport,
  saveReferenceRelay,
} from './provider-api.js';

function configuredText(value) {
  return value ? t('providers.savedValueConfigured') : t('providers.savedValueEmpty');
}

function transportBlock() {
  const mode = select([
    { value: 'direct', label: t('providers.transportDirect') },
    { value: 'explicit_proxy', label: t('providers.transportProxy') },
  ], { value: 'direct' });
  const proxy = input({
    type: 'password',
    autocomplete: 'new-password',
    placeholder: t('providers.proxyPlaceholder'),
  });
  const state = el('p', { class: 'field-help' }, t('providers.loadingConnectionSettings'));
  let loaded = null;

  function sync() {
    proxy.disabled = mode.value !== 'explicit_proxy';
  }
  mode.addEventListener('change', sync);
  sync();

  const save = button(t('providers.transportSave'), {
    size: 'sm', variant: 'primary',
    onClick: async () => {
      const payload = { transport_mode: mode.value };
      const proxyValue = proxy.value.trim();
      if (proxyValue) payload.proxy_url = proxyValue;
      save.disabled = true;
      try {
        const result = await saveGlobalProviderTransport(payload);
        loaded = result?.data || {};
        proxy.value = '';
        state.textContent = `:  · : `;
        toast(t('providers.transportSaved'), 'success');
      } catch (error) {
        toast(safeErrorMessage(error, t('providers.transportSaveError')), 'error');
      } finally {
        save.disabled = false;
      }
    },
  });

  const section = el('section', { class: 'provider-connectivity-block' },
    el('div', { class: 'provider-config-section-heading' },
      el('div', {}, el('h3', {}, t('providers.globalTransportTitle')), el('p', { class: 'field-help' }, t('providers.globalTransportHelp'))),
      save,
    ),
    field(t('providers.transportMode'), mode),
    field(t('providers.proxyUrl'), proxy, { help: t('providers.proxyWriteOnlyHelp') }),
    state,
  );

  loadGlobalProviderTransport().then((result) => {
    loaded = result?.data || {};
    mode.value = loaded.transport_mode || loaded.effective_mode || 'direct';
    if (!['direct', 'explicit_proxy'].includes(mode.value)) mode.value = 'direct';
    sync();
    state.textContent = `:  · : `;
  }).catch((error) => {
    state.textContent = safeErrorMessage(error, t('providers.connectionSettingsLoadError'));
  });
  return section;
}

function relayBlock() {
  const mode = select([
    { value: 'disabled', label: t('providers.relayDisabled') },
    { value: 'external_http', label: t('providers.relayExternalHttp') },
  ], { value: 'disabled' });
  const uploadUrl = input({ type: 'password', autocomplete: 'new-password', placeholder: t('providers.relayUrlPlaceholder') });
  const token = input({ type: 'password', autocomplete: 'new-password', placeholder: t('providers.relayTokenPlaceholder') });
  const state = el('p', { class: 'field-help' }, t('providers.loadingConnectionSettings'));

  function sync() {
    const enabled = mode.value === 'external_http';
    uploadUrl.disabled = !enabled;
    token.disabled = !enabled;
  }
  mode.addEventListener('change', sync);
  sync();

  const save = button(t('providers.relaySave'), {
    size: 'sm', variant: 'primary',
    onClick: async () => {
      const payload = { mode: mode.value };
      const urlValue = uploadUrl.value.trim();
      const tokenValue = token.value.trim();
      if (urlValue) payload.upload_url = urlValue;
      if (tokenValue) payload.token = tokenValue;
      save.disabled = true;
      try {
        const result = await saveReferenceRelay(payload);
        const data = result?.data || {};
        uploadUrl.value = '';
        token.value = '';
        state.textContent = `:  · : `;
        toast(t('providers.relaySaved'), 'success');
      } catch (error) {
        toast(safeErrorMessage(error, t('providers.relaySaveError')), 'error');
      } finally {
        save.disabled = false;
      }
    },
  });
  const clearToken = button(t('providers.relayClearToken'), {
    size: 'sm', variant: 'secondary',
    onClick: async () => {
      clearToken.disabled = true;
      try {
        const result = await saveReferenceRelay({ token: '' });
        const data = result?.data || {};
        token.value = '';
        state.textContent = `:  · : `;
        toast(t('providers.relayTokenCleared'), 'success');
      } catch (error) {
        toast(safeErrorMessage(error, t('providers.relaySaveError')), 'error');
      } finally {
        clearToken.disabled = false;
      }
    },
  });

  const section = el('section', { class: 'provider-connectivity-block' },
    el('div', { class: 'provider-config-section-heading' },
      el('div', {}, el('h3', {}, t('providers.referenceRelayTitle')), el('p', { class: 'field-help' }, t('providers.referenceRelayHelp'))),
      save,
    ),
    field(t('providers.relayMode'), mode),
    field(t('providers.relayUploadUrl'), uploadUrl, { help: t('providers.relayUrlWriteOnlyHelp') }),
    field(t('providers.relayToken'), token, { help: t('providers.relayTokenHelp') }),
    el('div', { class: 'action-row provider-config-inline-actions' }, clearToken),
    state,
  );

  loadReferenceRelay().then((result) => {
    const data = result?.data || {};
    mode.value = data.mode || 'disabled';
    sync();
    state.textContent = `:  · : `;
  }).catch((error) => {
    state.textContent = safeErrorMessage(error, t('providers.connectionSettingsLoadError'));
  });
  return section;
}

export function renderConnectivitySettingsPanel() {
  return panel({
    title: t('providers.connectivityTitle'),
    subtitle: t('providers.connectivitySubtitle'),
    className: 'provider-connectivity-panel',
  },
    el('div', { class: 'provider-connectivity-grid' }, transportBlock(), relayBlock()),
  );
}

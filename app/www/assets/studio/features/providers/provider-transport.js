import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, select } from '../../components/forms.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { loadProviderTransport, saveProviderTransport } from './provider-api.js';

function modeLabel(mode) {
  if (mode === 'explicit_proxy') return t('providers.transportProxy');
  return t('providers.transportDirect');
}

export function providerTransportSection(providerId) {
  const mode = select([
    { value: 'inherit', label: t('providers.transportInherit') },
    { value: 'direct', label: t('providers.transportDirect') },
    { value: 'explicit_proxy', label: t('providers.transportProxy') },
  ], { value: 'inherit' });
  const proxy = input({ type: 'password', autocomplete: 'new-password', placeholder: t('providers.proxyPlaceholder') });
  const state = el('p', { class: 'field-help' }, t('providers.loadingConnectionSettings'));

  function sync() {
    proxy.disabled = mode.value !== 'explicit_proxy';
  }
  mode.addEventListener('change', sync);
  sync();

  const save = button(t('providers.transportSave'), {
    size: 'sm', variant: 'secondary',
    onClick: async () => {
      const payload = { transport_mode: mode.value === 'inherit' ? null : mode.value };
      const proxyValue = proxy.value.trim();
      if (proxyValue) payload.proxy_url = proxyValue;
      save.disabled = true;
      try {
        const result = await saveProviderTransport(providerId, payload);
        const data = result?.data || {};
        proxy.value = '';
        state.textContent = `:  · :  · : ${data.proxy_configured ? t('providers.savedValueConfigured') : t('providers.savedValueEmpty')}`;
        toast(t('providers.transportSaved'), 'success');
      } catch (error) {
        toast(safeErrorMessage(error, t('providers.transportSaveError')), 'error');
      } finally {
        save.disabled = false;
      }
    },
  });

  const section = el('section', { class: 'provider-config-section provider-transport-section' },
    el('div', { class: 'provider-config-section-heading' },
      el('div', {}, el('h3', {}, t('providers.providerTransportTitle')), el('p', { class: 'field-help' }, t('providers.providerTransportHelp'))),
      save,
    ),
    field(t('providers.transportMode'), mode),
    field(t('providers.proxyUrl'), proxy, { help: t('providers.proxyWriteOnlyHelp') }),
    state,
  );

  loadProviderTransport(providerId).then((result) => {
    const data = result?.data || {};
    mode.value = data.transport_mode || 'inherit';
    sync();
    state.textContent = `:  · :  · : ${data.proxy_configured ? t('providers.savedValueConfigured') : t('providers.savedValueEmpty')}`;
  }).catch((error) => {
    state.textContent = safeErrorMessage(error, t('providers.connectionSettingsLoadError'));
  });
  return section;
}

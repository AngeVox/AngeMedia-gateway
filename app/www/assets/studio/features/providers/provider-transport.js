import { captureSessionScope } from '../../auth.js';
import { bindViewResources, createPendingScope, createRenderScope } from '../../router.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, select } from '../../components/forms.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { loadProviderTransport, saveProviderTransport } from './provider-api.js';
import { createProviderActionGate } from './provider-action-gate.js';

const transportActions = createProviderActionGate();

function modeLabel(mode) {
  if (mode === 'explicit_proxy') return t('providers.transportProxy');
  return t('providers.transportDirect');
}

export function transportStateText(data) {
  const sourceKey = 'providers.transportSource.' + data.effective_source;
  const source = t(sourceKey);
  return t('providers.transportSummary')
    .replace('{mode}', modeLabel(data.effective_mode))
    .replace('{source}', source === sourceKey ? t('providers.transportSource.unknown') : source)
    .replace('{proxy}', t(data.proxy_configured ? 'providers.savedValueConfigured' : 'providers.savedValueEmpty'));
}

export function providerTransportSection(providerId, {
  onSaved = () => {}, isActive: owner = () => true, actionGate = transportActions,
} = {}) {
  const view = createRenderScope(), session = captureSessionScope();
  let loaded = false, busy = false;
  const active = () => resources.isActive();
  const mode = select([
    { value: 'inherit', label: t('providers.transportInherit') },
    { value: 'direct', label: t('providers.transportDirect') },
    { value: 'explicit_proxy', label: t('providers.transportProxy') },
  ], { value: 'inherit' });
  const proxy = input({ type: 'password', autocomplete: 'new-password', placeholder: t('providers.proxyPlaceholder') });
  const state = el('p', { class: 'field-help' }, t('providers.loadingConnectionSettings'));

  function sync() {
    mode.disabled = busy || !loaded;
    proxy.disabled = busy || !loaded || mode.value !== 'explicit_proxy';
    save.disabled = busy || !loaded;
    retry.disabled = busy;
  }
  mode.addEventListener('change', sync);

  const save = button(t('providers.transportSave'), {
    size: 'sm', variant: 'secondary',
    onClick: async () => {
      if (!active() || !loaded || busy) return;
      const release = actionGate.acquire(providerId, 'transport');
      if (!release) { toast(t('common.actionPending')); return; }
      const pending = createPendingScope(active);
      const payload = { transport_mode: mode.value === 'inherit' ? null : mode.value };
      const proxyValue = proxy.value.trim();
      if (proxyValue) payload.proxy_url = proxyValue;
      busy = true; proxy.value = ''; sync();
      try {
        const result = await saveProviderTransport(providerId, payload);
        if (!pending.isActive()) return;
        const data = result?.data || {};
        proxy.value = '';
        state.textContent = transportStateText(data);
        toast(t('providers.transportSaved'), 'success');
        await onSaved();
      } catch (error) {
        if (pending.isActive()) toast(safeErrorMessage(error, t('providers.transportSaveError')), 'error');
      } finally {
        pending.close(); busy = false;
        release(false);
        if (active()) sync();
      }
    },
  });

  const retry = button(t('common.refresh'), { size: 'sm', variant: 'secondary', onClick: load });
  retry.hidden = true;
  sync();
  const section = el('section', { class: 'provider-config-section provider-transport-section' },
    el('div', { class: 'provider-config-section-heading' },
      el('div', {}, el('h3', {}, t('providers.providerTransportTitle')), el('p', { class: 'field-help' }, t('providers.providerTransportHelp'))),
      save,
    ),
    field(t('providers.transportMode'), mode),
    field(t('providers.proxyUrl'), proxy, { help: t('providers.proxyWriteOnlyHelp') }),
    state, retry,
  );

  const resources = bindViewResources(() => section.isConnected && view.isActive() && session() && owner(), {
    dispose: () => { proxy.value = ''; }, suspend: () => { proxy.value = ''; }, resume() {},
  });

  async function load() {
    if (!active() || busy) return;
    if (actionGate.action(providerId)) {
      loaded = false; state.textContent = t('common.actionPending'); retry.hidden = false; sync(); return;
    }
    const pending = createPendingScope(active);
    busy = true; loaded = false; retry.hidden = true; sync();
    state.textContent = t('providers.loadingConnectionSettings');
    try {
      const result = await loadProviderTransport(providerId);
      if (!pending.isActive()) return;
      const data = result?.data || {};
      mode.value = data.transport_mode || 'inherit';
      state.textContent = transportStateText(data); loaded = true;
    } catch (error) {
      if (pending.isActive()) {
        state.textContent = safeErrorMessage(error, t('providers.connectionSettingsLoadError'));
        retry.hidden = false;
      }
    } finally {
      pending.close(); busy = false;
      if (active()) sync();
    }
  }
  queueMicrotask(load);
  return section;
}

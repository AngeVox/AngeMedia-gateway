import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { renderModelPolicy } from '../../components/model-policy.js';
import { renderProviderModels, catalogMediaLabel, catalogTypeLabel } from './catalog-sections.js?v=provider-drawer-sections-1';
import { badge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, toggle } from '../../components/forms.js';
import { toast } from '../../components/toast.js';
import { formatDate } from '../../lib/format.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';
import { hasProviderSecretField, providerCreateErrorMessage, validateProviderBaseUrl } from './provider-validation.js';
import { providerTransportSection } from './provider-transport.js?v=provider-connectivity-1';
import { disposeConnectionEvidence, renderConnectionEvidence } from './connection-evidence.js';

export function builtinConfigDrawerContent(provider, closeDrawer, state, catalog, isActive, headerStatus) {
  const providerId = safeText(provider.provider_id || '-', 64);
  const baseUrlInput = input({
    type: 'url',
    value: provider.base_url_override || '',
    placeholder: t('providers.builtinBaseUrlPlaceholder'),
    autocomplete: 'url',
  });
  const keyInput = input({
    type: 'password',
    value: '',
    placeholder: t('providers.builtinKeyPlaceholder'),
    autocomplete: 'new-password',
  });
  const enabledToggle = toggle(t('providers.enabled'), { checked: provider.enabled === true });
  const enabledInput = enabledToggle.querySelector('input');
  const connectionStatus = el('div', { class: 'provider-connection-status', role: 'status' });
  const error = el('p', { class: 'form-error', hidden: true });
  const keyBadge = el('span');
  const keyHelp = el('span', { class: 'field-help' });
  const keyField = field(t('providers.secret'), keyInput);
  keyField.append(keyHelp);

  const save = button(t('providers.builtinSave'), {
    variant: 'primary',
    onClick: () => {
      if (!isActive() || state.action(providerId)) return;
      error.hidden = true;
      const baseUrl = baseUrlInput.value.trim();
      const baseUrlError = baseUrl ? validateProviderBaseUrl(baseUrl) : '';
      if (baseUrlError) {
        error.textContent = baseUrlError;
        error.hidden = false;
        return;
      }
      const payload = {
        enabled: enabledInput.checked,
        base_url_override: baseUrl,
      };
      const apiKey = keyInput.value.trim();
      if (apiKey) payload.api_key = apiKey;
      return state.perform(providerId, isActive, 'save', async current => {
        keyInput.value = '';
        try {
          const result = await api.post(`/admin/provider-configs/${encodeURIComponent(providerId)}`, payload);
          if (!current()) return;
          if (hasProviderSecretField(result) || !state.accept(providerId, result?.data)) {
            error.textContent = t('providers.securityError');
            error.hidden = false;
            return;
          }
          keyInput.value = '';
          toast(t('providers.builtinSaveSuccess'), 'success');
          await state.refresh(providerId, current);
        } catch (requestError) {
          if (current()) { error.textContent = providerCreateErrorMessage(requestError); error.hidden = false; }
        }
      });
    },
  });

  const clearKey = button(t('providers.builtinClearKey'), {
    size: 'sm',
    variant: 'secondary',
    onClick: () => state.perform(providerId, isActive, 'clear', async current => {
      keyInput.value = '';
      try {
        const result = await api.post(`/admin/provider-configs/${encodeURIComponent(providerId)}/clear-key`, {});
        if (!current()) return;
        if (hasProviderSecretField(result) || !state.accept(providerId, result?.data)) {
          toast(t('providers.securityError'), 'error');
          return;
        }
        keyInput.value = '';
        toast(t('providers.builtinClearSuccess'), 'success');
        await state.refresh(providerId, current);
      } catch (requestError) {
        if (current()) toast(safeErrorMessage(requestError, t('providers.builtinClearError')), 'error');
      }
    }),
  });

  const testConnection = button(t('providers.builtinTestConnection'), {
    size: 'sm', variant: 'secondary', onClick: () => state.test(providerId, isActive),
  });
  const release = state.subscribe(providerId, (snapshot, action) => {
    [save, clearKey, testConnection, baseUrlInput, keyInput, enabledInput].forEach(node => { node.disabled = !!action; });
    testConnection.textContent = t(action === 'test' ? 'providers.builtinTesting' : 'providers.builtinTestConnection');
    renderConnectionEvidence(connectionStatus, action === 'test' ? { status: 'testing' } : snapshot.connection_evidence);
    keyBadge.replaceChildren(badge(t(snapshot.api_key_configured ? 'providers.configured' : 'providers.notConfigured'), snapshot.api_key_configured ? 'success' : 'warning'));
    keyHelp.textContent = snapshot.api_key_preview ? `${t('providers.builtinKeyStatus')}: ${safeText(snapshot.api_key_preview, 24)}` : t('providers.builtinKeyHelp');
    headerStatus.replaceChildren(badge(t(snapshot.enabled ? 'providers.enabled' : 'providers.disabled'), snapshot.enabled ? 'success' : 'muted'));
  }, { isCurrent: isActive, dispose: () => disposeConnectionEvidence(connectionStatus) });
  const media = catalogMediaLabel(provider.media_types);

  return {
    initialFocus: enabledInput, dispose: release,
    content: el('div', {},
      el('section', { class: 'provider-config-section' },
        el('h3', {}, t('providers.basicConfig')),
        el('div', { class: 'builtin-config-meta' },
          el('span', {}, `${t('providers.type')}: ${catalogTypeLabel(provider.provider_type)}`),
          el('span', {}, `${t('providers.builtinMedia')}: ${media}`),
          el('span', { title: provider.default_model || '' }, `${t('providers.requestDefaultModel')}: ${safeText(provider.default_model || '-', 80)}`),
          el('span', {}, `${t('providers.updated')}: ${formatDate(provider.updated_at)}`),
        ),
        enabledToggle,
        provider.default_model_policy?.sources?.length ? renderModelPolicy(provider.default_model_policy) : null,
      ),
      renderProviderModels(catalog, providerId),
      el('section', { class: 'provider-config-section' },
        el('div', { class: 'provider-config-section-heading' },
          el('h3', {}, t('providers.secret')),
          keyBadge,
        ),
        keyField,
        el('div', { class: 'action-row provider-config-inline-actions' }, clearKey),
      ),
      providerTransportSection(providerId, { isActive, onSaved: () => state.refresh(providerId, isActive) }),
      el('section', { class: 'provider-config-section' },
        el('h3', {}, t('providers.builtinBaseUrl')),
        field(t('providers.builtinBaseUrl'), baseUrlInput, { help: t('providers.builtinBaseUrlHelp') }),
        el('div', { class: 'provider-config-test-row' }, connectionStatus, testConnection),
      ),
      error,
    ),
    footer: [
      button(t('providers.cancelConfig'), { variant: 'secondary', onClick: closeDrawer }),
      save,
    ],
  };
}

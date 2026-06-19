import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, toggle } from '../../components/forms.js';
import { panel } from '../../components/page.js';
import { emptyState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { formatDate } from '../../lib/format.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';
import { hasProviderSecretField, providerCreateErrorMessage, validateProviderBaseUrl } from './provider-validation.js';


function keyStatus(provider) {
  return badge(
    provider.api_key_configured ? t('providers.configured') : t('providers.notConfigured'),
    provider.api_key_configured ? 'success' : 'warning',
  );
}

function builtinConfigCard(provider, reload) {
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
  const error = el('p', { class: 'form-error', hidden: true });

  const save = button(t('providers.builtinSave'), {
    variant: 'primary',
    size: 'sm',
    onClick: async () => {
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
      save.disabled = true;
      try {
        const result = await api.post(`/admin/provider-configs/${encodeURIComponent(providerId)}`, payload);
        if (hasProviderSecretField(result)) {
          error.textContent = t('providers.securityError');
          error.hidden = false;
          return;
        }
        keyInput.value = '';
        toast(t('providers.builtinSaveSuccess'), 'success');
        await reload();
      } catch (requestError) {
        error.textContent = providerCreateErrorMessage(requestError);
        error.hidden = false;
      } finally {
        save.disabled = false;
      }
    },
  });

  const clearKey = button(t('providers.builtinClearKey'), {
    size: 'sm',
    variant: 'secondary',
    onClick: async () => {
      clearKey.disabled = true;
      try {
        const result = await api.post(`/admin/provider-configs/${encodeURIComponent(providerId)}/clear-key`, {});
        if (hasProviderSecretField(result)) {
          toast(t('providers.securityError'), 'error');
          return;
        }
        keyInput.value = '';
        toast(t('providers.builtinClearSuccess'), 'success');
        await reload();
      } catch (requestError) {
        toast(safeErrorMessage(requestError, t('providers.builtinClearError')), 'error');
      } finally {
        clearKey.disabled = false;
      }
    },
  });

  return el('article', { class: 'provider-card builtin-config-card' },
    el('div', { class: 'provider-card-header' },
      el('div', { class: 'truncate' },
        el('p', { class: 'card-title truncate', title: provider.display_name || providerId }, safeText(provider.display_name || providerId, 96)),
        el('p', { class: 'card-subtitle truncate', title: providerId }, providerId),
      ),
      el('div', { class: 'action-row provider-badges' },
        badge(provider.enabled ? t('providers.enabled') : t('providers.disabled'), provider.enabled ? 'success' : 'muted'),
        keyStatus(provider),
      ),
    ),
    el('div', { class: 'builtin-config-meta' },
      el('span', {}, `${t('providers.type')}: ${safeText(provider.provider_type || '-', 60)}`),
      el('span', {}, `${t('providers.builtinMedia')}: ${safeText((provider.media_types || []).join(', ') || '-', 60)}`),
      el('span', { title: provider.default_model || '' }, `${t('providers.defaultModel')}: ${safeText(provider.default_model || '-', 80)}`),
      el('span', {}, `${t('providers.updated')}: ${formatDate(provider.updated_at)}`),
    ),
    el('div', { class: 'builtin-config-form' },
      field(t('providers.secret'), keyInput, {
        help: provider.api_key_preview ? `${t('providers.builtinKeyStatus')}: ${safeText(provider.api_key_preview, 24)}` : t('providers.builtinKeyHelp'),
      }),
      field(t('providers.builtinBaseUrl'), baseUrlInput, { help: t('providers.builtinBaseUrlHelp') }),
    ),
    el('div', { class: 'builtin-config-footer' },
      enabledToggle,
      el('div', { class: 'action-row builtin-config-actions' }, save, clearKey),
    ),
    error,
  );
}


export function renderBuiltinConfigPanel(providers, reload) {
  return panel({
    title: t('providers.builtinProviders'),
    subtitle: t('providers.builtinConfigHelp'),
    className: 'builtin-config-panel',
  },
  el('div', { class: 'providers-content' },
    providers.length ? el('div', { class: 'builtin-config-list' }, providers.map((provider) => builtinConfigCard(provider, reload))) :
      emptyState(t('providers.emptyReadOnly')),
  ));
}

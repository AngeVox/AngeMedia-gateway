import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { el } from '../../components/dom.js';
import { confirmModal } from '../../components/modal.js';
import { toast } from '../../components/toast.js';
import { formatDate, shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';
import { openEditProvider } from './provider-form.js';
import { providerTestSummary, testProvider } from './provider-test-state.js';
import { hasProviderSecretField } from './provider-validation.js';

function providerStatus(provider) {
  if (provider.enabled) return badge(t('providers.enabled'), 'success');
  return badge(t('providers.disabled'), 'muted');
}

function keyStatus(provider) {
  if (provider.api_key_configured) return badge(t('providers.configured'), 'success');
  return badge(t('providers.notConfigured'), 'warning');
}

function confirmRemoveProvider(provider, reload) {
  confirmModal({
    title: t('providers.removeTitle'),
    message: `${t('providers.removeMessage')} ${safeText(provider.name || provider.id || '-', 80)}`,
    confirmLabel: t('common.delete'),
    cancelLabel: t('common.cancel'),
    danger: true,
    onConfirm: async () => {
      try {
        await api.delete(`/admin/providers/${encodeURIComponent(provider.id)}`);
        toast(t('providers.removeSuccess'), 'success');
        await reload();
      } catch (_) {
        toast(t('providers.removeError'), 'error');
      }
    },
  });
}

export function providerCard(provider, reload) {
  const nextEnabled = !provider.enabled;
  const toggleButton = button(nextEnabled ? t('providers.enableAction') : t('providers.disableAction'), {
    size: 'sm',
    variant: nextEnabled ? 'primary' : 'secondary',
    onClick: async () => {
      try {
        const result = await api.post(`/admin/providers/${encodeURIComponent(provider.id)}/enabled`, { enabled: nextEnabled });
        if (hasProviderSecretField(result)) {
          toast(t('providers.securityError'), 'error');
          return;
        }
        await reload();
      } catch (_) {
        toast(t('providers.updateError'), 'error');
      }
    },
  });

  return el('article', { class: 'provider-card provider-compact-card' },
    el('div', { class: 'provider-compact-main' },
      el('div', { class: 'provider-card-header' },
        el('div', { class: 'truncate' },
          el('p', { class: 'card-title truncate', title: provider.name || provider.id || '-' }, safeText(provider.name || provider.id || '-', 96)),
          el('p', { class: 'card-subtitle truncate', title: provider.id || '' }, `${shortId(provider.id)} · ${safeText(provider.provider_type || '-', 60)}`),
        ),
        el('div', { class: 'action-row provider-badges' }, providerStatus(provider), keyStatus(provider)),
      ),
      el('div', { class: 'provider-compact-meta' },
        el('span', { title: provider.default_model || '' }, `${t('providers.defaultModel')}: ${safeText(provider.default_model || '-', 80)}`),
        el('span', {}, `${t('providers.created')}: ${formatDate(provider.created_at)}`),
        el('span', {}, `${t('providers.updated')}: ${formatDate(provider.updated_at)}`),
      ),
    ),
    el('div', { class: 'action-row provider-compact-actions' },
      toggleButton,
      button(t('providers.editAction') || 'Edit', {
        size: 'sm',
        variant: 'secondary',
        onClick: () => openEditProvider(provider, reload),
      }),
      button(t('providers.testAction') || 'Test', {
        size: 'sm',
        variant: 'secondary',
        onClick: () => testProvider(provider, reload),
      }),
      button(t('common.delete'), {
        size: 'sm',
        variant: 'danger',
        onClick: () => confirmRemoveProvider(provider, reload),
      }),
      providerTestSummary(provider),
    ),
  );
}

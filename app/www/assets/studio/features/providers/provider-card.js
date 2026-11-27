import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { createPendingScope } from '../../router.js';
import { providerAvailability } from '../../lib/capabilities.js';
import { button } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { el } from '../../components/dom.js';
import { confirmModal } from '../../components/modal.js?v=web-studio-2j';
import { toast } from '../../components/toast.js';
import { formatDate, shortId } from '../../lib/format.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';
import { customProviderActions } from './provider-action-gate.js';
import { openEditProvider } from './provider-form.js?v=provider-drawer-sections-1';
import { providerTestSummary } from './provider-test-state.js';
import { hasProviderSecretField } from './provider-validation.js';

function providerStatus(provider) {
  const state = providerAvailability(provider);
  return badge(t(state.reasonKey), state.selectable ? 'info' : 'muted');
}

function keyStatus(provider) {
  if (provider.api_key_configured) return badge(t('providers.configured'), 'success');
  if (provider.configuration_complete) return badge(t('availability.noKey'), 'info');
  return badge(t('providers.notConfigured'), 'warning');
}

export function providerCard(initial, { isActive, isBlocked }) {
  let provider = initial, testResult, summary, confirmation;
  const id = provider.id;
  const path = `/admin/providers/${encodeURIComponent(id)}`;
  const row = el('article', { class: 'provider-card provider-compact-card' });
  const identity = el('div', { class: 'truncate' });
  const badges = el('div', { class: 'action-row provider-badges' });
  const metadata = el('div', { class: 'provider-compact-meta' });
  const actions = el('div', { class: 'action-row provider-compact-actions' });
  const current = () => row.isConnected && isActive();
  const ready = () => current() && !isBlocked(id) && !customProviderActions.action(id);

  async function perform(kind, request, onResult, onError, owner = current) {
    if (!ready() || !owner()) return;
    const release = customProviderActions.acquire(id, kind);
    if (!release) return;
    const pending = createPendingScope(() => current() && owner());
    try {
      const result = await request();
      if (!pending.isActive()) return;
      if (hasProviderSecretField(result)) {
        toast(t('providers.securityError'), 'error');
        return;
      }
      return onResult(result);
    } catch (error) {
      if (pending.isActive()) return onError(error);
    } finally {
      pending.close();
      release();
    }
  }

  const toggle = button('', {
    size: 'sm',
    onClick: () => perform('toggle', () => api.post(path + '/enabled', { enabled: !provider.enabled }),
      () => {}, () => toast(t('providers.updateError'), 'error')),
  });
  const edit = button(t('providers.editAction'), {
    size: 'sm',
    onClick: event => { if (ready()) return openEditProvider(provider, event.currentTarget); },
  });
  const probe = button(t('providers.testAction'), {
    size: 'sm',
    onClick: () => perform('test', () => api.post(path + '/test'), result => {
      testResult = {
        status: result.status, ok: result.ok, message: result.message,
        model_found: result.model_found, elapsed_ms: result.elapsed_ms,
        stamp: result.data?.updated_at ?? provider.updated_at,
      };
      toast(t(result.ok ? 'providers.testSuccess' : 'providers.testFailed'), result.ok ? 'success' : 'warning');
    }, error => {
      if (error?.detail?.status === 'test_not_supported') {
        testResult = { status: 'test_not_supported', message: t('providers.testUnsupported'), stamp: provider.updated_at };
      } else toast(safeErrorMessage(error, t('providers.testFailed')), 'error');
    }),
  });
  const remove = button(t('common.delete'), {
    size: 'sm', variant: 'danger',
    onClick: () => {
      if (!ready() || confirmation?.isConnected) return;
      confirmation = confirmModal({
        title: t('providers.removeTitle'),
        message: `${t('providers.removeMessage')} ${safeText(provider.name || id, 80)}`,
        confirmLabel: t('common.delete'),
        cancelLabel: t('common.cancel'),
        danger: true,
        errorMessage: t('providers.removeError'),
        onConfirm: ({ isActive }) => perform('delete', () => api.delete(path),
          () => toast(t('providers.removeSuccess'), 'success'), error => { throw error; }, isActive),
      });
    },
  });
  actions.append(toggle, edit, probe, remove);
  row.append(el('div', { class: 'provider-compact-main' },
    el('div', { class: 'provider-card-header' }, identity, badges), metadata), actions);

  function sync() {
    const kind = customProviderActions.action(id);
    const disabled = isBlocked(id) || !!kind;
    toggle.disabled = edit.disabled = probe.disabled = remove.disabled = disabled;
    probe.textContent = t(kind === 'test' ? 'providers.builtinTesting' : 'providers.testAction');
    actions.setAttribute('aria-busy', String(disabled));
  }

  function update(snapshot) {
    provider = snapshot;
    const enabled = provider.enabled;
    toggle.textContent = t(enabled ? 'providers.disableAction' : 'providers.enableAction');
    toggle.classList.toggle('btn-primary', !enabled);
    toggle.classList.toggle('btn-secondary', enabled);
    identity.replaceChildren(
      el('p', { class: 'card-title truncate', title: provider.name || id }, safeText(provider.name || id, 96)),
      el('p', { class: 'card-subtitle truncate', title: id }, `${shortId(id)} · ${safeText(provider.provider_type || '-', 60)}`),
    );
    badges.replaceChildren(providerStatus(provider), keyStatus(provider));
    metadata.replaceChildren(
      el('span', { title: provider.default_model || '' }, `${t('providers.defaultModel')}: ${safeText(provider.default_model || '-', 80)}`),
      el('span', {}, `${t('providers.created')}: ${formatDate(provider.created_at)}`),
      el('span', {}, `${t('providers.updated')}: ${formatDate(provider.updated_at)}`),
    );
    // 当前测试详情只随对应快照显示；翻页后从列表读取上次测试摘要。
    if (testResult?.stamp !== provider.updated_at) testResult = null;
    summary?.remove();
    summary = providerTestSummary(provider, testResult);
    if (summary) row.append(summary);
    sync();
  }
  return { row, sync, update };
}

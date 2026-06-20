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


const CONNECTION_STATUS_META = Object.freeze({
  success: { label: 'providers.connectionSuccess', tone: 'success' },
  failed: { label: 'providers.connectionFailed', tone: 'danger' },
  unsupported: { label: 'providers.connectionUnsupported', tone: 'muted' },
  not_configured: { label: 'providers.notConfigured', tone: 'warning' },
  disabled: { label: 'providers.disabled', tone: 'muted' },
});

const connectionResults = new Map();


function normalizedConnectionResult(result) {
  const status = Object.prototype.hasOwnProperty.call(CONNECTION_STATUS_META, result?.status) ? result.status : 'failed';
  return {
    status,
    message: safeText(result?.message || '', 180),
  };
}


function renderConnectionResult(container, result, { compact = false } = {}) {
  const normalized = normalizedConnectionResult(result);
  const meta = CONNECTION_STATUS_META[normalized.status];
  const children = [badge(t(meta.label), meta.tone)];
  if (normalized.message && !compact) children.push(el('span', {}, normalized.message));
  container.dataset.status = normalized.status;
  container.title = normalized.message;
  container.replaceChildren(...children);
  container.hidden = false;
}


function keyStatus(provider) {
  return badge(
    provider.api_key_configured ? t('providers.configured') : t('providers.notConfigured'),
    provider.api_key_configured ? 'success' : 'warning',
  );
}


function statusCell(label, className, child) {
  return el('div', { class: `builtin-provider-cell ${className}` },
    el('span', { class: 'builtin-provider-cell-label' }, label),
    child,
  );
}


function createConnectionStatus(providerId, registerStatus, { compact = false } = {}) {
  const node = el('div', {
    class: compact ? 'provider-connection-status provider-connection-status-compact' : 'provider-connection-status',
    role: 'status',
  });
  registerStatus(providerId, node, compact);
  const previous = connectionResults.get(providerId);
  if (previous) {
    renderConnectionResult(node, previous, { compact });
  } else if (compact) {
    node.replaceChildren(el('span', { class: 'text-muted' }, t('providers.connectionNotTested')));
  } else {
    node.hidden = true;
  }
  return node;
}


function createConnectionTestButton(providerId, updateStatus, { className = '' } = {}) {
  const testConnection = button(t('providers.builtinTestConnection'), {
    size: 'sm',
    variant: 'secondary',
    onClick: async () => {
      testConnection.disabled = true;
      testConnection.textContent = t('providers.builtinTesting');
      try {
        const response = await api.post(`/admin/provider-configs/${encodeURIComponent(providerId)}/test`, {});
        const result = hasProviderSecretField(response) ? {
          status: 'failed',
          message: t('providers.securityError'),
        } : response?.data || {};
        connectionResults.set(providerId, normalizedConnectionResult(result));
        updateStatus(providerId, result);
      } catch (requestError) {
        const result = {
          status: 'failed',
          message: safeErrorMessage(requestError, t('providers.builtinTestError')),
        };
        connectionResults.set(providerId, normalizedConnectionResult(result));
        updateStatus(providerId, result);
      } finally {
        testConnection.disabled = false;
        testConnection.textContent = t('providers.builtinTestConnection');
      }
    },
  });
  testConnection.classList.add('provider-test-button');
  if (className) testConnection.classList.add(className);
  return testConnection;
}


function providerIdentity(provider, providerId) {
  return el('div', { class: 'builtin-provider-identity' },
    el('p', {
      class: 'card-title truncate',
      title: provider.display_name || providerId,
    }, safeText(provider.display_name || providerId, 96)),
    el('p', { class: 'card-subtitle truncate', title: providerId }, providerId),
  );
}


function providerRow(provider, openDrawer, registerStatus, updateStatus) {
  const providerId = safeText(provider.provider_id || '-', 64);
  const row = el('article', {
    class: 'builtin-provider-row',
    dataset: { providerId },
    role: 'listitem',
  });
  const configure = button(t('providers.configureAction'), {
    size: 'sm',
    variant: 'secondary',
    onClick: () => openDrawer(provider, row, configure),
  });
  const testConnection = createConnectionTestButton(providerId, updateStatus, { className: 'builtin-provider-row-test' });
  const typeMedia = `${safeText(provider.provider_type || '-', 48)} · ${safeText((provider.media_types || []).join(', ') || '-', 32)}`;
  const baseUrlState = provider.base_url_override ? t('providers.baseUrlOverridden') : t('providers.baseUrlDefault');

  row.append(
    el('div', { class: 'builtin-provider-cell builtin-provider-main' }, providerIdentity(provider, providerId)),
    statusCell(t('providers.registryTypeMedia'), 'builtin-provider-type', el('span', { class: 'truncate', title: typeMedia }, typeMedia)),
    statusCell(t('providers.enabled'), 'builtin-provider-enabled', badge(
      provider.enabled ? t('providers.enabled') : t('providers.disabled'),
      provider.enabled ? 'success' : 'muted',
    )),
    statusCell(t('providers.keyState'), 'builtin-provider-key', keyStatus(provider)),
    statusCell(t('providers.builtinBaseUrl'), 'builtin-provider-base', el('span', {
      class: 'truncate',
      title: provider.base_url_override || t('providers.baseUrlDefault'),
    }, baseUrlState)),
    statusCell(
      t('providers.registryLastTest'),
      'builtin-provider-test-state',
      createConnectionStatus(providerId, registerStatus, { compact: true }),
    ),
    el('div', { class: 'action-row builtin-provider-row-actions' }, configure, testConnection),
  );
  return row;
}


function builtinConfigDrawer(provider, reload, closeDrawer, registerStatus, updateStatus) {
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
  const connectionStatus = createConnectionStatus(providerId, registerStatus);
  const error = el('p', { class: 'form-error', hidden: true });

  const save = button(t('providers.builtinSave'), {
    variant: 'primary',
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
        closeDrawer();
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
        closeDrawer();
        await reload();
      } catch (requestError) {
        toast(safeErrorMessage(requestError, t('providers.builtinClearError')), 'error');
      } finally {
        clearKey.disabled = false;
      }
    },
  });

  const testConnection = createConnectionTestButton(providerId, updateStatus);
  const media = safeText((provider.media_types || []).join(', ') || '-', 60);

  return el('aside', {
    class: 'provider-config-drawer',
    role: 'dialog',
    'aria-modal': 'true',
    'aria-label': `${t('providers.configDrawerTitle')}: ${provider.display_name || providerId}`,
  },
    el('header', { class: 'provider-config-drawer-header' },
      el('div', { class: 'truncate' },
        el('div', { class: 'provider-config-title-line' },
          el('h2', {}, t('providers.configDrawerTitle')),
          badge(provider.enabled ? t('providers.enabled') : t('providers.disabled'), provider.enabled ? 'success' : 'muted'),
        ),
        el('p', { class: 'card-title truncate', title: provider.display_name || providerId }, safeText(provider.display_name || providerId, 96)),
        el('p', { class: 'card-subtitle truncate', title: providerId }, `${providerId} · ${media}`),
      ),
      button(t('providers.closeConfig'), { size: 'sm', variant: 'secondary', onClick: closeDrawer }),
    ),
    el('div', { class: 'provider-config-drawer-body' },
      el('section', { class: 'provider-config-section' },
        el('h3', {}, t('providers.basicConfig')),
        el('div', { class: 'builtin-config-meta' },
          el('span', {}, `${t('providers.type')}: ${safeText(provider.provider_type || '-', 60)}`),
          el('span', {}, `${t('providers.builtinMedia')}: ${media}`),
          el('span', { title: provider.default_model || '' }, `${t('providers.defaultModel')}: ${safeText(provider.default_model || '-', 80)}`),
          el('span', {}, `${t('providers.updated')}: ${formatDate(provider.updated_at)}`),
        ),
        enabledToggle,
      ),
      el('section', { class: 'provider-config-section' },
        el('div', { class: 'provider-config-section-heading' },
          el('h3', {}, t('providers.secret')),
          keyStatus(provider),
        ),
        field(t('providers.secret'), keyInput, {
          help: provider.api_key_preview ? `${t('providers.builtinKeyStatus')}: ${safeText(provider.api_key_preview, 24)}` : t('providers.builtinKeyHelp'),
        }),
        el('div', { class: 'action-row provider-config-inline-actions' }, clearKey),
      ),
      el('section', { class: 'provider-config-section' },
        el('h3', {}, t('providers.builtinBaseUrl')),
        field(t('providers.builtinBaseUrl'), baseUrlInput, { help: t('providers.builtinBaseUrlHelp') }),
        el('div', { class: 'provider-config-test-row' }, connectionStatus, testConnection),
      ),
      error,
    ),
    el('footer', { class: 'provider-config-drawer-footer' },
      button(t('providers.cancelConfig'), { variant: 'secondary', onClick: closeDrawer }),
      save,
    ),
  );
}


export function renderBuiltinConfigPanel(providers, reload) {
  const statusTargets = new Map();
  const rowByProvider = new Map();
  let activeRow = null;
  let returnFocus = null;
  let escapeHandler = null;
  let routeHandler = null;

  const drawerLayer = el('div', { class: 'provider-config-drawer-layer', hidden: true });
  const drawerBackdrop = el('div', { class: 'provider-config-drawer-backdrop' });
  const drawerSlot = el('div', { class: 'provider-config-drawer-slot' });
  drawerLayer.append(drawerBackdrop, drawerSlot);

  function registerStatus(providerId, node, compact) {
    if (!statusTargets.has(providerId)) statusTargets.set(providerId, new Set());
    statusTargets.get(providerId).add({ node, compact });
  }

  function updateStatus(providerId, result) {
    const normalized = normalizedConnectionResult(result);
    connectionResults.set(providerId, normalized);
    (statusTargets.get(providerId) || []).forEach(({ node, compact }) => {
      renderConnectionResult(node, normalized, { compact });
    });
  }

  function closeDrawer() {
    drawerLayer.hidden = true;
    drawerSlot.replaceChildren();
    drawerLayer.remove();
    document.documentElement.classList.remove('provider-drawer-open');
    if (escapeHandler) document.removeEventListener('keydown', escapeHandler);
    if (routeHandler) window.removeEventListener('hashchange', routeHandler);
    escapeHandler = null;
    routeHandler = null;
    if (activeRow) activeRow.classList.remove('is-selected');
    activeRow = null;
    if (returnFocus?.isConnected) returnFocus.focus();
    returnFocus = null;
  }

  function openDrawer(provider, row, trigger) {
    closeDrawer();
    activeRow = row;
    returnFocus = trigger;
    activeRow.classList.add('is-selected');
    drawerSlot.replaceChildren(builtinConfigDrawer(provider, reload, closeDrawer, registerStatus, updateStatus));
    document.body.appendChild(drawerLayer);
    drawerLayer.hidden = false;
    document.documentElement.classList.add('provider-drawer-open');
    escapeHandler = (event) => {
      if (event.key === 'Escape') closeDrawer();
    };
    document.addEventListener('keydown', escapeHandler);
    routeHandler = closeDrawer;
    window.addEventListener('hashchange', routeHandler, { once: true });
    drawerSlot.querySelector('input')?.focus();
  }

  drawerBackdrop.addEventListener('click', closeDrawer);

  const rows = providers.map((provider) => {
    const row = providerRow(provider, openDrawer, registerStatus, updateStatus);
    rowByProvider.set(String(provider.provider_id || ''), row);
    return row;
  });
  const noMatches = el('div', { class: 'builtin-provider-filter-empty', hidden: true }, t('providers.noProviderMatches'));
  const visibleCount = el('span', { class: 'provider-registry-count' }, `${providers.length} ${t('providers.registryItems')}`);
  const search = input({
    type: 'search',
    class: 'provider-registry-search',
    placeholder: t('providers.searchBuiltins'),
    'aria-label': t('providers.searchBuiltins'),
    oninput: () => {
      const query = search.value.trim().toLocaleLowerCase();
      let count = 0;
      providers.forEach((provider) => {
        const providerId = String(provider.provider_id || '');
        const row = rowByProvider.get(providerId);
        const haystack = `${provider.display_name || ''} ${providerId} ${provider.provider_type || ''} ${(provider.media_types || []).join(' ')}`.toLocaleLowerCase();
        const visible = !query || haystack.includes(query);
        row.hidden = !visible;
        if (visible) count += 1;
      });
      visibleCount.textContent = `${count} ${t('providers.registryItems')}`;
      noMatches.hidden = count !== 0;
    },
  });

  const registry = el('div', { class: 'builtin-provider-registry' },
    el('div', { class: 'builtin-provider-registry-head', 'aria-hidden': 'true' },
      el('span', {}, t('providers.registryProvider')),
      el('span', {}, t('providers.registryTypeMedia')),
      el('span', {}, t('providers.enabled')),
      el('span', {}, t('providers.keyState')),
      el('span', {}, t('providers.builtinBaseUrl')),
      el('span', {}, t('providers.registryLastTest')),
      el('span', {}, t('providers.registryActions')),
    ),
    el('div', { class: 'builtin-provider-registry-body', role: 'list' }, rows, noMatches),
  );

  return panel({
    title: t('providers.builtinProviders'),
    subtitle: t('providers.builtinConfigHelp'),
    className: 'builtin-config-panel',
  },
  el('div', { class: 'providers-content' },
    providers.length ? el('div', { class: 'builtin-provider-registry-shell' },
      el('div', { class: 'builtin-provider-registry-toolbar' }, search, visibleCount),
      registry,
    ) : emptyState(t('providers.emptyReadOnly')),
  ));
}

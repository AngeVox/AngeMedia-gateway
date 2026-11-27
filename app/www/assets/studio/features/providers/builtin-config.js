import { t } from '../../i18n.js';
import { captureSessionScope } from '../../auth.js';
import { bindViewResources, createRenderScope } from '../../router.js';
import { disposeConnectionEvidence, renderConnectionEvidence } from './connection-evidence.js';
import { badge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { input } from '../../components/forms.js';
import { emptyState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { safeText } from '../../lib/security.js';
import { beginProviderDrawerRequest, openProviderDrawer } from './provider-drawer.js?v=provider-drawer-sections-1';
import { createBuiltinState } from './builtin-state.js';
import { builtinConfigDrawerContent } from './builtin-drawer.js';
import { catalogMediaLabel } from './catalog-sections.js?v=provider-drawer-sections-1';

let builtinMediaFilter = 'image';


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


function providerIdentity(provider, providerId) {
  return el('div', { class: 'builtin-provider-identity' },
    el('p', {
      class: 'card-title truncate',
      title: provider.display_name || providerId,
    }, safeText(provider.display_name || providerId, 96)),
    el('p', { class: 'card-subtitle truncate', title: providerId }, providerId),
  );
}


function providerMediaLabel(provider) {
  const mediaTypes = Array.isArray(provider.media_types) ? provider.media_types : [];
  const media = catalogMediaLabel(mediaTypes);
  const type = mediaTypes.length > 1 ? t('providers.builtinProviders')
    : t(String(provider.provider_type || '').includes('video') ? 'providers.builtinVideoChannel' : 'providers.builtinImageChannel');
  return `${type} / ${media}`;
}


function providerRow(provider, openDrawer, state, updateCounts) {
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
  const testConnection = button(t('providers.builtinTestConnection'), { size: 'sm', variant: 'secondary', onClick: () => state.test(providerId, () => row.isConnected) });
  testConnection.classList.add('provider-test-button', 'builtin-provider-row-test');
  const enabled = el('div'), secret = el('div'), connection = el('div', { class: 'provider-connection-status provider-connection-status-compact', role: 'status' });
  const baseValue = el('span', { class: 'truncate' });
  const typeMedia = providerMediaLabel(provider);

  row.append(
    el('div', { class: 'builtin-provider-cell builtin-provider-main' }, providerIdentity(provider, providerId)),
    statusCell(t('providers.registryTypeMedia'), 'builtin-provider-type', el('span', { class: 'truncate', title: typeMedia }, typeMedia)),
    statusCell(t('providers.enabled'), 'builtin-provider-enabled', enabled),
    statusCell(t('providers.keyState'), 'builtin-provider-key', secret),
    statusCell(t('providers.builtinBaseUrl'), 'builtin-provider-base', baseValue),
    statusCell(t('providers.registryLastTest'), 'builtin-provider-test-state', connection),
    el('div', { class: 'action-row builtin-provider-row-actions' }, configure, testConnection),
  );
  state.subscribe(providerId, (snapshot, action) => {
    provider = snapshot;
    enabled.replaceChildren(badge(t(provider.enabled ? 'providers.enabled' : 'providers.disabled'), provider.enabled ? 'success' : 'muted'));
    secret.replaceChildren(keyStatus(provider));
    baseValue.textContent = t(provider.base_url_override ? 'providers.baseUrlOverridden' : 'providers.baseUrlDefault');
    baseValue.title = provider.base_url_override || t('providers.baseUrlDefault');
    testConnection.disabled = configure.disabled = !!action;
    testConnection.textContent = t(action === 'test' ? 'providers.builtinTesting' : 'providers.builtinTestConnection');
    renderConnectionEvidence(connection, action === 'test' ? { status: 'testing' } : provider.connection_evidence, { compact: true });
    updateCounts();
  }, { isCurrent: () => row.isConnected, dispose: () => disposeConnectionEvidence(connection) });
  return row;
}




export function renderBuiltinConfigPanel(providers, reload, catalog = {}) {
  const session = captureSessionScope();
  let panel, scope;
  const active = () => panel?.isConnected && scope.isActive() && session();
  const state = createBuiltinState(providers, active);
  const rowByProvider = new Map();
  const countLabel = el('span', { class: 'provider-section-count' });
  function updateCounts() {
    const latest = providers.map(provider => state.get(provider.provider_id));
    countLabel.textContent = `${latest.length} ${t('providers.registryItems')} · ${latest.filter(p => p.enabled).length} ${t('providers.enabled')} · ${latest.filter(p => p.api_key_configured).length} ${t('providers.configured')}`;
  }

  async function openDrawer(provider, row, trigger) {
    const providerId = provider.provider_id;
    if (state.action(providerId)) { toast(t('common.actionPending')); return; }
    const request = beginProviderDrawerRequest(trigger);
    try {
      const loaded = await state.refresh(providerId, request.isActive);
      if (!loaded || !request.isActive()) {
        if (loaded === false && request.isActive()) toast(t('providers.connectionSettingsLoadError'), 'error');
        return;
      }
      if (state.action(providerId)) return;
      provider = state.get(providerId);
      const headerStatus = el('span');
      const media = safeText((provider.media_types || []).join(', ') || '-', 60);
      row.classList.add('is-selected');
      openProviderDrawer({
        title: t('providers.configDrawerTitle'),
        identity: safeText(provider.display_name || providerId, 96),
        identityMeta: `${providerId} · ${media}`,
        status: headerStatus,
        trigger,
        onClose: () => row.classList.remove('is-selected'),
        build: (closeDrawer, { isActive }) => builtinConfigDrawerContent(provider, closeDrawer, state, catalog, isActive, headerStatus),
      });
    } finally { request.close(); }
  }

  const rows = providers.map((provider) => {
    const row = providerRow(provider, openDrawer, state, updateCounts);
    rowByProvider.set(String(provider.provider_id || ''), row);
    return row;
  });
  const noMatches = el('div', { class: 'builtin-provider-filter-empty', hidden: true }, t('providers.noProviderMatches'));
  const visibleCount = el('span', { class: 'provider-registry-count' });
  function applyFilter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    providers.forEach((provider) => {
      const providerId = String(provider.provider_id || '');
      const mediaTypes = Array.isArray(provider.media_types) ? provider.media_types : [];
      const haystack = `${provider.display_name || ''} ${providerId} ${provider.provider_type || ''} ${mediaTypes.join(' ')}`.toLocaleLowerCase();
      const visible = mediaTypes.includes(builtinMediaFilter) && (!query || haystack.includes(query));
      rowByProvider.get(providerId).hidden = !visible;
      if (visible) count += 1;
    });
    visibleCount.textContent = `${count} ${t('providers.registryItems')}`;
    noMatches.hidden = count !== 0;
    filterButtons.forEach((item, media) => {
      item.setAttribute('aria-pressed', String(media === builtinMediaFilter));
      item.classList.toggle('btn-primary', media === builtinMediaFilter);
      item.classList.toggle('btn-secondary', media !== builtinMediaFilter);
    });
  }
  const search = input({
    type: 'search',
    class: 'provider-registry-search',
    placeholder: t('providers.searchBuiltins'),
    'aria-label': t('providers.searchBuiltins'),
    oninput: applyFilter,
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

  const filterButtons = new Map();
  for (const media of ['image', 'video']) {
    filterButtons.set(media, button(t(media === 'image' ? 'providers.imageChannels' : 'providers.videoChannels'), {
      size: 'sm',
      onClick: () => {
        builtinMediaFilter = media;
        applyFilter();
      },
    }));
  }
  const filterTabs = el('div', { class: 'builtin-provider-tabs' }, ...filterButtons.values());
  applyFilter();
  panel = el('details', { class: 'panel provider-section-collapsible builtin-config-panel', open: true },
    el('summary', { class: 'provider-section-summary' },
      el('span', { class: 'provider-section-summary-main' },
        el('strong', {}, t('providers.builtinProviders')),
        el('small', {}, t('providers.builtinConfigHelp')),
      ),
      countLabel,
    ),
    el('div', { class: 'providers-content provider-section-content' },
      el('p', { class: 'field-help' }, `${t('availability.scope')} ${t('providers.evidenceScopeHelp')}`),
      providers.length ? el('div', { class: 'builtin-provider-registry-shell' },
        el('div', { class: 'builtin-provider-registry-toolbar' }, search, filterTabs, visibleCount),
        registry,
      ) : emptyState(t('providers.emptyReadOnly')),
    ),
  );
  scope = createRenderScope(panel);
  bindViewResources(() => panel.isConnected && scope.isActive() && session(), {
    dispose: state.dispose, suspend: state.pause, resume: state.resume,
  });
  updateCounts();
  return panel;
}

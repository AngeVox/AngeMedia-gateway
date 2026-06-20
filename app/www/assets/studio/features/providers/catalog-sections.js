import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { el } from '../../components/dom.js';
import { emptyState } from '../../components/states.js';
import { shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

function catalogProviders(catalog) {
  return Array.isArray(catalog?.providers) ? catalog.providers : [];
}

export function builtinProviders(catalog) {
  return catalogProviders(catalog).filter((provider) => String(provider.ui_group || '').startsWith('builtin'));
}

export function reservedProviders(catalog) {
  return catalogProviders(catalog).filter((provider) => provider.status === 'reserved');
}

export function releaseCatalogProviders(catalog) {
  return catalogProviders(catalog).filter((provider) => (
    provider.status !== 'reserved' &&
    !String(provider.ui_group || '').startsWith('builtin')
  ));
}

function readOnly() {
  return badge(t('providers.readOnly'), 'muted');
}

function readOnlyProviderCard(provider, kind) {
  const media = Array.isArray(provider.media_types) ? provider.media_types.join(', ') : provider.media_type || '-';
  const isExperimental = provider.status === 'experimental';
  const isDisabled = provider.enabled_default === false;
  return el('article', {
    class: [
      'provider-card',
      'provider-readonly-card',
      isExperimental ? 'provider-readonly-experimental' : '',
      isDisabled ? 'provider-readonly-disabled' : '',
    ].filter(Boolean).join(' '),
  },
    el('div', { class: 'provider-card-header' },
      el('div', { class: 'truncate' },
        el('p', { class: 'card-title truncate', title: provider.display_name || provider.name || provider.id || '-' }, safeText(provider.display_name || provider.name || provider.id || '-', 96)),
        el('p', { class: 'card-subtitle truncate', title: provider.id || '' }, `${shortId(provider.id)} · ${safeText(media, 60)}`),
      ),
      el('div', { class: 'action-row provider-badges' },
        badge(safeText(provider.status || kind || '-', 24), provider.status === 'release' ? 'success' : 'warning'),
        readOnly(),
      ),
    ),
    el('div', { class: 'provider-compact-meta' },
      el('span', {}, `${t('providers.type')}: ${safeText(provider.ui_group || kind || '-', 60)}`),
      el('span', {}, `${t('providers.enabled')}: ${provider.enabled_default ? t('providers.enabled') : t('providers.disabled')}`),
    ),
  );
}

export function renderReadOnlyPanel(title, subtitle, items, kind) {
  return el('details', { class: 'panel provider-section-collapsible provider-readonly-section' },
    el('summary', { class: 'provider-section-summary provider-readonly-summary' },
      el('span', { class: 'provider-section-summary-main' },
        el('strong', {}, title),
        el('small', {}, subtitle),
      ),
      el('span', { class: 'provider-section-count' }, `${items.length} ${t('providers.registryItems')} · ${t('providers.readOnly')}`),
    ),
    el('div', { class: 'providers-content provider-section-content' },
      items.length ? el('div', { class: 'provider-list bounded-list' }, items.map((provider) => readOnlyProviderCard(provider, kind))) :
        emptyState(t('providers.emptyReadOnly')),
    ),
  );
}

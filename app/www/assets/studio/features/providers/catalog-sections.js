import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { modelStatusBadge } from '../../components/model-policy.js';
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

export function catalogMediaLabel(value) {
  return (Array.isArray(value) ? value : [value]).filter(Boolean).map((type) => {
    const key = 'jobDisplay.media.' + type;
    const translated = t(key);
    return translated === key ? safeText(type, 40) : translated;
  }).join(' / ') || '-';
}

export function catalogTypeLabel(value) {
  const key = 'providers.catalogType.' + value;
  const translated = t(key);
  return translated === key ? safeText(value || '-', 60) : translated;
}

export function renderProviderModels(catalog, providerId) {
  const loaded = Array.isArray(catalog?.models);
  const models = loaded ? catalog.models.filter((model) => model?.provider_id === providerId) : [];
  return el('details', { class: 'provider-config-section list-disclosure provider-model-catalog' },
    el('summary', {}, t('providers.modelCatalog').replace('{count}', loaded ? String(models.length) : '—')),
    el('p', { class: 'field-help' }, t('providers.modelCatalogHelp')),
    !loaded ? emptyState(t('providers.modelCatalogUnavailable')) : !models.length
      ? emptyState(t('providers.modelCatalogEmpty'))
      : el('div', { class: 'form-stack compact-stack' }, models.map((model) => {
        const selectable = model.selectable === true && model.policy?.lifecycle !== 'retired';
        const capabilities = Object.entries(model.capabilities || {}).filter(([, value]) => value === true);
        return el('article', { class: 'form-subsection' },
          el('p', { class: 'card-title' }, safeText(model.display_name || model.id, 96)),
          el('p', { class: 'card-subtitle' }, safeText(model.provider_model || model.id, 128)),
          el('div', { class: 'action-row' },
            badge(t(selectable ? 'providers.modelSelectable' : 'providers.modelUnselectable'), 'muted'),
            capabilities.map(([key]) => {
              const labelKey = key === 'image_edit' ? 'generateImage.operationEdit' : `jobDisplay.inputModes.${key}`;
              const translated = t(labelKey);
              return badge(translated === labelKey ? safeText(key.replaceAll('_', ' '), 60) : translated, 'info');
            }),
          ),
        );
      })),
  );
}

function readOnlyProviderCard(provider, kind) {
  const media = catalogMediaLabel(provider.media_types || provider.media_type);
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
        modelStatusBadge(provider.status || kind),
        readOnly(),
      ),
    ),
    el('div', { class: 'provider-compact-meta' },
      el('span', {}, `${t('providers.type')}: ${catalogTypeLabel(provider.ui_group || kind)}`),
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

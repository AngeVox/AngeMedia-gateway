import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { clampPage, pageSlice, paginationBar } from '../../components/pagination.js';
import { pageHeader } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import {
  releaseCatalogProviders,
  renderReadOnlyPanel,
  reservedProviders,
} from './catalog-sections.js?v=provider-drawer-sections-1';
import { renderBuiltinConfigPanel } from './builtin-config.js?v=provider-drawer-sections-1';
import { dataArray, loadAdminConfig, loadBuiltinProviderConfigs, loadCatalog, loadProviders } from './provider-api.js';
import { providerCard } from './provider-card.js?v=provider-drawer-sections-1';
import { openCreateProvider } from './provider-form.js?v=provider-drawer-sections-1';
import { renderRuntimeSettingsPanel } from './runtime-settings.js?v=web-studio-2h';
import { hasProviderSecretField } from './provider-validation.js';

const PROVIDER_PAGE_SIZE = 5;

let providerPage = 1;
let customSectionOpen = false;

function pagerLabels() {
  return {
    prev: t('common.prev'),
    next: t('common.next'),
    status: t('common.pageStatus'),
  };
}

function renderCustomProviderSection(providers, paged, reload, content, builtinConfigs, catalog, runtimeConfig) {
  const section = el('details', {
    class: 'panel provider-section-collapsible provider-custom-section',
    open: customSectionOpen,
    ontoggle: (event) => {
      customSectionOpen = event.currentTarget.open;
    },
  },
    el('summary', { class: 'provider-section-summary' },
      el('span', { class: 'provider-section-summary-main' },
        el('strong', {}, t('providers.customProviders')),
        el('small', {}, t('providers.customSectionHelp')),
      ),
      el('span', { class: 'provider-section-count' }, `${providers.length} ${t('providers.registryItems')}`),
    ),
    el('div', { class: 'providers-content provider-section-content' },
      el('div', { class: 'provider-section-toolbar' },
        button(t('providers.createTitle'), {
          variant: 'primary',
          size: 'sm',
          onClick: (event) => openCreateProvider(reload, event.currentTarget),
        }),
      ),
      providers.length ? el('div', { class: 'provider-list bounded-list provider-compact-list' }, paged.items.map((provider) => providerCard(provider, reload))) :
        emptyState(t('providers.empty')),
    ),
    paginationBar({
      page: providerPage,
      total: providers.length,
      pageSize: PROVIDER_PAGE_SIZE,
      labels: pagerLabels(),
      onPage: (page) => {
        providerPage = page;
        customSectionOpen = true;
        renderProviders(content, providers, builtinConfigs, catalog, runtimeConfig, reload);
      },
    }),
  );
  return section;
}

function renderProviders(content, providers, builtinConfigs, catalog, runtimeConfig, reload) {
  const paged = pageSlice(providers, providerPage, PROVIDER_PAGE_SIZE);
  providerPage = paged.current;
  const catalogRelease = releaseCatalogProviders(catalog);
  const reserved = reservedProviders(catalog);

  mount(content,
    pageHeader({
      kicker: t('providers.kicker'),
      title: t('providers.title'),
      subtitle: t('providers.subtitle'),
      actions: [button(t('common.refresh'), { onClick: reload })],
    }),
    el('div', { class: 'provider-layout provider-layout-bounded' },
      el('div', { class: 'provider-sections' },
        renderRuntimeSettingsPanel(runtimeConfig),
        renderBuiltinConfigPanel(builtinConfigs, reload),
        renderCustomProviderSection(providers, paged, reload, content, builtinConfigs, catalog, runtimeConfig),
        renderReadOnlyPanel(t('providers.catalogProviders'), t('providers.readOnlyHelp'), catalogRelease, 'catalog'),
        renderReadOnlyPanel(t('providers.reservedProviders'), t('providers.readOnlyHelp'), reserved, 'reserved'),
      ),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');

  async function reload() {
    mount(content, loadingState(t('providers.loading')));
    try {
      const [result, builtinResult, catalog, runtimeConfig] = await Promise.all([
        loadProviders(),
        loadBuiltinProviderConfigs(),
        loadCatalog(),
        loadAdminConfig(),
      ]);
      if (hasProviderSecretField(result) || hasProviderSecretField(builtinResult)) {
        mount(content,
          pageHeader({ kicker: t('providers.kicker'), title: t('providers.title'), subtitle: t('providers.subtitle') }),
          errorState(t('providers.securityError')),
        );
        return;
      }
      const providers = dataArray(result);
      const builtinConfigs = dataArray(builtinResult);
      providerPage = clampPage(providerPage, providers.length, PROVIDER_PAGE_SIZE);
      renderProviders(content, providers, builtinConfigs, catalog, runtimeConfig, reload);
    } catch (_) {
      mount(content,
        pageHeader({ kicker: t('providers.kicker'), title: t('providers.title'), subtitle: t('providers.subtitle') }),
        errorState(t('providers.error')),
      );
    }
  }

  await reload();
}

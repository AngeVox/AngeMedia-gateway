import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { clampPage, pageSlice, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import {
  builtinProviders,
  releaseCatalogProviders,
  renderReadOnlyPanel,
  reservedProviders,
} from './catalog-sections.js';
import { dataArray, loadCatalog, loadProviders } from './provider-api.js';
import { providerCard } from './provider-card.js';
import { createProviderForm } from './provider-form.js';
import { hasProviderSecretField } from './provider-validation.js';

const PROVIDER_PAGE_SIZE = 5;

let providerPage = 1;

function pagerLabels() {
  return {
    prev: t('common.prev'),
    next: t('common.next'),
    status: t('common.pageStatus'),
  };
}

function renderProviders(content, providers, catalog, reload) {
  const paged = pageSlice(providers, providerPage, PROVIDER_PAGE_SIZE);
  providerPage = paged.current;
  const builtin = builtinProviders(catalog);
  const catalogRelease = releaseCatalogProviders(catalog);
  const reserved = reservedProviders(catalog);

  mount(content,
    pageHeader({
      kicker: t('providers.kicker'),
      title: t('providers.title'),
      subtitle: t('providers.subtitle'),
      actions: [button(t('common.refresh'), { onClick: reload })],
    }),
    el('div', { class: 'provider-layout' },
      createProviderForm(reload),
      el('div', { class: 'provider-sections' },
        panel({ title: t('providers.customProviders'), subtitle: t('providers.advancedNote') },
          el('div', { class: 'providers-content' },
            providers.length ? el('div', { class: 'provider-list bounded-list' }, paged.items.map((provider) => providerCard(provider, reload))) :
              emptyState(t('providers.empty')),
          ),
          paginationBar({
            page: providerPage,
            total: providers.length,
            pageSize: PROVIDER_PAGE_SIZE,
            labels: pagerLabels(),
            onPage: (page) => {
              providerPage = page;
              renderProviders(content, providers, catalog, reload);
            },
          }),
        ),
        renderReadOnlyPanel(t('providers.builtinProviders'), t('providers.readOnlyHelp'), builtin, 'builtin'),
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
      const result = await loadProviders();
      const catalog = await loadCatalog();
      if (hasProviderSecretField(result)) {
        mount(content,
          pageHeader({ kicker: t('providers.kicker'), title: t('providers.title'), subtitle: t('providers.subtitle') }),
          errorState(t('providers.securityError')),
        );
        return;
      }
      const providers = dataArray(result);
      providerPage = clampPage(providerPage, providers.length, PROVIDER_PAGE_SIZE);
      renderProviders(content, providers, catalog, reload);
    } catch (_) {
      mount(content,
        pageHeader({ kicker: t('providers.kicker'), title: t('providers.title'), subtitle: t('providers.subtitle') }),
        errorState(t('providers.error')),
      );
    }
  }

  await reload();
}

import { captureSessionScope } from '../../auth.js';
import { bindViewResources, createRenderScope } from '../../router.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { pageSlice, paginationBar } from '../../components/pagination.js';
import { emptyState } from '../../components/states.js';
import { loadProviders } from './provider-api.js';
import { customProviderActions } from './provider-action-gate.js';
import { providerCard } from './provider-card.js?v=provider-drawer-sections-1';
import { openCreateProvider } from './provider-form.js?v=provider-drawer-sections-1';
import { hasProviderSecretField } from './provider-validation.js';

const PAGE_SIZE = 5;
let page = 1;
let expanded = false;

export function renderCustomProviderSection() {
  let providers = [];
  let cards = new Map();
  let pagerKey;
  const rechecking = new Set();
  const session = captureSessionScope();
  const active = () => resources.isActive();
  const isBlocked = id => rechecking.has(id);
  const count = el('span', { class: 'provider-section-count' });
  const list = el('div', { class: 'provider-list bounded-list provider-compact-list' });
  const empty = emptyState(t('providers.empty'));
  const pager = el('div');
  const status = el('p', { class: 'field-help', role: 'status' }, t('providers.loading'));
  const retry = el('div', { hidden: true }, button(t('common.refresh'), {
    size: 'sm', onClick: () => { if (!retry.hidden) return refresh(); },
  }));
  const section = el('details', {
    class: 'panel provider-section-collapsible provider-custom-section',
    open: expanded,
    ontoggle: event => { expanded = event.currentTarget.open; },
  },
    el('summary', { class: 'provider-section-summary' },
      el('span', { class: 'provider-section-summary-main' },
        el('strong', {}, t('providers.customProviders')),
        el('small', {}, t('providers.customSectionHelp')),
      ),
      count,
    ),
    el('div', { class: 'providers-content provider-section-content' },
      el('div', { class: 'provider-section-toolbar' },
        button(t('providers.createTitle'), {
          variant: 'primary', size: 'sm',
          onClick: (event) => { if (active()) openCreateProvider(refresh, event.currentTarget); },
        }),
      ),
      status, retry, list, pager,
    ),
  );
  const view = createRenderScope(section);
  const unsubscribe = customProviderActions.subscribe((id, reconcile) => {
    // 写入结束后等最新列表确认，避免从旧的启用状态再次发出同一请求。
    if (reconcile) rechecking.add(id);
    if (!active()) return;
    cards.get(id)?.sync();
    if (reconcile) refresh();
  });
  const resources = bindViewResources(() => view.isActive() && session(), {
    dispose: unsubscribe, suspend: () => view.nextRequest(), resume: refresh,
  });

  function renderRows() {
    const paged = pageSlice(providers, page, PAGE_SIZE);
    page = paged.current;
    const nextCards = new Map(paged.items.map(provider => {
      const card = cards.get(provider.id) || providerCard(provider, { isActive: active, isBlocked });
      card.update(provider);
      return [provider.id, card];
    }));
    const rows = [...nextCards.values()].map(card => card.row);
    if (!rows.length) rows.push(empty);
    if (list.children.length !== rows.length || rows.some((row, index) => list.children[index] !== row)) {
      list.replaceChildren(...rows);
    }
    cards = nextCards;
    count.textContent = `${providers.length} ${t('providers.registryItems')}`;
    const nextPagerKey = `${page}:${providers.length}`;
    if (pagerKey === nextPagerKey) return;
    pagerKey = nextPagerKey;
    pager.replaceChildren(paginationBar({
      page, total: providers.length, pageSize: PAGE_SIZE,
      labels: { prev: t('common.prev'), next: t('common.next'), status: t('common.pageStatus') },
      onPage: next => { if (active()) { page = next; renderRows(); } },
    }));
  }

  async function refresh() {
    if (!active()) return;
    const current = view.nextRequest();
    retry.hidden = true;
    let errorKey = 'providers.error';
    try {
      const response = await loadProviders();
      if (!current() || !active()) return;
      if (hasProviderSecretField(response)) {
        errorKey = 'providers.securityError';
        throw new Error();
      }
      if (!Array.isArray(response?.data)) throw new Error();
      providers = response.data;
      rechecking.clear();
      renderRows();
      status.hidden = true;
    } catch (_) {
      if (!current() || !active()) return;
      status.textContent = t(errorKey);
      status.hidden = false;
      retry.hidden = false;
    }
  }

  queueMicrotask(refresh);
  return section;
}

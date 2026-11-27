import { t } from '../../i18n.js';
import { captureSessionScope } from '../../auth.js';
import { createRenderScope } from '../../router.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { pageHeader } from '../../components/page.js';
import { errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import {
  releaseCatalogProviders,
  renderReadOnlyPanel,
  reservedProviders,
} from './catalog-sections.js?v=provider-drawer-sections-1';
import { renderBuiltinConfigPanel } from './builtin-config.js?v=provider-drawer-sections-1';
import { renderCustomProviderSection } from './custom-provider-section.js';
import { dataArray, loadBuiltinProviderConfigs, loadCatalog } from './provider-api.js';
import { renderRuntimeSettingsPanel } from './runtime-settings.js?v=web-studio-2j';
import { renderConnectivitySettingsPanel } from './connectivity-settings.js?v=provider-connectivity-1';
import { hasProviderSecretField } from './provider-validation.js';

function renderAdvancedSettings(onTransportSaved) {
  return el('details', { class: 'panel provider-section-collapsible provider-advanced-settings' },
    el('summary', { class: 'provider-section-summary' },
      el('span', { class: 'provider-section-summary-main' },
        el('strong', {}, t('providers.advancedSettings')),
        el('small', {}, t('providers.advancedSettingsHelp')),
      ),
    ),
    el('div', { class: 'provider-section-content' },
      renderRuntimeSettingsPanel(),
      renderConnectivitySettingsPanel({ onTransportSaved }),
    ),
  );
}

function renderProviders(content, builtinConfigs, catalog, custom, advanced, reload) {
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
        renderBuiltinConfigPanel(builtinConfigs, reload, catalog),
        custom,
        advanced,
        renderReadOnlyPanel(t('providers.catalogProviders'), t('providers.readOnlyHelp'), catalogRelease, 'catalog'),
        renderReadOnlyPanel(t('providers.reservedProviders'), t('providers.readOnlyHelp'), reserved, 'reserved'),
      ),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  const session = captureSessionScope();
  let advanced, custom;

  async function reload({ preserveSettings = false } = {}) {
    if (!scope.isActive() || !session()) return;
    const request = scope.nextRequest();
    const current = () => request() && session();
    // 全局代理保存只刷新内置渠道证据，保留其他设置和自定义列表的在途状态。
    if (!preserveSettings) {
      advanced = null;
      custom = null;
      mount(content, loadingState(t('providers.loading')));
    }
    try {
      const [builtinResult, catalog] = await Promise.all([
        loadBuiltinProviderConfigs(),
        loadCatalog(),
      ]);
      if (!current()) return;
      if (hasProviderSecretField(builtinResult)) {
        if (preserveSettings) { toast(t('providers.securityError'), 'error'); return; }
        mount(content,
          pageHeader({ kicker: t('providers.kicker'), title: t('providers.title'), subtitle: t('providers.subtitle') }),
          errorState(t('providers.securityError')),
        );
        return;
      }
      const builtinConfigs = dataArray(builtinResult);
      advanced ??= renderAdvancedSettings(() => reload({ preserveSettings: true }));
      custom ??= renderCustomProviderSection();
      renderProviders(content, builtinConfigs, catalog, custom, advanced, reload);
    } catch (_) {
      if (!current()) return;
      if (preserveSettings) { toast(t('providers.error'), 'error'); return; }
      mount(content,
        pageHeader({ kicker: t('providers.kicker'), title: t('providers.title'), subtitle: t('providers.subtitle') }),
        errorState(t('providers.error')),
      );
    }
  }

  await reload();
}

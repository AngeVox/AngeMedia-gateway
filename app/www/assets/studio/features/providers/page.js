import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { el, mount } from '../../components/dom.js';
import { field, input, select, textarea, toggle } from '../../components/forms.js';
import { confirmModal } from '../../components/modal.js';
import { clampPage, pageSlice, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { formatDate, shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

const PROVIDER_SECRET_RESPONSE_FIELDS = [
  'api_key',
  '_api_key',
  'key',
  'secret',
  '_secret',
  'token',
  'access_token',
  'password',
  'raw',
  'raw_response',
  'raw_error',
  'exception',
  'stack',
];
const PROVIDER_PAGE_SIZE = 5;

let providerPage = 1;
let editingProvider = null;
const providerTestResults = new Map();

function hasProviderSecretField(item) {
  if (!item || typeof item !== 'object') return false;
  if (Array.isArray(item)) return item.some(hasProviderSecretField);
  return Object.keys(item).some((key) => PROVIDER_SECRET_RESPONSE_FIELDS.includes(key)) ||
    Object.values(item).some(hasProviderSecretField);
}

function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

function catalogProviders(catalog) {
  return Array.isArray(catalog?.providers) ? catalog.providers : [];
}

function builtinProviders(catalog) {
  return catalogProviders(catalog).filter((provider) => String(provider.ui_group || '').startsWith('builtin'));
}

function reservedProviders(catalog) {
  return catalogProviders(catalog).filter((provider) => provider.status === 'reserved');
}

function releaseCatalogProviders(catalog) {
  return catalogProviders(catalog).filter((provider) => (
    provider.status !== 'reserved' &&
    !String(provider.ui_group || '').startsWith('builtin')
  ));
}

function readOnly() {
  return badge(t('providers.readOnly'), 'muted');
}

function pagerLabels() {
  return {
    prev: t('common.prev'),
    next: t('common.next'),
    status: t('common.pageStatus'),
  };
}

function providerStatus(provider) {
  if (provider.enabled) return badge(t('providers.enabled'), 'success');
  return badge(t('providers.disabled'), 'muted');
}

function keyStatus(provider) {
  if (provider.api_key_configured) return badge(t('providers.configured'), 'success');
  return badge(t('providers.notConfigured'), 'warning');
}

function providerCreateErrorMessage(error) {
  const detail = typeof error?.detail === 'string' ? error.detail : '';
  const message = [
    error?.safe?.human_hint,
    error?.safe?.message,
    error?.message,
    detail,
  ].filter(Boolean).join(' ');
  const safeDetail = detail ? ` ${t('providers.errorDetailPrefix')} ${safeText(detail, 180)}` : '';

  if (/只允许\s*http|http\s*or\s*https|missing.*scheme|invalid.*url|URL.*scheme/i.test(message)) {
    return `${t('providers.baseUrlMissingProtocol')}${safeDetail}`;
  }

  if (/内网|保留地址|私网|private|reserved|loopback|link-local|localhost|127\.0\.0\.1|::1/i.test(message)) {
    return `${t('providers.privateUrlPolicy')}${safeDetail}`;
  }

  return safeErrorMessage(error, t('providers.createError'));
}

function validateProviderBaseUrl(value) {
  const text = value.trim();
  if (!/^https?:\/\//i.test(text)) return t('providers.baseUrlMissingProtocol');

  let url;
  try {
    url = new URL(text);
  } catch (_) {
    return t('providers.baseUrlInvalid');
  }

  if (/\/images\/generations\/?$/i.test(url.pathname) || /\/images\/generations\//i.test(url.pathname)) {
    return t('providers.baseUrlNoEndpoint');
  }

  return '';
}

function providerTestSummary(provider) {
  const result = providerTestResults.get(provider.id);
  if (!result) return null;
  const ok = result.ok === true;
  const status = safeText(result.status || '-', 48);
  const message = safeText(result.message || (ok ? t('providers.testSuccess') : t('providers.testFailed')), 160);
  const modelFound = result.model_found === true ? t('providers.modelFoundYes') : t('providers.modelFoundNo');
  const elapsed = Number.isFinite(Number(result.elapsed_ms)) ? `${Number(result.elapsed_ms)}ms` : '-';
  return el('div', { class: `provider-test-result ${ok ? 'ok' : 'failed'}` },
    el('span', {}, `${t('providers.testStatus')}: ${status}`),
    el('span', {}, `${t('providers.modelFound')}: ${modelFound}`),
    el('span', {}, `${t('providers.elapsedMs')}: ${elapsed}`),
    el('p', {}, message),
  );
}

async function testProvider(provider, reload) {
  try {
    const result = await api.post(`/admin/providers/${encodeURIComponent(provider.id)}/test`);
    if (hasProviderSecretField(result)) {
      toast(t('providers.securityError'), 'error');
      return;
    }
    providerTestResults.set(provider.id, result);
    toast(result.ok ? t('providers.testSuccess') : t('providers.testFailed'), result.ok ? 'success' : 'warning');
    await reload();
  } catch (error) {
    const detail = error?.detail;
    if (detail?.status === 'test_not_supported') {
      providerTestResults.set(provider.id, {
        ok: false,
        status: 'test_not_supported',
        message: detail.message || t('providers.testUnsupported'),
      });
      await reload();
      return;
    }
    toast(safeErrorMessage(error, t('providers.testFailed')), 'error');
  }
}

async function openEditProvider(provider, reload) {
  editingProvider = provider;
  let detail;
  try {
    const result = await api.get(`/admin/providers/${encodeURIComponent(provider.id)}`);
    if (hasProviderSecretField(result)) {
      toast(t('providers.securityError'), 'error');
      return;
    }
    detail = result?.data || {};
  } catch (error) {
    toast(safeErrorMessage(error, t('providers.editLoadError')), 'error');
    return;
  }
  if (!detail.editable) {
    toast(t('providers.readOnly'), 'warning');
    return;
  }

  const overlay = el('div', { class: 'modal-overlay' });
  const close = () => {
    editingProvider = null;
    overlay.remove();
  };
  const nameInput = input({ name: 'name', type: 'text', maxLength: 80, value: detail.name || '' });
  const endpointInput = input({ name: 'base_url', type: 'url', value: detail.base_url || '', placeholder: t('providers.endpointPlaceholder') });
  const modelInput = input({ name: 'default_model', type: 'text', maxLength: 120, value: detail.default_model || '' });
  const editSecretPlaceholder = t('providers.editSecretPlaceholder');
  const secretInput = input({ name: 'api_key', type: 'password', autocomplete: 'new-password', placeholder: editSecretPlaceholder });
  const notesInput = textarea({ name: 'notes', class: 'compact-textarea provider-notes-input', maxLength: 800, value: detail.notes || '' });
  notesInput.value = detail.notes || '';
  const enabledToggle = toggle(t('providers.enabled'), { name: 'enabled', checked: detail.enabled === true });
  const enabledInput = enabledToggle.querySelector('input');
  const formError = el('p', { class: 'form-error', hidden: true });

  function showEditError(message) {
    formError.textContent = safeText(message, 260);
    formError.hidden = false;
  }

  const editSubmit = button(t('providers.editSubmit') || 'Edit', {
    variant: 'primary',
    onClick: async () => {
      formError.hidden = true;
      const baseUrlError = validateProviderBaseUrl(endpointInput.value);
      if (baseUrlError) {
        showEditError(baseUrlError);
        return;
      }
      const payload = {
        name: nameInput.value.trim(),
        base_url: endpointInput.value.trim(),
        default_model: modelInput.value.trim(),
        api_key: secretInput.value.trim(),
        enabled: enabledInput.checked,
        notes: notesInput.value.trim(),
      };
      editSubmit.disabled = true;
      try {
        const result = await api.patch(`/admin/providers/${encodeURIComponent(editingProvider.id)}`, payload);
        if (hasProviderSecretField(result)) {
          showEditError(t('providers.securityError'));
          return;
        }
        toast(t('providers.editSuccess'), 'success');
        close();
        await reload();
      } catch (error) {
        showEditError(safeErrorMessage(error, t('providers.editError')));
      } finally {
        editSubmit.disabled = false;
      }
    },
  });

  overlay.appendChild(el('div', { class: 'modal provider-edit-modal', role: 'dialog', ariaModal: 'true' },
    el('h2', {}, t('providers.editTitle')),
    el('div', { class: 'form-stack' },
      field(t('providers.name'), nameInput),
      field(t('providers.endpoint'), endpointInput, { help: t('providers.baseUrlHelp') }),
      field(t('providers.defaultModel'), modelInput),
      field(t('providers.secret'), secretInput, { help: t('providers.editSecretHelp') }),
      field(t('providers.notes'), notesInput),
      enabledToggle,
      formError,
    ),
    el('div', { class: 'action-row' },
      button(t('common.cancel'), { onClick: close }),
      editSubmit,
    ),
  ));
  document.body.appendChild(overlay);
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

function providerCard(provider, reload) {
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

function readOnlyProviderCard(provider, kind) {
  const media = Array.isArray(provider.media_types) ? provider.media_types.join(', ') : provider.media_type || '-';
  return el('article', { class: 'provider-card provider-readonly-card' },
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

function createProviderForm(reload) {
  const nameInput = input({ name: 'name', type: 'text', maxLength: 80, placeholder: t('providers.namePlaceholder') });
  const typeSelect = select([{ value: 'openai_image', label: t('providers.typeOpenAIImage') }], { name: 'provider_type' });
  const endpointInput = input({ name: 'base_url', type: 'url', placeholder: t('providers.endpointPlaceholder') });
  const modelInput = input({ name: 'default_model', type: 'text', maxLength: 120, placeholder: t('providers.defaultModelPlaceholder') });
  const secretInput = input({ name: 'api_key', type: 'password', autocomplete: 'new-password', placeholder: t('providers.secretPlaceholder') });
  const enabledToggle = toggle(t('providers.createEnabled'), { name: 'enabled', checked: true });
  const enabledInput = enabledToggle.querySelector('input');
  const submit = button(t('providers.createSubmit'), { variant: 'primary' });
  const formError = el('p', { class: 'form-error', hidden: true });

  function showFormError(message) {
    formError.textContent = safeText(message, 260);
    formError.hidden = false;
  }

  function clearFormError() {
    formError.textContent = '';
    formError.hidden = true;
  }

  [nameInput, endpointInput, modelInput, secretInput, typeSelect, enabledInput].forEach((control) => {
    control.addEventListener('input', clearFormError);
    control.addEventListener('change', clearFormError);
  });

  submit.addEventListener('click', async () => {
    clearFormError();
    const payload = {
      name: nameInput.value.trim(),
      provider_type: typeSelect.value,
      base_url: endpointInput.value.trim(),
      default_model: modelInput.value.trim(),
      api_key: secretInput.value.trim(),
      enabled: enabledInput.checked,
    };
    if (!payload.name || !payload.base_url || !payload.default_model) {
      showFormError(t('providers.createRequired'));
      toast(t('providers.createRequired'), 'error');
      return;
    }
    const baseUrlError = validateProviderBaseUrl(payload.base_url);
    if (baseUrlError) {
      showFormError(baseUrlError);
      toast(baseUrlError, 'error');
      return;
    }
    submit.disabled = true;
    submit.textContent = t('providers.creating');
    try {
      const result = await api.post('/admin/providers', payload);
      if (hasProviderSecretField(result)) {
        showFormError(t('providers.securityError'));
        toast(t('providers.securityError'), 'error');
        return;
      }
      nameInput.value = '';
      endpointInput.value = '';
      modelInput.value = '';
      secretInput.value = '';
      enabledInput.checked = true;
      toast(t('providers.createSuccess'), 'success');
      await reload();
    } catch (error) {
      const message = providerCreateErrorMessage(error);
      showFormError(message);
      toast(message, 'error');
    } finally {
      submit.disabled = false;
      submit.textContent = t('providers.createSubmit');
    }
  });

  return panel({ title: t('providers.createTitle'), subtitle: t('providers.subtitle') },
    el('div', { class: 'panel-body form-stack' },
      el('div', { class: 'form-grid' },
        field(t('providers.name'), nameInput),
        field(t('providers.type'), typeSelect),
        field(t('providers.endpoint'), endpointInput, { help: t('providers.baseUrlHelp') }),
        field(t('providers.defaultModel'), modelInput),
        field(t('providers.secret'), secretInput),
        enabledToggle,
      ),
      formError,
      el('div', { class: 'action-row' }, submit),
    ),
  );
}

function renderReadOnlyPanel(title, subtitle, items, kind) {
  return panel({ title, subtitle },
    el('div', { class: 'providers-content' },
      items.length ? el('div', { class: 'provider-list bounded-list' }, items.map((provider) => readOnlyProviderCard(provider, kind))) :
        emptyState(t('providers.emptyReadOnly')),
    ),
  );
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
      const result = await api.get('/admin/providers');
      const catalog = await api.get('/admin/catalog').catch(() => ({ providers: [] }));
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

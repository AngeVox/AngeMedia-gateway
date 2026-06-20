import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, select, textarea, toggle } from '../../components/forms.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';
import { openProviderDrawer } from './provider-drawer.js?v=provider-drawer-sections-1';
import {
  hasProviderSecretField,
  providerCreateErrorMessage,
  validateProviderBaseUrl,
} from './provider-validation.js';


function providerFormContent({ detail = null, reload, close }) {
  const editing = Boolean(detail);
  const nameInput = input({
    name: 'name',
    type: 'text',
    maxLength: 80,
    value: detail?.name || '',
    placeholder: t('providers.namePlaceholder'),
  });
  const typeSelect = select([{ value: 'openai_image', label: t('providers.typeOpenAIImage') }], {
    name: 'provider_type',
    value: detail?.provider_type || 'openai_image',
    disabled: editing,
  });
  const endpointInput = input({
    name: 'base_url',
    type: 'url',
    value: detail?.base_url || '',
    placeholder: t('providers.endpointPlaceholder'),
  });
  const modelInput = input({
    name: 'default_model',
    type: 'text',
    maxLength: 120,
    value: detail?.default_model || '',
    placeholder: t('providers.defaultModelPlaceholder'),
  });
  const secretInput = input({
    name: 'api_key',
    type: 'password',
    value: '',
    autocomplete: 'new-password',
    placeholder: editing ? t('providers.editSecretPlaceholder') : t('providers.secretPlaceholder'),
  });
  const notesInput = editing ? textarea({
    name: 'notes',
    class: 'compact-textarea provider-notes-input',
    maxLength: 800,
    value: detail?.notes || '',
  }) : null;
  if (notesInput) notesInput.value = detail?.notes || '';
  const enabledToggle = toggle(editing ? t('providers.enabled') : t('providers.createEnabled'), {
    name: 'enabled',
    checked: editing ? detail?.enabled === true : true,
  });
  const enabledInput = enabledToggle.querySelector('input');
  const formError = el('p', { class: 'form-error', hidden: true });

  function showError(message) {
    formError.textContent = safeText(message, 260);
    formError.hidden = false;
  }

  function clearError() {
    formError.textContent = '';
    formError.hidden = true;
  }

  [nameInput, endpointInput, modelInput, secretInput, typeSelect, enabledInput, notesInput]
    .filter(Boolean)
    .forEach((control) => {
      control.addEventListener('input', clearError);
      control.addEventListener('change', clearError);
    });

  const submit = button(editing ? t('providers.editSubmit') : t('providers.createSubmit'), {
    variant: 'primary',
    onClick: async () => {
      clearError();
      const payload = {
        name: nameInput.value.trim(),
        base_url: endpointInput.value.trim(),
        default_model: modelInput.value.trim(),
        api_key: secretInput.value.trim(),
        enabled: enabledInput.checked,
      };
      if (!editing) payload.provider_type = typeSelect.value;
      if (editing) payload.notes = notesInput.value.trim();
      if (!payload.name || !payload.base_url || !payload.default_model) {
        showError(t('providers.createRequired'));
        return;
      }
      const baseUrlError = validateProviderBaseUrl(payload.base_url);
      if (baseUrlError) {
        showError(baseUrlError);
        return;
      }
      submit.disabled = true;
      submit.textContent = editing ? t('providers.editSubmit') : t('providers.creating');
      try {
        const result = editing ?
          await api.patch(`/admin/providers/${encodeURIComponent(detail.id)}`, payload) :
          await api.post('/admin/providers', payload);
        if (hasProviderSecretField(result)) {
          showError(t('providers.securityError'));
          return;
        }
        secretInput.value = '';
        toast(editing ? t('providers.editSuccess') : t('providers.createSuccess'), 'success');
        close();
        await reload();
      } catch (error) {
        showError(editing ? safeErrorMessage(error, t('providers.editError')) : providerCreateErrorMessage(error));
      } finally {
        submit.disabled = false;
        submit.textContent = editing ? t('providers.editSubmit') : t('providers.createSubmit');
      }
    },
  });

  return {
    initialFocus: nameInput,
    content: el('div', { class: 'form-stack provider-drawer-form' },
      field(t('providers.name'), nameInput),
      field(t('providers.type'), typeSelect),
      field(t('providers.endpoint'), endpointInput, { help: t('providers.baseUrlHelp') }),
      field(t('providers.defaultModel'), modelInput),
      field(t('providers.secret'), secretInput, {
        help: editing ? t('providers.editSecretHelp') : '',
      }),
      notesInput ? field(t('providers.notes'), notesInput) : null,
      enabledToggle,
      formError,
    ),
    footer: [
      button(t('common.cancel'), { variant: 'secondary', onClick: close }),
      submit,
    ],
  };
}


export function openCreateProvider(reload, trigger) {
  openProviderDrawer({
    title: t('providers.createTitle'),
    description: t('providers.createDrawerHelp'),
    trigger,
    build: (close) => providerFormContent({ reload, close }),
  });
}


export async function openEditProvider(provider, reload, trigger) {
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

  openProviderDrawer({
    title: t('providers.editTitle'),
    identity: safeText(detail.name || provider.name || provider.id || '-', 96),
    identityMeta: `${safeText(detail.id || provider.id || '-', 64)} · ${safeText(detail.provider_type || provider.provider_type || '-', 60)}`,
    status: badge(detail.enabled ? t('providers.enabled') : t('providers.disabled'), detail.enabled ? 'success' : 'muted'),
    trigger,
    build: (close) => providerFormContent({ detail, reload, close }),
  });
}

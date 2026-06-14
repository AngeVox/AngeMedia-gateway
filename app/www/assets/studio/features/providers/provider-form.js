import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input, select, textarea, toggle } from '../../components/forms.js';
import { panel } from '../../components/page.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';
import {
  hasProviderSecretField,
  providerCreateErrorMessage,
  validateProviderBaseUrl,
} from './provider-validation.js';

let editingProvider = null;

export async function openEditProvider(provider, reload) {
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

export function createProviderForm(reload) {
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

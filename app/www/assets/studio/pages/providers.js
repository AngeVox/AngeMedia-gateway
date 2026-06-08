import { api } from '../api.js';
import { t } from '../i18n.js';

const FORBIDDEN_RESPONSE_FIELDS = [
  'api_key',
  'masked_api_key',
  'api_key_preview',
  '_api_key',
  'key_hash',
  'secret',
  '_secret',
  'token',
  'access_token',
  'password',
  'base_url',
  'status_url',
  'quota_url',
  'last_error',
  'notes',
  'raw',
  'raw_body',
  'raw_response',
  'raw_error',
  'exception',
  'stack',
];

function formatDate(dateStr) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
}

function shortId(id) {
  if (!id) return '-';
  return String(id).substring(0, 8);
}

function hasForbiddenField(item) {
  if (!item || typeof item !== 'object') return false;
  return FORBIDDEN_RESPONSE_FIELDS.some(field =>
    Object.prototype.hasOwnProperty.call(item, field)
  );
}

function isProviderObject(item) {
  return Boolean(item && typeof item === 'object' && !Array.isArray(item));
}

function providerArrayFromResponse(result) {
  if (Array.isArray(result?.data)) return result.data;
  if (Array.isArray(result)) return result;
  return null;
}

function apiKeyConfigured(item) {
  return item?.api_key_configured === true;
}

function createTextCell(value) {
  const td = document.createElement('td');
  td.textContent = value || '-';
  return td;
}

function createBooleanCell(value, trueLabel, falseLabel) {
  const td = document.createElement('td');
  const badge = document.createElement('span');
  badge.className = value ? 'badge badge-success' : 'badge badge-pending';
  badge.textContent = value ? trueLabel : falseLabel;
  td.appendChild(badge);
  return td;
}

function createProviderTypeSelect() {
  const select = document.createElement('select');
  select.name = 'provider_type';
  select.className = 'form-control';

  const option = document.createElement('option');
  option.value = 'openai_image';
  option.textContent = t('providers.typeOpenAIImage');
  select.appendChild(option);

  return select;
}

function createField(labelText, control) {
  const label = document.createElement('label');
  label.className = 'field-label form-field';
  label.textContent = labelText;
  label.appendChild(control);
  return label;
}

function createInput({ name, type = 'text', required = false, placeholder = '' }) {
  const input = document.createElement('input');
  input.name = name;
  input.type = type;
  input.required = required;
  input.placeholder = placeholder;
  input.className = 'form-control';
  return input;
}

function createCreateForm(onCreated) {
  const card = document.createElement('div');
  card.className = 'card section-card';

  const title = document.createElement('h2');
  title.textContent = t('providers.createTitle');
  card.appendChild(title);

  const nameInput = createInput({
    name: 'name',
    required: true,
    placeholder: t('providers.namePlaceholder'),
  });
  const typeSelect = createProviderTypeSelect();
  const baseUrlInput = createInput({
    name: 'base_url',
    required: true,
    placeholder: t('providers.baseUrlPlaceholder'),
  });
  const defaultModelInput = createInput({
    name: 'default_model',
    required: true,
    placeholder: t('providers.defaultModelPlaceholder'),
  });
  const apiKeyInput = createInput({
    name: 'api_key',
    type: 'password',
    placeholder: t('providers.apiKeyPlaceholder'),
  });
  const enabledInput = document.createElement('input');
  enabledInput.name = 'enabled';
  enabledInput.type = 'checkbox';
  enabledInput.checked = true;

  card.appendChild(createField(t('providers.name'), nameInput));
  card.appendChild(createField(t('providers.type'), typeSelect));
  card.appendChild(createField(t('providers.baseUrl'), baseUrlInput));
  card.appendChild(createField(t('providers.defaultModel'), defaultModelInput));
  card.appendChild(createField(t('providers.apiKey'), apiKeyInput));

  const enabledLabel = document.createElement('label');
  enabledLabel.className = 'form-field';
  enabledLabel.appendChild(enabledInput);
  enabledLabel.appendChild(document.createTextNode(` ${t('providers.createEnabled')}`));
  card.appendChild(enabledLabel);

  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const submitBtn = document.createElement('button');
  submitBtn.type = 'button';
  submitBtn.className = 'btn btn-primary';
  submitBtn.textContent = t('providers.createSubmit');
  actions.appendChild(submitBtn);
  card.appendChild(actions);

  const status = document.createElement('p');
  status.className = 'text-muted';
  card.appendChild(status);

  submitBtn.addEventListener('click', async () => {
    const payload = {
      name: nameInput.value.trim(),
      provider_type: typeSelect.value,
      base_url: baseUrlInput.value.trim(),
      default_model: defaultModelInput.value.trim(),
      api_key: apiKeyInput.value.trim(),
      enabled: enabledInput.checked,
    };

    if (!payload.name || !payload.base_url || !payload.default_model) {
      status.className = 'error-text';
      status.textContent = t('providers.createRequired');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = t('providers.creating');
    status.className = 'text-muted';
    status.textContent = '';

    try {
      const result = await api.post('/admin/providers', payload);
      const created = result?.data;
      if (!isProviderObject(created) || hasForbiddenField(created)) {
        status.className = 'error-text';
        status.textContent = t('providers.securityError');
        return;
      }
      nameInput.value = '';
      baseUrlInput.value = '';
      defaultModelInput.value = '';
      apiKeyInput.value = '';
      enabledInput.checked = true;
      status.className = 'text-success';
      status.textContent = t('providers.createSuccess');
      await onCreated();
    } catch (_) {
      status.className = 'error-text';
      status.textContent = t('providers.createError');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = t('providers.createSubmit');
    }
  });

  return card;
}

function createActionCell(item, onToggled) {
  const td = document.createElement('td');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-sm';
  const nextEnabled = !Boolean(item.enabled);
  button.textContent = nextEnabled ? t('providers.enableAction') : t('providers.disableAction');
  td.appendChild(button);

  const status = document.createElement('span');
  status.className = 'text-muted';
  td.appendChild(status);

  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = t('providers.updating');
    try {
      const result = await api.post(`/admin/providers/${encodeURIComponent(item.id)}/enabled`, {
        enabled: nextEnabled,
      });
      const updated = result?.data;
      if (!isProviderObject(updated) || hasForbiddenField(updated)) {
        status.className = 'error-text';
        status.textContent = t('providers.securityError');
        return;
      }
      status.textContent = '';
      await onToggled();
    } catch (_) {
      status.className = 'error-text';
      status.textContent = t('providers.updateError');
    } finally {
      button.disabled = false;
    }
  });

  return td;
}

function renderTable(providers, onToggled) {
  const table = document.createElement('table');
  table.className = 'data-table';

  const headers = [
    t('providers.id'),
    t('providers.name'),
    t('providers.type'),
    t('providers.enabled'),
    t('providers.apiKey'),
    t('providers.defaultModel'),
    t('providers.sortOrder'),
    t('providers.lastTestStatus'),
    t('providers.lastResponseMs'),
    t('providers.lastTestAt'),
    t('providers.created'),
    t('providers.updated'),
    t('providers.actions'),
  ];

  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  headers.forEach(text => {
    const th = document.createElement('th');
    th.textContent = text;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  providers.forEach(item => {
    const row = document.createElement('tr');
    row.appendChild(createTextCell(shortId(item.id)));
    row.appendChild(createTextCell(item.name));
    row.appendChild(createTextCell(item.provider_type));
    row.appendChild(createBooleanCell(Boolean(item.enabled), t('providers.enabledYes'), t('providers.enabledNo')));
    row.appendChild(createBooleanCell(apiKeyConfigured(item), t('providers.configured'), t('providers.notConfigured')));
    row.appendChild(createTextCell(item.default_model));
    row.appendChild(createTextCell(String(item.sort_order ?? '-')));
    row.appendChild(createTextCell(item.last_test_status));
    row.appendChild(createTextCell(item.last_response_ms ? String(item.last_response_ms) : '-'));
    row.appendChild(createTextCell(formatDate(item.last_test_at)));
    row.appendChild(createTextCell(formatDate(item.created_at)));
    row.appendChild(createTextCell(formatDate(item.updated_at)));
    row.appendChild(createActionCell(item, onToggled));
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  return table;
}

async function loadProviders(card) {
  card.textContent = '';
  const loading = document.createElement('p');
  loading.className = 'text-muted';
  loading.textContent = t('providers.loading');
  card.appendChild(loading);

  try {
    const result = await api.get('/admin/providers');
    card.textContent = '';
    const providerItems = providerArrayFromResponse(result);
    if (!providerItems) {
      const errorText = document.createElement('p');
      errorText.className = 'error-text';
      errorText.textContent = t('providers.error');
      card.appendChild(errorText);
      return;
    }

    const providers = providerItems.filter(isProviderObject);

    if (providers.some(hasForbiddenField)) {
      const securityError = document.createElement('p');
      securityError.className = 'error-text';
      securityError.textContent = t('providers.securityError');
      card.appendChild(securityError);
      return;
    }

    if (providers.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'text-muted';
      empty.textContent = t('providers.empty');
      card.appendChild(empty);
      return;
    }

    card.appendChild(renderTable(providers, () => loadProviders(card)));
  } catch (_) {
    card.textContent = '';
    const errorText = document.createElement('p');
    errorText.className = 'error-text';
    errorText.textContent = t('providers.error');
    card.appendChild(errorText);
  }
}

export async function render() {
  const content = document.getElementById('content');
  content.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'page-header';
  const heading = document.createElement('h1');
  heading.className = 'page-heading';
  heading.textContent = t('providers.title');
  const subtitle = document.createElement('p');
  subtitle.className = 'page-subtitle';
  subtitle.textContent = t('providers.subtitle');
  header.append(heading, subtitle);
  content.appendChild(header);

  let listCard;
  const createCard = createCreateForm(() => loadProviders(listCard));
  content.appendChild(createCard);

  const card = document.createElement('div');
  card.className = 'card section-card';
  listCard = card;
  content.appendChild(card);

  await loadProviders(card);
}

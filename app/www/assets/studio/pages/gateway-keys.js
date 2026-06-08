import { api } from '../api.js';
import { t } from '../i18n.js';

const FORBIDDEN_RESPONSE_FIELDS = ['key', 'key_hash'];
const FORBIDDEN_CREATE_FIELDS = ['key_hash'];

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

function hasForbiddenCreateField(item) {
  if (!item || typeof item !== 'object') return false;
  return FORBIDDEN_CREATE_FIELDS.some(field =>
    Object.prototype.hasOwnProperty.call(item, field)
  );
}

function createTextCell(value) {
  const td = document.createElement('td');
  td.textContent = value || '-';
  return td;
}

function createStatusCell(item) {
  const td = document.createElement('td');
  const badge = document.createElement('span');
  badge.className = 'badge';

  if (item.revoked_at) {
    badge.classList.add('badge-error');
    badge.textContent = t('apiKeys.revoked');
  } else if (item.enabled) {
    badge.classList.add('badge-success');
    badge.textContent = t('apiKeys.enabled');
  } else {
    badge.classList.add('badge-pending');
    badge.textContent = t('apiKeys.disabled');
  }

  td.appendChild(badge);
  return td;
}

function renderTable(keys) {
  const table = document.createElement('table');
  table.className = 'data-table';

  const headers = [
    t('apiKeys.keyPrefix'),
    t('apiKeys.name'),
    t('apiKeys.status'),
    t('apiKeys.created'),
    t('apiKeys.lastUsed'),
    t('apiKeys.revokedAt'),
    t('apiKeys.note'),
    t('apiKeys.id'),
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
  keys.forEach(item => {
    const row = document.createElement('tr');
    row.appendChild(createTextCell(item.key_prefix));
    row.appendChild(createTextCell(item.name));
    row.appendChild(createStatusCell(item));
    row.appendChild(createTextCell(formatDate(item.created_at)));
    row.appendChild(createTextCell(formatDate(item.last_used_at)));
    row.appendChild(createTextCell(formatDate(item.revoked_at)));
    row.appendChild(createTextCell(item.note));
    row.appendChild(createTextCell(shortId(item.id)));
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  return table;
}

export async function render() {
  const content = document.getElementById('content');
  content.innerHTML = '';
  let oneTimeKey = '';

  const header = document.createElement('div');
  header.className = 'page-header';
  const heading = document.createElement('h1');
  heading.className = 'page-heading';
  heading.textContent = t('apiKeys.title');
  const subtitle = document.createElement('p');
  subtitle.className = 'page-subtitle';
  subtitle.textContent = t('apiKeys.subtitle');
  header.append(heading, subtitle);
  content.appendChild(header);

  const createCard = document.createElement('div');
  createCard.className = 'card section-card';

  const createButton = document.createElement('button');
  createButton.type = 'button';
  createButton.className = 'btn btn-primary';
  createButton.textContent = t('apiKeys.createButton');
  createCard.appendChild(createButton);

  const form = document.createElement('form');
  form.hidden = true;

  const nameLabel = document.createElement('label');
  nameLabel.className = 'field-label form-field';
  nameLabel.textContent = t('apiKeys.name');
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.autocomplete = 'off';
  nameInput.maxLength = 80;
  nameInput.placeholder = t('apiKeys.namePlaceholder');
  nameLabel.appendChild(nameInput);

  const noteLabel = document.createElement('label');
  noteLabel.className = 'field-label form-field';
  noteLabel.textContent = t('apiKeys.note');
  const noteInput = document.createElement('textarea');
  noteInput.rows = 2;
  noteInput.maxLength = 240;
  noteInput.placeholder = t('apiKeys.notePlaceholder');
  noteLabel.appendChild(noteInput);

  const formActions = document.createElement('div');
  formActions.className = 'form-actions';
  const submitButton = document.createElement('button');
  submitButton.type = 'submit';
  submitButton.className = 'btn btn-primary';
  submitButton.textContent = t('apiKeys.createSubmit');
  const cancelButton = document.createElement('button');
  cancelButton.type = 'button';
  cancelButton.className = 'btn';
  cancelButton.textContent = t('apiKeys.cancel');
  formActions.append(submitButton, cancelButton);

  const createStatus = document.createElement('div');
  createStatus.className = 'result-panel';

  form.append(nameLabel, noteLabel, formActions);
  createCard.append(form, createStatus);
  content.appendChild(createCard);

  const secretPanel = document.createElement('div');
  secretPanel.className = 'card section-card';
  secretPanel.hidden = true;
  content.appendChild(secretPanel);

  const card = document.createElement('div');
  card.className = 'card section-card';

  content.appendChild(card);

  function clearOneTimeSecret() {
    oneTimeKey = '';
    secretPanel.hidden = true;
    secretPanel.textContent = '';
  }

  function showCreateError(message) {
    createStatus.textContent = '';
    const errorText = document.createElement('p');
    errorText.textContent = message;
    errorText.className = 'error-text';
    createStatus.appendChild(errorText);
  }

  function renderOneTimeSecret(data, warning) {
    oneTimeKey = String(data.key || '');
    secretPanel.textContent = '';
    secretPanel.hidden = false;

    const title = document.createElement('h2');
    title.textContent = t('apiKeys.createdTitle');

    const warningText = document.createElement('p');
    warningText.className = 'text-danger';
    warningText.textContent = warning || t('apiKeys.createdWarning');

    const keyLabel = document.createElement('p');
    keyLabel.className = 'meta-title';
    keyLabel.textContent = t('apiKeys.fullKey');

    const keyBox = document.createElement('textarea');
    keyBox.className = 'form-control';
    keyBox.rows = 2;
    keyBox.readOnly = true;
    keyBox.value = oneTimeKey;

    const actions = document.createElement('div');
    actions.className = 'form-actions';
    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'btn btn-primary';
    copyButton.textContent = t('apiKeys.copy');
    const dismissButton = document.createElement('button');
    dismissButton.type = 'button';
    dismissButton.className = 'btn';
    dismissButton.textContent = t('apiKeys.dismiss');
    const copyStatus = document.createElement('span');
    copyStatus.className = 'text-muted';
    actions.append(copyButton, dismissButton, copyStatus);

    copyButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(oneTimeKey);
        copyStatus.textContent = t('apiKeys.copySuccess');
      } catch (_) {
        copyStatus.textContent = t('apiKeys.copyFailed');
      }
    });
    dismissButton.addEventListener('click', () => {
      clearOneTimeSecret();
    });

    secretPanel.append(title, warningText, keyLabel, keyBox, actions);
  }

  async function loadKeys() {
    card.textContent = '';
    const loading = document.createElement('p');
    loading.textContent = t('apiKeys.loading');
    loading.className = 'text-muted';
    card.appendChild(loading);

    try {
      const result = await api.get('/admin/gateway-keys');
      loading.remove();

      const keys = Array.isArray(result?.data) ? result.data : [];
      if (keys.some(hasForbiddenField)) {
        const securityError = document.createElement('p');
        securityError.textContent = t('apiKeys.securityError');
        securityError.className = 'error-text';
        card.appendChild(securityError);
        return;
      }

      if (keys.length === 0) {
        const empty = document.createElement('p');
        empty.textContent = t('apiKeys.empty');
        empty.className = 'text-muted';
        card.appendChild(empty);
        return;
      }

      card.appendChild(renderTable(keys));
    } catch (err) {
      loading.remove();
      const errorText = document.createElement('p');
      errorText.textContent = t('apiKeys.error');
      errorText.className = 'error-text';
      card.appendChild(errorText);
    }
  }

  createButton.addEventListener('click', () => {
    clearOneTimeSecret();
    createStatus.textContent = '';
    form.hidden = false;
    createButton.hidden = true;
    nameInput.focus();
  });

  cancelButton.addEventListener('click', () => {
    clearOneTimeSecret();
    createStatus.textContent = '';
    form.reset();
    form.hidden = true;
    createButton.hidden = false;
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearOneTimeSecret();
    createStatus.textContent = '';
    submitButton.disabled = true;
    submitButton.textContent = t('apiKeys.creating');

    try {
      const payload = {
        name: nameInput.value.trim(),
        note: noteInput.value.trim(),
      };
      const result = await api.post('/admin/gateway-keys', payload);
      const data = result?.data || {};
      if (hasForbiddenCreateField(data)) {
        showCreateError(t('apiKeys.securityError'));
        return;
      }
      if (!data.key) {
        showCreateError(t('apiKeys.createMissingKey'));
        return;
      }

      renderOneTimeSecret(data, result.warning);
      form.reset();
      form.hidden = true;
      createButton.hidden = false;
      await loadKeys();
    } catch (err) {
      clearOneTimeSecret();
      showCreateError(t('apiKeys.createError'));
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = t('apiKeys.createSubmit');
    }
  });

  await loadKeys();
}

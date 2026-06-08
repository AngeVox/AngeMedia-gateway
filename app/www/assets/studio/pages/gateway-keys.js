import { api } from '../api.js';
import { t } from '../i18n.js';

const FORBIDDEN_RESPONSE_FIELDS = ['key', 'key_hash'];
const FORBIDDEN_CREATE_FIELDS = ['key_hash'];
const FORBIDDEN_REVOKE_FIELDS = ['key', 'key_hash'];

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

function hasForbiddenRevokeField(value) {
  if (!value || typeof value !== 'object') return false;
  if (FORBIDDEN_REVOKE_FIELDS.some(field => Object.prototype.hasOwnProperty.call(value, field))) {
    return true;
  }
  return Object.values(value).some(hasForbiddenRevokeField);
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

function statusText(item) {
  if (item.revoked_at) return t('apiKeys.revoked');
  if (item.enabled) return t('apiKeys.enabled');
  return t('apiKeys.disabled');
}

function renderTable(keys, onRevoke) {
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
    t('apiKeys.actions'),
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
    const actionCell = document.createElement('td');
    if (!item.revoked_at && item.key_prefix) {
      const revokeButton = document.createElement('button');
      revokeButton.type = 'button';
      revokeButton.className = 'btn btn-danger btn-sm';
      revokeButton.textContent = t('apiKeys.revoke');
      revokeButton.addEventListener('click', () => onRevoke(item));
      actionCell.appendChild(revokeButton);
    } else {
      const unavailable = document.createElement('span');
      unavailable.className = 'text-muted';
      unavailable.textContent = item.revoked_at ? t('apiKeys.revoked') : t('apiKeys.revokeUnavailable');
      actionCell.appendChild(unavailable);
    }
    row.appendChild(actionCell);
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

  const revokePanel = document.createElement('div');
  revokePanel.className = 'card section-card';
  revokePanel.hidden = true;
  content.appendChild(revokePanel);

  const card = document.createElement('div');
  card.className = 'card section-card';

  content.appendChild(card);
  let pendingRevoke = null;

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

  function clearRevokeConfirmation() {
    pendingRevoke = null;
    revokePanel.hidden = true;
    revokePanel.textContent = '';
  }

  function showRevokeError(message) {
    revokePanel.textContent = '';
    revokePanel.hidden = false;
    const errorText = document.createElement('p');
    errorText.textContent = message;
    errorText.className = 'error-text';
    revokePanel.appendChild(errorText);
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

  function renderRevokeConfirmation(item) {
    pendingRevoke = item;
    revokePanel.textContent = '';
    revokePanel.hidden = false;

    const title = document.createElement('h2');
    title.textContent = t('apiKeys.revokeTitle');

    const warning = document.createElement('p');
    warning.className = 'text-danger';
    warning.textContent = t('apiKeys.revokeWarning');

    const detailRows = [
      [t('apiKeys.name'), item.name || '-'],
      [t('apiKeys.keyPrefix'), item.key_prefix || '-'],
      [t('apiKeys.created'), formatDate(item.created_at)],
      [t('apiKeys.status'), statusText(item)],
    ];
    const meta = document.createElement('div');
    meta.className = 'meta-row';
    detailRows.forEach(([label, value]) => {
      const line = document.createElement('p');
      line.className = 'meta-line';
      line.textContent = `${label}: ${value}`;
      meta.appendChild(line);
    });

    const prefixLabel = document.createElement('label');
    prefixLabel.className = 'field-label form-field';
    prefixLabel.textContent = t('apiKeys.revokeConfirmLabel');
    const prefixInput = document.createElement('input');
    prefixInput.type = 'text';
    prefixInput.autocomplete = 'off';
    prefixInput.className = 'form-control';
    prefixInput.placeholder = item.key_prefix || '';
    prefixLabel.appendChild(prefixInput);

    const status = document.createElement('p');
    status.className = 'text-muted';
    status.textContent = t('apiKeys.revokeConfirmHelp');

    const actions = document.createElement('div');
    actions.className = 'form-actions';
    const confirmButton = document.createElement('button');
    confirmButton.type = 'button';
    confirmButton.className = 'btn btn-danger';
    confirmButton.textContent = t('apiKeys.revoke');
    confirmButton.disabled = true;
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.className = 'btn';
    cancelButton.textContent = t('apiKeys.cancel');
    actions.append(confirmButton, cancelButton);

    prefixInput.addEventListener('input', () => {
      confirmButton.disabled = prefixInput.value !== item.key_prefix;
      status.textContent = confirmButton.disabled ? t('apiKeys.revokeConfirmHelp') : '';
    });

    cancelButton.addEventListener('click', () => {
      clearRevokeConfirmation();
    });

    confirmButton.addEventListener('click', async () => {
      if (!pendingRevoke || prefixInput.value !== pendingRevoke.key_prefix) {
        status.textContent = t('apiKeys.revokePrefixMismatch');
        return;
      }
      confirmButton.disabled = true;
      confirmButton.textContent = t('apiKeys.revoking');
      status.textContent = '';

      try {
        const result = await api.delete(`/admin/gateway-keys/${pendingRevoke.id}`);
        if (hasForbiddenRevokeField(result)) {
          showRevokeError(t('apiKeys.securityError'));
          return;
        }
        clearRevokeConfirmation();
        await loadKeys();
      } catch (_) {
        confirmButton.disabled = false;
        confirmButton.textContent = t('apiKeys.revoke');
        status.textContent = '';
        showRevokeError(t('apiKeys.revokeError'));
      }
    });

    revokePanel.append(title, warning, meta, prefixLabel, status, actions);
    prefixInput.focus();
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

      card.appendChild(renderTable(keys, renderRevokeConfirmation));
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
    clearRevokeConfirmation();
    createStatus.textContent = '';
    form.hidden = false;
    createButton.hidden = true;
    nameInput.focus();
  });

  cancelButton.addEventListener('click', () => {
    clearOneTimeSecret();
    clearRevokeConfirmation();
    createStatus.textContent = '';
    form.reset();
    form.hidden = true;
    createButton.hidden = false;
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearOneTimeSecret();
    clearRevokeConfirmation();
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

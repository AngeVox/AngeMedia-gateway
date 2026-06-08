import { api } from '../api.js';
import { t } from '../i18n.js';

const FORBIDDEN_RESPONSE_FIELDS = ['key', 'key_hash'];

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

  const card = document.createElement('div');
  card.className = 'card section-card';

  const loading = document.createElement('p');
  loading.textContent = t('apiKeys.loading');
  loading.className = 'text-muted';
  card.appendChild(loading);

  content.appendChild(card);

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

import { api } from '../../api.js';
import { bindViewResources, createPendingScope, createRenderScope } from '../../router.js';
import { captureSessionScope } from '../../auth.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { badge } from '../../components/badges.js';
import { copyTextButton } from '../../components/clipboard.js';
import { el, mount } from '../../components/dom.js';
import { field, input, textarea, toggle } from '../../components/forms.js';
import { clampPage, pageSlice, paginationBar } from '../../components/pagination.js';
import { pageHeader, panel, metaGrid } from '../../components/page.js';
import { emptyState, errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { formatDate, shortId } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

const LIST_FORBIDDEN_FIELDS = ['key', 'key_hash'];
const CREATE_FORBIDDEN_FIELDS = ['key_hash'];
const REVOKE_FORBIDDEN_FIELDS = ['key', 'key_hash'];
const KEY_PAGE_SIZE = 5;

function hasForbiddenField(value, fields) {
  if (!value || typeof value !== 'object') return false;
  if (Array.isArray(value)) return value.some((item) => hasForbiddenField(item, fields));
  return Object.keys(value).some((key) => fields.includes(key)) ||
    Object.values(value).some((item) => hasForbiddenField(item, fields));
}

function dataArray(result) {
  return Array.isArray(result?.data) ? result.data : [];
}

function keyStatus(item) {
  if (item.revoked_at) return badge(t('apiKeys.revoked'), 'danger');
  if (item.enabled) return badge(t('apiKeys.enabled'), 'success');
  return badge(t('apiKeys.disabled'), 'muted');
}

function pagerLabels() {
  return {
    prev: t('common.prev'),
    next: t('common.next'),
    status: t('common.pageStatus'),
  };
}

function renderSecretPanel(fullKey, onDismiss, isActive) {
  const keyBox = textarea({ rows: 2, readOnly: true, value: fullKey, autocomplete: 'off' });
  return panel({ title: t('apiKeys.createdTitle'), subtitle: t('apiKeys.createdWarning'), className: 'secret-box' },
    el('div', { class: 'panel-body form-stack' },
      field(t('apiKeys.fullKey'), keyBox),
      el('div', { class: 'action-row' },
        copyTextButton(fullKey, { variant: 'primary', isActive }),
        button(t('common.close'), { onClick: onDismiss }),
      ),
    ),
  );
}

function createKeyForm(onCreated, isActive) {
  const nameInput = input({ type: 'text', maxLength: 80, autocomplete: 'off', placeholder: t('apiKeys.namePlaceholder') });
  const noteInput = textarea({ rows: 1, maxLength: 240, placeholder: t('apiKeys.notePlaceholder'), class: 'compact-textarea' });
  const submit = button(t('apiKeys.createSubmit'), { variant: 'primary' });

  submit.addEventListener('click', async () => {
    if (submit.disabled || !isActive()) return;
    const pending = createPendingScope(isActive);
    submit.disabled = true;
    submit.textContent = t('apiKeys.creating');
    try {
      const result = await api.post('/admin/gateway-keys', {
        name: nameInput.value.trim(),
        note: noteInput.value.trim(),
      });
      if (!pending.isActive()) return;
      const data = result?.data || {};
      if (hasForbiddenField(data, CREATE_FORBIDDEN_FIELDS)) {
        toast(t('apiKeys.securityError'), 'error');
        return;
      }
      if (!data.key) {
        toast(t('apiKeys.createMissingKey'), 'error');
        return;
      }
      nameInput.value = '';
      noteInput.value = '';
      await onCreated(String(data.key));
    } catch (_) {
      if (pending.isActive()) toast(t('apiKeys.createError'), 'error');
    } finally {
      pending.close();
      submit.disabled = false;
      submit.textContent = t('apiKeys.createSubmit');
    }
  });

  return panel({ title: t('apiKeys.createButton'), subtitle: t('apiKeys.subtitle'), className: 'api-key-form' },
    el('div', { class: 'panel-body form-stack' },
      el('div', { class: 'form-grid' },
        field(t('apiKeys.name'), nameInput),
        field(t('apiKeys.note'), noteInput),
      ),
      el('div', { class: 'action-row' }, submit),
    ),
  );
}

function revokePanel(item, reload) {
  const prefixInput = input({ type: 'text', autocomplete: 'off', placeholder: item.key_prefix || '' });
  const confirm = button(t('apiKeys.revoke'), { variant: 'danger', disabled: true });
  const cancel = button(t('common.cancel'));
  const panelNode = panel({ title: t('apiKeys.revokeTitle'), subtitle: t('apiKeys.revokeWarning') },
    el('div', { class: 'panel-body form-stack' },
      metaGrid([
        { label: t('apiKeys.keyPrefix'), value: item.key_prefix || '-' },
        { label: t('apiKeys.name'), value: item.name || '-' },
        { label: t('apiKeys.created'), value: formatDate(item.created_at) },
      ]),
      field(t('apiKeys.revokeConfirmLabel'), prefixInput, { help: t('apiKeys.revokeConfirmHelp') }),
      el('div', { class: 'action-row' }, cancel, confirm),
    ),
  );

  prefixInput.addEventListener('input', () => {
    confirm.disabled = prefixInput.value !== item.key_prefix;
  });
  cancel.addEventListener('click', () => panelNode.remove());
  confirm.addEventListener('click', async () => {
    if (prefixInput.value !== item.key_prefix) {
      toast(t('apiKeys.revokePrefixMismatch'), 'error');
      return;
    }
    confirm.disabled = true;
    confirm.textContent = t('apiKeys.revoking');
    try {
      const result = await api.delete(`/admin/gateway-keys/${encodeURIComponent(item.id)}`);
      if (hasForbiddenField(result, REVOKE_FORBIDDEN_FIELDS)) {
        toast(t('apiKeys.securityError'), 'error');
        return;
      }
      panelNode.remove();
      await reload();
    } catch (_) {
      toast(t('apiKeys.revokeError'), 'error');
    } finally {
      confirm.disabled = false;
      confirm.textContent = t('apiKeys.revoke');
    }
  });

  return panelNode;
}

function keyCard(item, revokeSlot, reload) {
  const canRevoke = !item.revoked_at && item.key_prefix;
  const meta = [
    { label: t('apiKeys.created'), value: formatDate(item.created_at) },
    { label: t('apiKeys.lastUsed'), value: formatDate(item.last_used_at) },
  ];
  if (item.revoked_at) meta.push({ label: t('apiKeys.revokedAt'), value: formatDate(item.revoked_at) });
  if (item.note) meta.push({ label: t('apiKeys.note'), value: safeText(item.note, 80) });

  return el('article', { class: 'key-card key-compact-card' },
    el('div', { class: 'key-card-header' },
      el('div', {},
        el('p', { class: 'card-title' }, safeText(item.name || item.key_prefix || shortId(item.id), 80)),
        el('p', { class: 'card-subtitle' }, `${t('apiKeys.keyPrefix')}: ${item.key_prefix || '-'}`),
      ),
      keyStatus(item),
    ),
    metaGrid(meta),
    canRevoke ? el('div', { class: 'action-row' },
      button(t('apiKeys.revoke'), {
        size: 'sm',
        variant: 'danger',
        onClick: () => {
          revokeSlot.textContent = '';
          revokeSlot.appendChild(revokePanel(item, reload));
        },
      }),
    ) : null,
  );
}

export async function render() {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  const session = captureSessionScope();
  let showRevoked = false;
  let keysCache = [];
  let keyPage = 1;
  let oneTimeSecret = null;

  function clearSecret() {
    oneTimeSecret?.remove();
    oneTimeSecret = null;
  }
  const resources = bindViewResources(() => scope.isActive() && session(), {
    dispose: clearSecret, suspend: clearSecret, resume: () => {},
  });
  const isActive = resources.isActive;
  const visibleKeys = () => keysCache.filter((item) => showRevoked || !item.revoked_at);

  function renderKeys() {
    const revokeSlot = el('div');
    const visible = visibleKeys();
    const paged = pageSlice(visible, keyPage, KEY_PAGE_SIZE);
    keyPage = paged.current;
    const activeCount = keysCache.filter((item) => !item.revoked_at).length;
    const revokedCount = keysCache.length - activeCount;
    const createColumn = el('div', { class: 'api-side' },
      createKeyForm(async (key) => {
        clearSecret();
        oneTimeSecret = renderSecretPanel(key, clearSecret, isActive);
        renderKeys();
        await reload();
      }, isActive),
      oneTimeSecret, revokeSlot,
    );

    mount(content,
      pageHeader({
        kicker: t('apiKeys.kicker'),
        title: t('apiKeys.title'),
        subtitle: t('apiKeys.subtitle'),
        actions: [button(t('common.refresh'), { onClick: reload })],
      }),
      el('div', { class: 'api-layout' },
        createColumn,
        panel({},
          el('div', { class: 'keys-toolbar' },
            el('div', { class: 'action-row' },
              badge(`${activeCount} ${t('apiKeys.enabled')}`, 'success'),
              revokedCount ? badge(`${revokedCount} ${t('apiKeys.revoked')}`, 'muted') : null,
            ),
            toggle(t('apiKeys.showRevoked'), {
              checked: showRevoked,
              onchange: (event) => {
                showRevoked = event.target.checked;
                keyPage = 1;
                renderKeys();
              },
            }),
          ),
          el('div', { class: 'keys-content' },
            visible.length ? el('div', { class: 'key-list bounded-list' }, paged.items.map((item) => keyCard(item, revokeSlot, reload))) :
              emptyState(t('apiKeys.empty')),
          ),
          paginationBar({
            page: keyPage,
            total: visible.length,
            pageSize: KEY_PAGE_SIZE,
            labels: pagerLabels(),
            onPage: (page) => {
              keyPage = page;
              renderKeys();
            },
          }),
        ),
      ),
    );
  }

  async function reload() {
    if (!isActive()) return;
    const current = scope.nextRequest();
    if (!oneTimeSecret) mount(content, loadingState(t('apiKeys.loading')));
    try {
      const result = await api.get('/admin/gateway-keys');
      if (!current() || !isActive()) return;
      if (hasForbiddenField(result, LIST_FORBIDDEN_FIELDS)) {
        if (oneTimeSecret) {
          toast(t('apiKeys.securityError'), 'error');
          return;
        }
        mount(content,
          pageHeader({ kicker: t('apiKeys.kicker'), title: t('apiKeys.title'), subtitle: t('apiKeys.subtitle') }),
          errorState(t('apiKeys.securityError')),
        );
        return;
      }
      keysCache = dataArray(result);
      keyPage = clampPage(keyPage, visibleKeys().length, KEY_PAGE_SIZE);
      renderKeys();
    } catch (_) {
      if (!current() || !isActive()) return;
      if (oneTimeSecret) {
        toast(t('apiKeys.error'), 'error');
        return;
      }
      mount(content,
        pageHeader({ kicker: t('apiKeys.kicker'), title: t('apiKeys.title'), subtitle: t('apiKeys.subtitle') }),
        errorState(t('apiKeys.error')),
      );
    }
  }

  await reload();
}

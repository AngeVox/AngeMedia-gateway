(() => {
  const { escapeHtml, escapeAttr } = window.AngeUtils;

  function currentValue(settings, key) {
    const value = settings && Object.prototype.hasOwnProperty.call(settings, key) ? settings[key] : '';
    return value == null ? '' : String(value);
  }

  function isChecked(value) {
    return String(value).toLowerCase() === 'true';
  }

  function fieldControl(field, settings) {
    const value = currentValue(settings, field.key);
    const secret = !!field.secret;
    if (field.kind === 'bool') {
      return `<label class="toggle-line">
        <input data-config-key="${escapeAttr(field.key)}" data-kind="bool" type="checkbox" ${isChecked(value) ? 'checked' : ''} />
        <span>${isChecked(value) ? '已开启' : '已关闭'}</span>
      </label>`;
    }

    const type = secret ? 'password' : (field.kind === 'int' || field.kind === 'float' ? 'number' : 'text');
    const placeholder = secret && value ? value : (field.placeholder || '');
    const visibleValue = secret ? '' : value;
    const step = field.kind === 'float' ? '0.01' : '1';
    const numericAttrs = field.kind === 'int' || field.kind === 'float' ? ` inputmode="decimal" step="${step}"` : '';
    return `<input
      data-config-key="${escapeAttr(field.key)}"
      data-kind="${escapeAttr(field.kind || 'text')}"
      data-secret="${secret ? 'true' : 'false'}"
      data-original="${escapeAttr(visibleValue)}"
      type="${type}"
      placeholder="${escapeAttr(placeholder)}"
      value="${escapeAttr(visibleValue)}"
      ${secret ? 'autocomplete="new-password"' : ''}
      ${numericAttrs}
    />`;
  }

  function renderField(field, settings) {
    return `<div class="config-field">
      <span class="config-title">${escapeHtml(field.label || field.key)}</span>
      <span class="config-desc">${escapeHtml(field.description || '')}</span>
      ${fieldControl(field, settings)}
      <code class="config-key">${escapeHtml(field.key)}</code>
    </div>`;
  }

  function renderGroups(container, groups, settings) {
    if (!container) return;
    container.innerHTML = groups.map(group => `<section class="config-group" data-config-group="${escapeAttr(group.id)}">
      <header class="config-group-head">
        <div>
          <p class="eyebrow">${escapeHtml(group.id)}</p>
          <h3>${escapeHtml(group.title)}</h3>
          <p>${escapeHtml(group.description || '')}</p>
        </div>
      </header>
      <div class="config-fields">
        ${group.fields.map(field => renderField(field, settings)).join('')}
      </div>
    </section>`).join('');

    container.querySelectorAll('.toggle-line input[type="checkbox"]').forEach(input => {
      input.addEventListener('change', () => {
        const text = input.closest('.toggle-line')?.querySelector('span');
        if (text) text.textContent = input.checked ? '已开启' : '已关闭';
      });
    });
  }

  function collectSettings(container) {
    const settings = {};
    container.querySelectorAll('[data-config-key]').forEach(input => {
      const key = input.dataset.configKey;
      if (input.type === 'checkbox') {
        settings[key] = String(input.checked);
        return;
      }
      const value = input.value.trim();
      if (input.dataset.secret === 'true') {
        if (value) settings[key] = value;
        return;
      }
      if (value !== (input.dataset.original || '')) {
        settings[key] = value;
      }
    });
    return settings;
  }

  function findGroup(metadata, id) {
    return (metadata?.groups || []).find(group => group.id === id);
  }

  window.AngeAdminConfig = { collectSettings, findGroup, renderGroups };
})();

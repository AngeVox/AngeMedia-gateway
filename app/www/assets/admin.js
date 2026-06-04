const $ = (id) => document.getElementById(id);
const { escapeHtml, escapeAttr, humanSize, displayGatewayUrl, fileNameFromUrl, bindDownloadButtons } = window.AngeUtils;
const { fetchJson, postJson, deleteJson } = window.AngeAdminApi;
const { collectSettings, findGroup, renderGroups } = window.AngeAdminConfig;

const state = {
  theme: document.documentElement.dataset.theme || 'light',
  config: null,
  metadata: null,
  health: null,
  providerStatus: null,
  providerTemplates: [],
  assistantModels: [],
  debugProviders: false,
  debugAssistant: false,
  authenticated: false
};

function setHTML(id, html) {
  const node = $(id);
  if (node) node.innerHTML = html;
}

function setText(id, text) {
  const node = $(id);
  if (node) node.textContent = text;
}

function toast(message) {
  const stack = $('toast-stack');
  if (!stack) return;
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

bindDownloadButtons(toast);

function fileActions(url, fallbackName = 'angemedia-media') {
  const displayUrl = displayGatewayUrl(url || '');
  if (!displayUrl) return '';
  const filename = fileNameFromUrl(displayUrl, fallbackName);
  return `<div class="button-row inline">
    <button type="button" class="ghost-button small" data-download-url="${escapeAttr(displayUrl)}" data-download-filename="${escapeAttr(filename)}">下载到本地</button>
    <a class="ghost-button small" href="${escapeAttr(displayUrl)}" target="_blank" rel="noopener">打开</a>
  </div>`;
}

function humanDuration(ms) {
  const n = Number(ms || 0);
  if (!n) return '-';
  if (n < 1000) return `${n}ms`;
  if (n < 60000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}s`;
  return `${Math.floor(n / 60000)}m ${Math.round((n % 60000) / 1000)}s`;
}

function mediaPreview(url, mediaType = '') {
  const displayUrl = displayGatewayUrl(url || '');
  if (!displayUrl) return '<div class="media-thumb empty">无预览</div>';
  const isVideo = mediaType === 'video' || /\.(mp4|webm|mov)$/i.test(displayUrl);
  if (isVideo) return `<video class="media-thumb" src="${escapeAttr(displayUrl)}" muted playsinline preload="metadata"></video>`;
  if (/\.(png|jpg|jpeg|webp|gif|avif)(\?|#|$)/i.test(displayUrl)) {
    return `<img class="media-thumb" src="${escapeAttr(displayUrl)}" alt="媒体预览" loading="lazy" />`;
  }
  return '<div class="media-thumb empty">文件</div>';
}

function metaStrip(parts) {
  const clean = parts.filter(part => part && part[1] !== undefined && part[1] !== '');
  if (!clean.length) return '';
  return `<div class="media-meta-strip compact">${clean.map(([label, value]) => `<span>${escapeHtml(label)}：${escapeHtml(value)}</span>`).join('')}</div>`;
}

function setTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('angemedia.theme.v1', theme);
  setText('theme-btn', theme === 'dark' ? '☀' : '☾');
}

function showLogin(show) {
  state.authenticated = !show;
  const login = $('login-screen');
  const shell = $('admin-shell');
  if (login) {
    login.hidden = !show;
    login.setAttribute('aria-hidden', show ? 'false' : 'true');
  }
  if (shell) {
    shell.hidden = show;
    shell.setAttribute('aria-hidden', show ? 'true' : 'false');
  }
  if (show) setTimeout(() => $('login-password')?.focus(), 30);
}

window.addEventListener('angemedia:admin-unauthorized', () => showLogin(true));

async function checkLogin() {
  showLogin(true);
  try {
    const session = await fetchJson('/v1/admin/session');
    if (!session.authenticated) {
      showLogin(true);
      return;
    }
    showLogin(false);
    await refreshAll();
  } catch (err) {
    showLogin(true);
    if (err?.status && err.status !== 401) toast(err.message || '无法检查登录状态');
  }
}

$('login-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await postJson('/v1/admin/login', {
      username: $('login-username').value.trim(),
      password: $('login-password').value
    });
    $('login-password').value = '';
    toast('登录成功');
    showLogin(false);
    await refreshAll();
  } catch (err) {
    toast(err.message || '登录失败');
  }
});

$('logout-btn')?.addEventListener('click', async () => {
  try {
    await postJson('/v1/admin/logout', {});
  } catch (err) {
    if (err?.status !== 401) toast(err.message || '退出失败');
  } finally {
    showLogin(true);
  }
});

function closePasswordDialog() {
  const dialog = $('password-dialog');
  if ($('current-password')) $('current-password').value = '';
  if ($('new-password')) $('new-password').value = '';
  if (dialog?.open) dialog.close();
}

$('change-password-btn')?.addEventListener('click', () => {
  const dialog = $('password-dialog');
  if (!dialog) return;
  if (!dialog.open) dialog.showModal();
  setTimeout(() => $('current-password')?.focus(), 30);
});

$('password-close-btn')?.addEventListener('click', () => closePasswordDialog());

$('password-dialog')?.addEventListener('cancel', () => {
  if ($('current-password')) $('current-password').value = '';
  if ($('new-password')) $('new-password').value = '';
});

$('password-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await postJson('/v1/admin/password', {
      current_password: $('current-password').value,
      new_password: $('new-password').value
    });
    closePasswordDialog();
    toast('密码已修改，请重新登录');
    try {
      await postJson('/v1/admin/logout', {});
    } catch {}
    showLogin(true);
  } catch (err) {
    toast(err.message || '修改密码失败');
  }
});

function setActiveTab(tab) {
  document.querySelectorAll('.admin-nav-btn[data-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.admin-panel[data-panel]').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.panel === tab);
  });
}

document.querySelectorAll('.admin-nav-btn[data-tab]').forEach((btn) => {
  btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
});

$('refresh-btn')?.addEventListener('click', async () => {
  if (!state.authenticated) {
    await checkLogin();
    return;
  }
  await refreshAll();
  toast('已刷新');
});

$('theme-btn')?.addEventListener('click', () => {
  setTheme(state.theme === 'dark' ? 'light' : 'dark');
});

setTheme(state.theme);
setActiveTab(document.querySelector('.admin-nav-btn.active')?.dataset.tab || 'overview');

async function refreshAll() {
  if (!state.authenticated) return;
  await Promise.allSettled([
    loadHealth(),
    loadProviderTemplates(),
    loadConfigCenter(),
    loadProviderStatus(),
    loadHistory(),
    loadTasks(),
    loadUploads(),
    loadGeneratedFiles()
  ]);
}

async function loadProviderTemplates() {
  try {
    const data = await fetchJson('/v1/admin/provider-templates');
    state.providerTemplates = data.data || [];
    renderProviderTemplates();
  } catch (err) {
    if (err?.status !== 401) console.warn(err);
  }
}

async function loadHealth() {
  try {
    const data = await fetchJson('/health');
    state.health = data;
    const pill = $('health-pill');
    if (pill) {
      pill.className = 'status-pill ok';
      pill.querySelector('b').textContent = '网关在线';
    }
    renderOverview();
    renderModels();
  } catch (err) {
    const pill = $('health-pill');
    if (pill) {
      pill.className = 'status-pill bad';
      pill.querySelector('b').textContent = '连接异常';
    }
    toast(err.message || '无法连接网关');
  }
}

async function loadConfigCenter() {
  try {
    if (!state.metadata) state.metadata = await fetchJson('/v1/admin/config-metadata');
    const data = await fetchJson('/v1/admin/config');
    state.config = data;
    state.providerTemplates = data.provider_templates || state.providerTemplates || [];
    renderConfig(data.settings || {});
    renderProviderTemplates();
    renderCustomProviders();
    setText('assistant-json', JSON.stringify(data.assistant || {}, null, 2));
  } catch (err) {
    if (err?.status !== 401) toast(err.message || '无法读取配置中心');
  }
}

async function loadProviderStatus() {
  try {
    const data = await fetchJson('/v1/admin/provider-status');
    state.providerStatus = data;
    renderProviderStatus(data);
    renderModels();
    const debug = $('provider-debug-json');
    if (debug) debug.textContent = JSON.stringify(data, null, 2);
    renderCustomProviders();
  } catch (err) {
    if (err?.status !== 401) console.warn(err);
  }
}

function readyPill(ready, textReady = '就绪', textBad = '未配置') {
  return `<span class="mini-status ${ready ? 'ok' : 'bad'}"><i></i>${ready ? textReady : textBad}</span>`;
}

function statusLabel(status) {
  const text = String(status || '').toLowerCase();
  return ({
    completed: '完成',
    succeeded: '完成',
    success: '完成',
    done: '完成',
    queued: '排队中',
    submitted: '已提交',
    pending: '等待中',
    processing: '处理中',
    running: '处理中',
    failed: '失败',
    error: '失败',
    cancelled: '已取消'
  })[text] || (status || '记录');
}

function statusPill(status) {
  const text = String(status || '').toLowerCase();
  const ok = ['completed', 'succeeded', 'success', 'done'].includes(text);
  const bad = ['failed', 'error', 'cancelled'].includes(text);
  return `<span class="mini-status ${ok ? 'ok' : (bad ? 'bad' : '')}"><i></i>${escapeHtml(statusLabel(status))}</span>`;
}

function renderOverview() {
  const h = state.health || {};
  setHTML('admin-metrics', [
    ['版本', h.version || '-'],
    ['访问保护', h.auth_enabled ? '已开启' : '未开启'],
    ['本地存储', h.storage_ready ? '就绪' : '不可用'],
    ['小助手', h.assistant?.enabled ? (h.assistant?.configured ? '可用' : '待配置') : '关闭'],
    ['模型别名', Array.isArray(h.models) ? h.models.length : '-']
  ].map(([k, v]) => `<article class="metric-card"><span>${k}</span><strong>${escapeHtml(v)}</strong></article>`).join(''));
}

function renderProviderStatus(data) {
  const items = data?.data || [...(data?.built_in || []), ...(data?.custom || [])];
  const table = renderProviderTable(items);
  setHTML('provider-status-grid', table);
  setHTML('custom-provider-grid', table);
}

function providerKindLabel(item) {
  if (item.source === 'built_in' || item.type === 'built_in') return '内置';
  return '自定义';
}

function providerHealthLabel(item) {
  if (!item.enabled) return readyPill(false, '启用', '已移出');
  if (item.ready) return readyPill(true, '就绪', '未就绪');
  return readyPill(false, '就绪', item.configured ? '未启用' : '待配置');
}

function providerActions(item) {
  const id = escapeAttr(item.id);
  const toggleText = item.enabled ? (item.source === 'built_in' ? '移出' : '停用') : '恢复';
  const edit = item.source === 'custom' ? `<button type="button" class="ghost-button small" data-edit-provider="${id}">编辑</button>` : '';
  const remove = item.source === 'custom' ? `<button type="button" class="ghost-button small danger" data-delete-provider="${id}">删除</button>` : '';
  return `<div class="table-actions">
    <button type="button" class="ghost-button small" data-test-provider="${id}">测试</button>
    <button type="button" class="ghost-button small" data-toggle-provider="${id}" data-enabled="${escapeAttr(String(!item.enabled))}">${toggleText}</button>
    ${edit}${remove}
  </div>`;
}

function renderProviderTable(items) {
  if (!items.length) return '<div class="empty-state"><p>暂无渠道状态。</p></div>';
  const rows = items.map(item => {
    const aliases = Array.isArray(item.aliases) ? item.aliases.join(', ') : '';
    const testStatus = item.last_test_status ? `${item.last_test_status}${item.last_response_ms ? ` · ${item.last_response_ms}ms` : ''}` : '-';
    const sortCell = item.source === 'custom'
      ? `<input class="table-number" type="number" data-sort-provider="${escapeAttr(item.id)}" value="${escapeAttr(item.sort_order ?? 100)}" />`
      : `<span class="muted">${escapeHtml(item.sort_order ?? '-')}</span>`;
    return `<tr>
      <td><b>${escapeHtml(item.name || item.id)}</b><small>${escapeHtml(item.description || aliases || '')}</small></td>
      <td>${escapeHtml(providerKindLabel(item))}</td>
      <td>${providerHealthLabel(item)}</td>
      <td><code>${escapeHtml(item.default_model || '-')}</code><small>${escapeHtml(aliases)}</small></td>
      <td>${sortCell}</td>
      <td>${escapeHtml(testStatus)}</td>
      <td>${providerActions(item)}</td>
    </tr>`;
  }).join('');
  return `<div class="data-table-wrap"><table class="admin-table provider-table">
    <thead><tr><th>渠道</th><th>类型</th><th>状态</th><th>模型/别名</th><th>排序</th><th>测试</th><th>操作</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderConfig(settings) {
  const metadata = state.metadata;
  const providerGroups = ['gateway', 'built_in', 'agnes', 'openai_image']
    .map(id => findGroup(metadata, id))
    .filter(Boolean);
  const assistantGroup = findGroup(metadata, 'assistant');
  renderGroups($('provider-settings'), providerGroups, settings);
  renderGroups($('assistant-settings'), assistantGroup ? [assistantGroup] : [], settings);
}

function renderProviderTemplates() {
  const select = $('provider-template-select');
  if (!select) return;
  const templates = state.providerTemplates || [];
  select.innerHTML = templates.map(tpl => `<option value="${escapeAttr(tpl.id)}">${escapeHtml(tpl.name)}</option>`).join('') || '<option value="">暂无模板</option>';
}

async function saveSettings(containerId) {
  const container = $(containerId);
  if (!container) return;
  const settings = collectSettings(container);
  if (!Object.keys(settings).length) {
    toast('没有需要保存的改动');
    return;
  }
  await postJson('/v1/admin/config', { settings });
  toast('配置已保存');
  await refreshAll();
}

$('generate-gateway-key')?.addEventListener('click', async () => {
  try {
    if (!confirm('生成新的网关密钥会覆盖当前网关访问密钥。继续吗？')) return;
    const data = await postJson('/v1/admin/gateway-key', { save: true });
    toast(`已生成并保存网关密钥：${data.key_preview || '已保存'}`);
    await refreshAll();
  } catch (err) {
    toast(err.message || '生成网关密钥失败');
  }
});

$('save-provider-settings')?.addEventListener('click', () => saveSettings('provider-settings').catch(err => toast(err.message)));
$('save-assistant-settings')?.addEventListener('click', () => saveSettings('assistant-settings').catch(err => toast(err.message)));

function customStatusById(id) {
  return (state.providerStatus?.custom || []).find(item => item.id === id);
}

function renderCustomProviders() {
  if (state.providerStatus) {
    renderProviderStatus(state.providerStatus);
    return;
  }
  const custom = (state.config?.custom_providers || []).map(item => ({
    ...item,
    source: 'custom',
    category: '图片',
    aliases: [`custom:${item.id}`],
    ready: !!item.enabled,
    configured: !!(item.base_url && item.default_model)
  }));
  setHTML('custom-provider-grid', renderProviderTable(custom));
}

$('save-custom-provider')?.addEventListener('click', async () => {
  try {
    const payload = {
      id: $('custom-provider-id').value.trim(),
      name: $('custom-provider-name').value.trim(),
      provider_type: 'openai_image',
      base_url: $('custom-provider-base-url').value.trim(),
      api_key: $('custom-provider-api-key').value.trim(),
      default_model: $('custom-provider-model').value.trim(),
      sort_order: $('custom-provider-sort').value.trim() || '100',
      status_url: $('custom-provider-status-url').value.trim(),
      quota_url: $('custom-provider-quota-url').value.trim(),
      enabled: String($('custom-provider-enabled').checked)
    };
    await postJson('/v1/admin/providers', payload);
    resetCustomProviderForm();
    toast('渠道已保存，可继续新增下一条');
    refreshAll();
  } catch (err) {
    toast(err.message || '保存失败');
  }
});

function fillCustomProviderForm(provider) {
  $('custom-provider-id').value = provider.id || '';
  $('custom-provider-name').value = provider.name || '';
  $('custom-provider-base-url').value = provider.base_url || '';
  $('custom-provider-api-key').value = '';
  $('custom-provider-model').value = provider.default_model || '';
  $('custom-provider-sort').value = provider.sort_order ?? 100;
  $('custom-provider-status-url').value = provider.status_url || '';
  $('custom-provider-quota-url').value = provider.quota_url || '';
  $('custom-provider-enabled').checked = provider.enabled !== false;
}

function resetCustomProviderForm() {
  ['custom-provider-id', 'custom-provider-name', 'custom-provider-base-url', 'custom-provider-api-key', 'custom-provider-model', 'custom-provider-status-url', 'custom-provider-quota-url'].forEach(id => {
    if ($(id)) $(id).value = '';
  });
  if ($('custom-provider-sort')) $('custom-provider-sort').value = '100';
  if ($('custom-provider-enabled')) $('custom-provider-enabled').checked = true;
}

$('reset-custom-provider-form')?.addEventListener('click', () => {
  resetCustomProviderForm();
  toast('已切换为新增空白渠道');
});

$('apply-provider-template')?.addEventListener('click', () => {
  const template = state.providerTemplates.find(item => item.id === $('provider-template-select')?.value);
  if (!template) return toast('请选择渠道模板');
  fillCustomProviderForm({ ...(template.payload || {}), enabled: true });
  $('custom-provider-id').value = '';
  toast(`已套用模板：${template.name}`);
});

document.addEventListener('click', async (event) => {
  const testBtn = event.target.closest('[data-test-provider]');
  if (testBtn) {
    event.preventDefault();
    try {
      const data = await postJson(`/v1/admin/providers/${encodeURIComponent(testBtn.dataset.testProvider)}/test`, {});
      toast(data.ok ? `测试成功${data.elapsed_ms ? `：${data.elapsed_ms}ms` : ''}` : (data.message || '测试未通过'));
      await refreshAll();
    } catch (err) {
      toast(err.message || '测试失败');
      await loadProviderStatus();
    }
    return;
  }

  const toggleBtn = event.target.closest('[data-toggle-provider]');
  if (toggleBtn) {
    event.preventDefault();
    try {
      await postJson(`/v1/admin/providers/${encodeURIComponent(toggleBtn.dataset.toggleProvider)}/enabled`, {
        enabled: toggleBtn.dataset.enabled === 'true'
      });
      toast(toggleBtn.dataset.enabled === 'true' ? '渠道已恢复' : '渠道已移出/停用');
      await refreshAll();
    } catch (err) {
      toast(err.message || '操作失败');
    }
    return;
  }

  const editBtn = event.target.closest('[data-edit-provider]');
  if (editBtn) {
    event.preventDefault();
    const provider = (state.config?.custom_providers || []).find(x => x.id === editBtn.dataset.editProvider);
    if (!provider) return;
    fillCustomProviderForm(provider);
    toast('已填入表单，接口密钥留空表示保持原值');
    return;
  }

  const deleteBtn = event.target.closest('[data-delete-provider]');
  if (deleteBtn) {
    event.preventDefault();
    if (!confirm('确定删除这个自定义渠道？删除后模型 custom:ID 会从列表移除。')) return;
    await deleteJson(`/v1/admin/providers/${encodeURIComponent(deleteBtn.dataset.deleteProvider)}`);
    toast('自定义渠道已删除');
    await refreshAll();
  }
});

document.addEventListener('change', async (event) => {
  const input = event.target.closest('[data-sort-provider]');
  if (!input) return;
  try {
    await postJson(`/v1/admin/providers/${encodeURIComponent(input.dataset.sortProvider)}/sort`, {
      sort_order: input.value
    });
    toast('渠道排序已更新');
    await refreshAll();
  } catch (err) {
    toast(err.message || '排序更新失败');
  }
});

$('toggle-provider-debug')?.addEventListener('click', () => {
  state.debugProviders = !state.debugProviders;
  const box = $('provider-debug-json');
  if (box) box.hidden = !state.debugProviders;
});

$('toggle-assistant-debug')?.addEventListener('click', () => {
  state.debugAssistant = !state.debugAssistant;
  const box = $('assistant-json');
  if (box) box.hidden = !state.debugAssistant;
});

function renderAssistantModelSelect(models) {
  const select = $('assistant-model-select');
  if (!select) return;
  select.innerHTML = models.length
    ? models.map(id => `<option value="${escapeAttr(id)}">${escapeHtml(id)}</option>`).join('')
    : '<option value="">未拉取到模型</option>';
}

$('fetch-assistant-models')?.addEventListener('click', async () => {
  try {
    setText('assistant-model-result', '正在拉取模型...');
    const data = await fetchJson('/v1/admin/assistant/models');
    state.assistantModels = data.data || [];
    renderAssistantModelSelect(state.assistantModels);
    setText('assistant-model-result', `已拉取 ${state.assistantModels.length} 个模型 · ${data.elapsed_ms || 0}ms`);
  } catch (err) {
    setText('assistant-model-result', err.message || '模型拉取失败');
    toast(err.message || '模型拉取失败');
  }
});

$('apply-assistant-model')?.addEventListener('click', () => {
  const model = $('assistant-model-select')?.value || '';
  if (!model) return toast('请先选择模型');
  const input = document.querySelector('[data-config-key="ANGE_LLM_MODEL"]');
  if (!input) return toast('未找到小助手模型配置项');
  input.value = model;
  toast(`已填入模型：${model}，记得保存小助手配置`);
});

$('test-assistant-connection')?.addEventListener('click', async () => {
  try {
    const model = $('assistant-model-select')?.value || document.querySelector('[data-config-key="ANGE_LLM_MODEL"]')?.value || '';
    setText('assistant-model-result', '正在测试连接...');
    const data = await postJson('/v1/admin/assistant/test', { model });
    setText('assistant-model-result', `连接正常 · ${data.model} · ${data.elapsed_ms}ms`);
    toast(data.preview || '小助手连接正常');
  } catch (err) {
    setText('assistant-model-result', err.message || '连接测试失败');
    toast(err.message || '连接测试失败');
  }
});

function modelReady(modelId) {
  const p = state.providerStatus;
  if (!p) return null;
  const built = Object.fromEntries((p.built_in || []).map(x => [x.id, x.ready]));
  if (modelId.startsWith('custom:')) {
    const id = modelId.split(':')[1];
    const item = (p.custom || []).find(x => x.id === id);
    return !!item?.ready;
  }
  if (['kolors'].includes(modelId)) return !!built.siliconflow;
  if (['qwen', 'qwen-image', 'flux', 'flux-krea', 'z-image', 'z-turbo', 'z-image-turbo'].includes(modelId)) return !!built.modelscope;
  if (['openai-image', 'gpt-image-2'].includes(modelId)) return !!built.openai_image;
  if (['agnes-image', 'agnes-2.1', 'agnes-2.0'].includes(modelId)) return !!built.agnes_image;
  if (['pollinations'].includes(modelId)) return true;
  return null;
}

async function renderModels() {
  try {
    const data = await fetchJson('/v1/models');
    const models = data.data || [];
    setHTML('model-list', models.map(item => {
      const ready = modelReady(item.id);
      return `<article class="model-card readable-card">
        <div class="card-row"><h3>${escapeHtml(item.id)}</h3>${readyPill(ready !== false, ready === null ? '未知' : '就绪', '未就绪')}</div>
        <p>${escapeHtml(item.display_name || item.owned_by || '模型别名')}</p>
        ${item.default_model ? `<p>实际模型：<code>${escapeHtml(item.default_model)}</code></p>` : ''}
      </article>`;
    }).join('') || '<div class="empty-state"><p>暂无模型。</p></div>');
  } catch (err) {
    setHTML('model-list', `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`);
  }
}

async function loadHistory() {
  try {
    const data = await fetchJson('/v1/history?limit=60');
    setHTML('history-list', (data.data || []).map(row => `<article class="media-card">
      ${mediaPreview(row.result_url, row.media_type)}
      <div class="media-card-body">
        <div class="card-row"><h3>${escapeHtml(row.media_type)} · ${escapeHtml(statusLabel(row.status))}</h3>${statusPill(row.status)}</div>
        <p>${escapeHtml(new Date(row.created_at).toLocaleString())}</p>
        <p class="history-prompt" title="${escapeAttr(row.prompt)}">${escapeHtml(row.prompt)}</p>
        ${metaStrip([['渠道', row.provider || '-'], ['模型', row.model || row.request_model || '-'], ['耗时', humanDuration(row.duration_ms)]])}
        ${fileActions(row.result_url, `angemedia-${row.media_type || 'history'}`)}
      </div>
    </article>`).join('') || '<div class="empty-state"><p>暂无历史。</p></div>');
  } catch (err) {
    setHTML('history-list', `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`);
  }
}

async function loadTasks() {
  try {
    const data = await fetchJson('/v1/video-tasks?limit=60');
    setHTML('video-task-list', (data.data || []).map(row => `<article class="media-card wide">
      ${mediaPreview(row.video_url, 'video')}
      <div class="media-card-body">
        <div class="card-row"><h3 title="${escapeAttr(row.task_id)}">${escapeHtml(row.task_id)}</h3>${statusPill(row.status)}</div>
        <p>${escapeHtml(new Date(row.updated_at).toLocaleString())}</p>
        <p class="history-prompt" title="${escapeAttr(row.prompt || '')}">${escapeHtml(row.prompt || '')}</p>
        ${metaStrip([['渠道', row.provider || 'agnes_video'], ['模型', row.model || '-'], ['耗时', humanDuration(row.duration_ms)]])}
        ${fileActions(row.video_url, 'angemedia-video.mp4')}
      </div>
    </article>`).join('') || '<div class="empty-state"><p>暂无视频任务。</p></div>');
  } catch (err) {
    setHTML('video-task-list', `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`);
  }
}

async function loadUploads() {
  try {
    const data = await fetchJson('/v1/uploads?limit=100');
    setHTML('upload-list', (data.data || []).map(row => `<article class="media-card">
      ${mediaPreview(row.url, row.content_type?.startsWith('video') ? 'video' : 'image')}
      <div class="media-card-body">
        <h3 class="truncate" title="${escapeAttr(row.original_filename || row.filename)}">${escapeHtml(row.original_filename || row.filename)}</h3>
        <p>${escapeHtml(row.role || 'reference')} · ${escapeHtml(new Date(row.created_at).toLocaleString())}</p>
        ${fileActions(row.url, row.original_filename || row.filename || 'angemedia-upload')}
        <div class="button-row inline"><button type="button" class="ghost-button small danger" data-delete-upload="${escapeAttr(row.id)}">删除</button></div>
      </div>
    </article>`).join('') || '<div class="empty-state"><p>暂无上传文件。</p></div>');
    document.querySelectorAll('[data-delete-upload]').forEach(btn => btn.addEventListener('click', async () => {
      if (!confirm('确定删除上传文件？')) return;
      await deleteJson(`/v1/uploads/${encodeURIComponent(btn.dataset.deleteUpload)}`);
      toast('上传文件已删除');
      loadUploads();
    }));
  } catch (err) {
    setHTML('upload-list', `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`);
  }
}

async function loadGeneratedFiles() {
  try {
    const data = await fetchJson('/v1/generated-files?limit=100');
    const files = data.data || [];
    setHTML('file-metrics', [
      ['生成文件', files.length],
      ['总大小', humanSize(files.reduce((s, x) => s + Number(x.size || 0), 0))]
    ].map(([k, v]) => `<article class="metric-card"><span>${k}</span><strong>${escapeHtml(v)}</strong></article>`).join(''));
    setHTML('generated-file-list', files.map(row => `<article class="media-card">
      ${mediaPreview(row.url, row.media_type)}
      <div class="media-card-body">
        <h3 class="truncate" title="${escapeAttr(row.filename)}">${escapeHtml(row.filename)}</h3>
        <p>${escapeHtml(humanSize(row.size))} · ${escapeHtml(new Date(row.mtime * 1000).toLocaleString())}</p>
        <p class="history-prompt" title="${escapeAttr(row.prompt || '')}">${escapeHtml(row.prompt || '未关联历史记录')}</p>
        ${metaStrip([['渠道', row.provider || '-'], ['模型', row.model || row.request_model || '-'], ['耗时', humanDuration(row.duration_ms)]])}
        ${fileActions(row.url, row.filename || 'angemedia-generated')}
        <div class="button-row inline"><button type="button" class="ghost-button small danger" data-delete-generated="${escapeAttr(row.filename)}">删除</button></div>
      </div>
    </article>`).join('') || '<div class="empty-state"><p>暂无生成文件。</p></div>');
    document.querySelectorAll('[data-delete-generated]').forEach(btn => btn.addEventListener('click', async () => {
      if (!confirm('确定删除生成文件？')) return;
      await deleteJson(`/v1/generated-files/${encodeURIComponent(btn.dataset.deleteGenerated)}`);
      toast('生成文件已删除');
      loadGeneratedFiles();
    }));
  } catch (err) {
    setHTML('generated-file-list', `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`);
  }
}

$('clear-history')?.addEventListener('click', async () => {
  if (!confirm('确定清空生成历史？不会删除本地文件。')) return;
  await deleteJson('/v1/history');
  await loadHistory();
  toast('历史已清空');
});

checkLogin();

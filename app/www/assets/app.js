const $ = (id) => document.getElementById(id);
const { escapeHtml, escapeAttr, humanSize, displayGatewayUrl, fileNameFromUrl, bindDownloadButtons } = window.AngeUtils;

const state = {
  media: 'image',
  health: null,
  theme: document.documentElement.dataset.theme || 'light',
  token: localStorage.getItem('angemedia.apiToken.v1') || '',
  assistant: localStorage.getItem('angemedia.assistant.v1') === 'true',
  confirmPlan: localStorage.getItem('angemedia.confirmPlan.v1') === 'true',
  statsCollapsed: localStorage.getItem('angemedia.statsCollapsed.v1') === 'true',
  debugJson: localStorage.getItem('angemedia.debugJson.v1') === 'true',
  adminAuthenticated: false,
  pendingPlan: null,
  pendingPrompt: '',
  uploads: [],
  jobs: [],
  activeJob: null,
  jobSeq: 0,
  queueBusy: false,
  lastError: ''
};

const els = {
  boot: $('boot-screen'),
  health: $('health-pill'),
  assistantToggle: $('assistant-toggle'),
  theme: $('theme-btn'),
  settings: $('settings-btn'),
  dialog: $('settings-dialog'),
  apiKey: $('api-key'),
  saveSettings: $('save-settings-btn'),
  clearKey: $('clear-key-btn'),
  confirmPlan: $('confirm-plan'),
  statsDrawer: $('stats-drawer'),
  statsToggle: $('stats-toggle'),
  modelsMetric: $('metric-models'),
  localizeMetric: $('metric-localize'),
  assistantMetric: $('metric-assistant'),
  versionMetric: $('metric-version'),
  form: $('generate-form'),
  prompt: $('prompt'),
  model: $('model'),
  size: $('size'),
  videoOptions: $('video-options'),
  numFrames: $('num-frames'),
  frameRate: $('frame-rate'),
  waitVideo: $('wait-video'),
  routePill: $('route-pill'),
  resultKind: $('result-kind'),
  resultStage: $('result-stage'),
  rawJson: $('raw-json'),
  toggleRawJson: $('toggle-raw-json'),
  planCard: $('plan-card'),
  uploadFiles: $('upload-files'),
  uploadRoles: $('upload-roles'),
  uploadBtn: $('upload-btn'),
  uploadList: $('upload-list'),
  historyList: $('history-list'),
  queueList: $('queue-list'),
  queueSummary: $('queue-summary'),
  generateBtn: $('generate-btn')
};

window.addEventListener('load', () => setTimeout(() => els.boot?.classList.add('hide'), 620));

function authHeaders(json = true) {
  const headers = {};
  if (json) headers['Content-Type'] = 'application/json';
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return headers;
}

function toast(message) {
  const stack = $('toast-stack');
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 3800);
}

bindDownloadButtons(toast);

function setDebugJson(show) {
  state.debugJson = show;
  localStorage.setItem('angemedia.debugJson.v1', String(show));
  if (els.rawJson) els.rawJson.hidden = !show;
  if (els.toggleRawJson) els.toggleRawJson.textContent = show ? '收起调试信息' : '调试信息';
}

els.toggleRawJson?.addEventListener('click', () => setDebugJson(!state.debugJson));
setDebugJson(state.debugJson);

function setTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('angemedia.theme.v1', theme);
  els.theme.textContent = theme === 'dark' ? '☀' : '☾';
}
els.theme.addEventListener('click', () => setTheme(state.theme === 'dark' ? 'light' : 'dark'));
setTheme(state.theme);

function setAssistant(enabled, silent = false) {
  state.assistant = enabled;
  localStorage.setItem('angemedia.assistant.v1', String(enabled));
  els.assistantToggle.classList.toggle('on', enabled);
  els.assistantToggle.setAttribute('aria-pressed', String(enabled));
  els.assistantToggle.querySelector('b').textContent = enabled ? 'Ange 小助手：开' : 'Ange 小助手：关';
  if (silent) {
    if (enabled) renderAssistantIdle();
    return;
  }
  if (enabled) {
    toast('Ange 小助手为 WIP/实验功能，v0.2.0 stable 不作为主流程。');
    renderAssistantIdle();
  } else {
    toast('Ange 小助手已关闭：将使用普通生成流程。');
  }
}
els.assistantToggle.addEventListener('click', () => setAssistant(!state.assistant));
setAssistant(state.assistant, true);

els.settings.addEventListener('click', () => {
  els.apiKey.value = state.token;
  els.confirmPlan.checked = state.confirmPlan;
  els.dialog.showModal();
});
els.saveSettings.addEventListener('click', () => {
  state.token = els.apiKey.value.trim();
  state.confirmPlan = els.confirmPlan.checked;
  if (state.token) localStorage.setItem('angemedia.apiToken.v1', state.token);
  else localStorage.removeItem('angemedia.apiToken.v1');
  localStorage.setItem('angemedia.confirmPlan.v1', String(state.confirmPlan));
  els.dialog.close();
  toast('设置已保存');
  loadHealth();
});
els.clearKey.addEventListener('click', () => {
  state.token = '';
  els.apiKey.value = '';
  localStorage.removeItem('angemedia.apiToken.v1');
  toast('已清除密钥');
});

els.statsDrawer.classList.toggle('collapsed', state.statsCollapsed);
els.statsToggle.textContent = state.statsCollapsed ? '展开' : '收起';
els.statsToggle.addEventListener('click', () => {
  state.statsCollapsed = !state.statsCollapsed;
  localStorage.setItem('angemedia.statsCollapsed.v1', String(state.statsCollapsed));
  els.statsDrawer.classList.toggle('collapsed', state.statsCollapsed);
  els.statsToggle.textContent = state.statsCollapsed ? '展开' : '收起';
});

function setMedia(media) {
  state.media = media;
  document.querySelectorAll('.segment').forEach(btn => btn.classList.toggle('active', btn.dataset.media === media));
  els.videoOptions.classList.toggle('show', media === 'video');
  els.routePill.textContent = media === 'video' ? '视频模式' : '图片模式';
}
document.querySelectorAll('.segment').forEach(btn => btn.addEventListener('click', () => setMedia(btn.dataset.media)));

async function loadHealth() {
  try {
    const res = await fetch('/health', {headers: authHeaders(false)});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'health failed');
    state.health = data;
    els.health.className = 'status-pill ok';
    els.health.querySelector('b').textContent = '网关在线';
    els.modelsMetric.textContent = Array.isArray(data.models) ? data.models.length : '-';
    els.localizeMetric.textContent = data.storage_ready ? '就绪' : '不可用';
    els.versionMetric.textContent = data.version || '-';
    const assistantReady = data.assistant?.enabled && data.assistant?.configured;
    els.assistantMetric.textContent = assistantReady ? '可用' : (data.assistant?.enabled ? '未配置' : '关闭');
  } catch (err) {
    els.health.className = 'status-pill bad';
    els.health.querySelector('b').textContent = '连接异常';
    toast(err.message || '无法连接网关');
  }
}

els.uploadBtn.addEventListener('click', async () => {
  const files = Array.from(els.uploadFiles.files || []);
  if (!files.length) return toast('请选择图片或视频文件');
  const form = new FormData();
  files.forEach(f => form.append('files', f));
  form.append('roles', els.uploadRoles.value.trim());
  try {
    const res = await fetch('/v1/uploads', { method:'POST', headers: authHeaders(false), body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '上传失败');
    state.uploads = data.data || [];
    renderUploads();
    toast('上传完成');
  } catch (err) {
    toast(err.message || '上传失败');
  }
});

function renderUploads() {
  if (!state.uploads.length) {
    els.uploadList.innerHTML = '';
    return;
  }
  els.uploadList.innerHTML = state.uploads.map(item => `<div class="upload-item">
    <b>${escapeHtml(item.role || 'reference')}</b>
    <span>${escapeHtml(item.original_filename || item.filename)}</span>
  </div>`).join('');
}

function uploadedUrls() {
  return state.uploads.map(x => x.url).filter(Boolean);
}

$('route-btn').addEventListener('click', async () => {
  const prompt = els.prompt.value.trim();
  if (!prompt) return toast('先输入提示词');
  if (state.assistant) {
    toast('Ange 正在分析提示词和生成参数');
    els.resultKind.textContent = '计划中';
    setLoading('Ange 正在制定计划', '正在判断类型、模型、尺寸与提示词清晰度，稍后会给出可确认的执行计划。');
  }
  const data = state.assistant
    ? await postJson('/v1/assistant/plan', assistantPayload())
    : await postJson('/v1/media/route', {
        prompt,
        media_type: state.media,
        images: uploadedUrls(),
        requested_model: els.model.value || null,
        size: els.size.value
      });
  if (!data) return;
  const plan = data.plan || data;
  renderPlan(plan, prompt, { showConfirm: state.assistant });
  els.routePill.textContent = data.model || data.input_mode || data.plan?.model || '默认链';
  els.rawJson.textContent = JSON.stringify(data, null, 2);
  if (state.assistant) {
    els.resultKind.textContent = '待确认';
    showPlanPending(plan);
    toast('计划已生成：可以确认执行，也可以继续修改提示词。');
  }
});

$('enhance-btn').addEventListener('click', async () => {
  const prompt = els.prompt.value.trim();
  if (!prompt) return toast('先输入提示词');
  const data = await postJson('/v1/prompt/enhance', { prompt, media_type: state.media });
  if (!data) return;
  renderPromptCompare(prompt, data.enhanced_prompt || prompt, data.notes);
  els.prompt.value = data.enhanced_prompt || prompt;
  els.rawJson.textContent = JSON.stringify(data, null, 2);
  toast(data.changed ? '已增强提示词' : '已检查提示词：当前可直接生成，也可以继续补充细节。');
});

els.form.addEventListener('submit', (event) => {
  event.preventDefault();
  enqueueGenerationJob();
});

function snapshotJob() {
  const prompt = els.prompt.value.trim();
  const uploads = state.uploads.map(item => ({ ...item }));
  const images = uploads.map(x => x.url).filter(Boolean);
  return {
    id: `job-${++state.jobSeq}`,
    prompt,
    media: state.media,
    model: els.model.value || '',
    size: els.size.value,
    assistant: state.assistant,
    confirmPlan: state.assistant && state.confirmPlan,
    uploads,
    images,
    numFrames: Number(els.numFrames.value),
    frameRate: Number(els.frameRate.value),
    waitVideo: els.waitVideo.checked,
    status: 'queued',
    message: '等待执行',
    createdAt: Date.now(),
    startedAt: 0,
    completedAt: 0,
    plan: null,
    result: null
  };
}

function jobSignature(job) {
  return JSON.stringify({
    prompt: job.prompt,
    media: job.media,
    model: job.model,
    size: job.size,
    assistant: job.assistant,
    confirmPlan: job.confirmPlan,
    images: job.images,
    numFrames: job.numFrames,
    frameRate: job.frameRate,
    waitVideo: job.waitVideo
  });
}

function enqueueGenerationJob() {
  const job = snapshotJob();
  if (!job.prompt) return toast('先输入提示词');
  if (!preflightModel(job)) return;
  const signature = jobSignature(job);
  const duplicate = [state.activeJob, ...state.jobs].find(item => item && !isTerminalJob(item) && jobSignature(item) === signature);
  if (duplicate) {
    toast(duplicate.status === 'queued' ? '相同任务已在队列中，未重复加入。' : '相同任务正在处理，完成前不会重复生成。');
    renderQueue();
    return;
  }
  state.jobs.push(job);
  toast(state.activeJob ? `已加入生成队列，第 ${queuedJobs().length} 位。` : '已加入生成队列，马上开始。');
  renderQueue();
  processQueue();
}

function queuedJobs() {
  return state.jobs.filter(job => job.status === 'queued');
}

function isTerminalJob(job) {
  return ['completed', 'submitted', 'failed'].includes(job?.status);
}

function jobStatusText(status) {
  return ({
    queued: '排队中',
    planning: '小助手分析中',
    waiting_confirm: '等待确认',
    verifying: '校验预览',
    generating: '生成中',
    submitted: '已提交',
    completed: '已完成',
    failed: '失败'
  })[status] || status;
}

function renderQueue() {
  if (!els.queueList || !els.queueSummary) return;
  const activeCount = state.activeJob && !isTerminalJob(state.activeJob) ? 1 : 0;
  const waiting = queuedJobs().length;
  els.queueSummary.textContent = activeCount ? `处理中 · 待排 ${waiting}` : (waiting ? `待排 ${waiting}` : '空闲');
  const visible = state.jobs.slice(-8).reverse();
  if (!visible.length) {
    els.queueList.innerHTML = '<div class="empty-state compact"><p>暂无排队任务。重复点击生成时，相同任务会被自动拦截。</p></div>';
    return;
  }
  els.queueList.innerHTML = visible.map((job) => {
    const elapsed = job.startedAt ? Math.max(0, Math.round(((job.completedAt || Date.now()) - job.startedAt) / 1000)) : 0;
    const model = job.plan?.model || job.model || (job.media === 'video' ? 'agnes-video-v2.0' : '默认链');
    return `<article class="queue-card ${escapeAttr(job.status)}">
      <div class="queue-main">
        <span class="mini-status ${job.status === 'failed' ? 'bad' : (job.status === 'completed' ? 'ok' : '')}"><i></i>${escapeHtml(jobStatusText(job.status))}</span>
        <b title="${escapeAttr(job.prompt)}">${escapeHtml(job.prompt)}</b>
        <p>${escapeHtml(job.media)} · ${escapeHtml(model)} · ${escapeHtml(job.size)}${elapsed ? ` · ${elapsed}s` : ''}</p>
      </div>
      <p class="queue-message">${escapeHtml(job.message || '')}</p>
    </article>`;
  }).join('');
}

async function processQueue() {
  if (state.queueBusy || state.activeJob) return;
  const job = state.jobs.find(item => item.status === 'queued');
  if (!job) {
    renderQueue();
    return;
  }
  state.activeJob = job;
  state.queueBusy = true;
  job.startedAt = Date.now();
  try {
    await runQueuedJob(job);
  } finally {
    state.queueBusy = false;
    if (!['waiting_confirm'].includes(job.status)) {
      state.activeJob = null;
      renderQueue();
      if (state.jobs.some(item => item.status === 'queued')) processQueue();
    }
  }
}

async function runQueuedJob(job) {
  const needsPlanConfirm = job.assistant && job.confirmPlan;
  job.status = job.assistant ? 'planning' : 'generating';
  job.message = job.assistant ? 'Ange 正在分析提示词、模型和尺寸。' : '正在调用媒体生成接口。';
  renderQueue();
  els.resultKind.textContent = needsPlanConfirm ? '计划中' : '生成中';
  if (job.assistant) renderAssistantWorking('planning', job);
  setLoading(
    needsPlanConfirm ? 'Ange 正在制定计划' : '正在生成媒体',
    needsPlanConfirm
      ? '正在判断类型、模型、尺寸与提示词清晰度，确认后才会开始生成。'
      : '像素正在聚合成画面，完成后会自动显示预览。'
  );

  if (job.assistant) {
    toast(job.confirmPlan ? 'Ange 正在生成计划' : 'Ange 正在规划并执行生成');
    const data = job.confirmPlan
      ? await postJson('/v1/assistant/plan', assistantPayload(job))
      : await postJson('/v1/assistant/generate', assistantPayload(job));
    if (!data) return markJobFailed(job, state.lastError || '小助手请求失败：未收到服务端错误详情');
    if (job.confirmPlan || (data.requires_confirmation && data.plan)) {
      const plan = data.plan || data;
      job.plan = plan;
      job.status = 'waiting_confirm';
      job.message = data.requires_confirmation ? '服务端要求确认计划，确认后继续执行。' : '计划已生成，确认后继续执行。';
      state.pendingPlan = plan;
      renderPlan(plan, job.prompt, { showConfirm: true, jobId: job.id });
      els.rawJson.textContent = JSON.stringify(data, null, 2);
      els.resultKind.textContent = '待确认';
      showPlanPending(plan, job);
      renderQueue();
      toast('计划已生成：确认后会按这份计划继续执行。');
      return;
    }
    job.plan = data.assistant_plan || data.plan || null;
    job.result = data;
    renderPlan(job.plan || {}, job.prompt);
    if (await applyJobResult(job, data, '小助手生成')) {
      showResult(data);
      loadHistory();
    }
    return;
  }

  const data = await runDirectGeneration(job);
  if (!data) return markJobFailed(job, state.lastError || '生成请求失败');
  job.result = data;
  if (await applyJobResult(job, data, '生成请求')) {
    showResult(data);
    loadHistory();
  }
}

async function runDirectGeneration(job) {
  if (job.media === 'image') {
    const payload = { prompt: job.prompt, size: job.size, response_format: 'url' };
    if (job.model) payload.model = job.model;
    if (job.images.length === 1) payload.image = job.images[0];
    if (job.images.length > 1) payload.images = job.images;
    return postJson('/v1/images/generations', payload);
  }

  const [width, height] = job.size.split('x').map(Number);
  const payload = {
    model: 'agnes-video-v2.0',
    prompt: job.prompt,
    width: Number.isFinite(width) ? width : 1152,
    height: Number.isFinite(height) ? height : 768,
    num_frames: job.numFrames,
    frame_rate: job.frameRate,
    wait_for_completion: job.waitVideo
  };
  if (job.images.length === 1) payload.image = job.images[0];
  if (job.images.length > 1) {
    payload.images = job.images;
    payload.mode = 'keyframes';
  }
  return postJson('/v1/videos', payload);
}

function markJobCompleted(job, message) {
  job.status = 'completed';
  job.message = message || '已完成';
  job.completedAt = Date.now();
  renderQueue();
}

function markJobSubmitted(job, message) {
  job.status = 'submitted';
  job.message = message || '任务已提交，等待远端完成。';
  job.completedAt = Date.now();
  renderQueue();
}

function markJobFailed(job, message) {
  job.status = 'failed';
  job.message = message || '请求失败';
  job.completedAt = Date.now();
  els.resultKind.textContent = '失败';
  if (els.resultStage?.classList.contains('loading')) {
    els.resultStage.classList.remove('loading');
    els.resultStage.innerHTML = `<div class="empty-state compact"><div class="empty-icon">!</div><p>${escapeHtml(job.message)}</p></div>`;
  }
  renderQueue();
}

function resultMediaInfo(data) {
  const item = data?.data?.[0] || {};
  if (item.b64_json) return { ready: true, url: '', type: 'image' };
  if (item.url) return { ready: true, url: displayUrl(item.url), type: 'image' };
  if (data?.video_url) return { ready: true, url: displayUrl(data.video_url), type: 'video' };
  if (data?.task_id) return { ready: false, taskId: data.task_id, type: 'video' };
  return { ready: false };
}

async function mediaUrlAccessible(url) {
  if (!url) return true;
  if (String(url).startsWith('data:')) return true;
  let parsed;
  try {
    parsed = new URL(url, window.location.href);
  } catch {
    return false;
  }
  const sameOrigin = parsed.origin === window.location.origin;
  const staticMedia = parsed.pathname.startsWith('/generated/') || parsed.pathname.startsWith('/uploads/');
  if (!sameOrigin || !staticMedia) return true;
  try {
    let response = await fetch(parsed.pathname + parsed.search, { method: 'HEAD', credentials: 'same-origin' });
    if (response.ok) return true;
    if (response.status === 405 || response.status === 404) {
      response = await fetch(parsed.pathname + parsed.search, { method: 'GET', credentials: 'same-origin' });
      return response.ok;
    }
    return false;
  } catch {
    return false;
  }
}

async function applyJobResult(job, data, sourceLabel = '生成结果') {
  const info = resultMediaInfo(data);
  if (info.ready) {
    job.status = 'verifying';
    job.message = '正在校验生成产物是否可预览。';
    renderQueue();
    const accessible = await mediaUrlAccessible(info.url);
    if (!accessible) {
      markJobFailed(job, `${sourceLabel}已返回，但本地预览文件不可访问：${info.url || '无 URL'}`);
      return false;
    }
    markJobCompleted(job, '生成完成，预览可访问。');
    return true;
  }
  if (info.taskId) {
    markJobSubmitted(job, `视频任务已提交，等待远端完成。任务 ID：${info.taskId}`);
    return true;
  }
  markJobFailed(job, `${sourceLabel}没有返回可预览媒体或任务 ID。`);
  return false;
}

function assistantPayload(job = null) {
  if (job) {
    return {
      prompt: job.prompt,
      media_type: job.media,
      images: job.images,
      image_roles: job.uploads.map(x => ({url:x.url, role:x.role})),
      size: job.size,
      wait_for_completion: job.waitVideo,
      confirm_plan: job.confirmPlan
    };
  }
  return {
    prompt: els.prompt.value.trim(),
    media_type: state.media,
    images: uploadedUrls(),
    image_roles: state.uploads.map(x => ({url:x.url, role:x.role})),
    size: els.size.value,
    wait_for_completion: els.waitVideo.checked,
    confirm_plan: state.confirmPlan
  };
}

async function postJson(url, payload) {
  state.lastError = '';
  try {
    const res = await fetch(url, { method: 'POST', headers: authHeaders(), body: JSON.stringify(payload) });
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 300)}`);
        throw new Error(`接口返回了非 JSON 内容：${text.slice(0, 300)}`);
      }
    }
    if (!res.ok) throw new Error(normalizeErrorDetail(data?.detail || data || `${res.status} ${res.statusText}`));
    return data;
  } catch (err) {
    state.lastError = err.message || '请求失败';
    els.resultKind.textContent = '失败';
    els.rawJson.textContent = state.lastError;
    toast(state.lastError);
    return null;
  }
}

function normalizeErrorDetail(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(normalizeErrorDetail).join('；');
  if (detail && typeof detail === 'object') {
    const parts = [];
    if (detail.message) parts.push(String(detail.message));
    if (Array.isArray(detail.errors)) parts.push(detail.errors.map(item => String(item)).join('；'));
    if (!parts.length) parts.push(JSON.stringify(detail));
    return parts.join('：');
  }
  return String(detail || '请求失败');
}

function setLoading(message = '正在生成媒体', hint = '像素正在聚合成画面，完成后会自动显示预览。') {
  els.resultStage.classList.remove('result-ready');
  els.resultStage.classList.add('loading');
  const pixels = Array.from({ length: 64 }, (_, index) => `<i style="--i:${index}"></i>`).join('');
  els.resultStage.innerHTML = `<div class="empty-state progress-state">
    <div class="pixel-canvas" aria-hidden="true">${pixels}</div>
    <p>${escapeHtml(message)}</p>
    <small>${escapeHtml(hint)}</small>
    <div class="progress-breath" aria-hidden="true"></div>
  </div>`;
}

function showPlanPending(plan, job = null) {
  state.pendingPlan = plan;
  els.resultStage.classList.remove('loading', 'result-ready');
  els.resultStage.innerHTML = `<div class="empty-state plan-pending-state">
    <div class="empty-icon">✓</div>
    <p>计划已准备好</p>
    <small>确认后会按当前计划生成，不需要关闭任何设置。</small>
    <button type="button" class="primary-button small" data-run-pending-plan="${escapeAttr(job?.id || '')}">确认并执行</button>
  </div>`;
}

async function executePlan(plan, job = state.activeJob) {
  if (!plan) return;
  if (job) {
    job.status = 'generating';
    job.message = '计划已确认，正在生成媒体。';
    renderQueue();
  }
  els.resultKind.textContent = '生成中';
  renderAssistantWorking('generating', job || { plan, prompt: state.pendingPrompt || els.prompt.value.trim(), media: plan.media_type || state.media });
  setLoading('正在按计划生成媒体', '计划已确认，像素正在聚合成画面，完成后会自动显示预览。');
  toast('已确认计划，开始生成媒体');
  let data = null;
  if (plan.media_type === 'video') {
    data = await postJson('/v1/videos', {
      model: 'agnes-video-v2.0',
      prompt: plan.prompt || state.pendingPrompt || els.prompt.value.trim(),
      image: plan.image,
      images: plan.images,
      mode: plan.mode,
      width: Number(plan.width || String(plan.size || '').split('x')[0]) || 1152,
      height: Number(plan.height || String(plan.size || '').split('x')[1]) || 768,
      num_frames: Number(plan.num_frames || 121),
      frame_rate: Number(plan.frame_rate || 24),
      wait_for_completion: !!plan.wait_for_completion
    });
  } else {
    const payload = {
      prompt: plan.prompt || state.pendingPrompt || els.prompt.value.trim(),
      model: plan.model || undefined,
      size: plan.size || els.size.value,
      response_format: 'url',
      negative_prompt: plan.negative_prompt
    };
    data = await postJson('/v1/images/generations', payload);
  }
  if (!data) {
    if (job) {
      markJobFailed(job, state.lastError || '确认后生成失败');
      state.activeJob = null;
      processQueue();
    }
    els.resultStage.classList.remove('loading');
    return;
  }
  data.assistant_plan = plan;
  let shouldDisplayResult = true;
  if (job) {
    job.result = data;
    job.plan = plan;
    shouldDisplayResult = await applyJobResult(job, data, '确认后的生成结果');
    state.activeJob = null;
  }
  renderPlan(plan, state.pendingPrompt || els.prompt.value.trim());
  if (shouldDisplayResult) {
    showResult(data);
    loadHistory();
  }
  renderQueue();
  processQueue();
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-run-pending-plan]');
  if (!button) return;
  event.preventDefault();
  const jobId = button.dataset.runPendingPlan || '';
  const job = jobId ? state.jobs.find(item => item.id === jobId) : state.activeJob;
  executePlan(job?.plan || state.pendingPlan, job || state.activeJob);
});

function resultActions(url, fallbackName) {
  const safeUrl = escapeAttr(url);
  const filename = fileNameFromUrl(url, fallbackName);
  return `<div class="result-actions">
    <button type="button" class="secondary-button small" data-download-url="${safeUrl}" data-download-filename="${escapeAttr(filename)}">下载到本地</button>
    <a class="ghost-button small" href="${safeUrl}" target="_blank" rel="noopener">打开媒体</a>
  </div>`;
}

function humanDuration(ms) {
  const n = Number(ms || 0);
  if (!n) return '-';
  if (n < 1000) return `${n}ms`;
  if (n < 60000) return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}s`;
  return `${Math.floor(n / 60000)}m ${Math.round((n % 60000) / 1000)}s`;
}

function statusTone(status) {
  const text = String(status || '').toLowerCase();
  if (['completed', 'succeeded', 'success', 'done'].includes(text)) return 'ok';
  if (['failed', 'error', 'cancelled'].includes(text)) return 'bad';
  return '';
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

function resultMeta(data) {
  const parts = [
    data.provider ? `渠道：${data.provider}` : '',
    data.model ? `模型：${data.model}` : '',
    data.duration_ms ? `耗时：${humanDuration(data.duration_ms)}` : ''
  ].filter(Boolean);
  return parts.length ? `<div class="media-meta-strip">${parts.map(part => `<span>${escapeHtml(part)}</span>`).join('')}</div>` : '';
}

function showResult(data) {
  els.resultStage.classList.remove('loading');
  els.resultStage.classList.add('result-ready');
  els.rawJson.textContent = JSON.stringify(data, null, 2);
  const item = data.data?.[0] || {};
  if (item.url) {
    els.resultKind.textContent = item.localized === false ? '图片（未本地化）' : '图片';
    const url = displayUrl(item.url);
    els.resultStage.innerHTML = `<div class="result-media-card">
      ${resultMeta(data)}
      <img src="${escapeAttr(url)}" alt="generated image" />
      ${resultActions(url, 'angemedia-image.png')}
    </div>`;
    return;
  }
  if (item.b64_json) {
    els.resultKind.textContent = '图片';
    const dataUrl = `data:image/png;base64,${item.b64_json}`;
    els.resultStage.innerHTML = `<div class="result-media-card">
      ${resultMeta(data)}
      <img src="${dataUrl}" alt="generated image" />
      ${resultActions(dataUrl, 'angemedia-image.png')}
    </div>`;
    return;
  }
  if (data.video_url) {
    els.resultKind.textContent = data.localized === false ? '视频（未本地化）' : '视频';
    const url = displayUrl(data.video_url);
    els.resultStage.innerHTML = `<div class="result-media-card">
      ${resultMeta(data)}
      <video src="${escapeAttr(url)}" controls playsinline></video>
      ${resultActions(url, 'angemedia-video.mp4')}
    </div>`;
    return;
  }
  if (data.task_id) {
    els.resultKind.textContent = '视频任务';
    els.resultStage.innerHTML = `<div class="empty-state"><div class="empty-icon">↻</div><p>任务已提交：${escapeHtml(data.task_id)}<br/>请前往 Web Studio Jobs / Assets 查看结果。</p></div>`;
  }
}

function renderPromptCompare(original, enhanced, notes='') {
  const changed = String(original || '').trim() !== String(enhanced || '').trim();
  els.planCard.innerHTML = `<p class="eyebrow">Prompt Compare</p>
    <div class="compare-grid">
      <div><b>原始</b><p>${escapeHtml(original)}</p></div>
      <div><b>增强后</b><p>${escapeHtml(enhanced)}</p></div>
    </div>
    <p class="muted">${escapeHtml(notes || (changed ? '提示词已补充画面信息。' : '当前提示词可直接生成；继续补充风格、镜头、光影或负面限制会更稳。'))}</p>`;
}

function promptDelta(original, planned) {
  const before = String(original || '').trim();
  const after = String(planned || '').trim();
  if (!after) return '小助手还没有生成计划 Prompt。';
  if (before === after) return '当前提示词可直接执行；小助手未改写原意，也可以继续补充主体、风格、镜头、光影或负面限制。';
  const delta = after.length - before.length;
  const areas = promptFocusAreas(after.replace(before, '') || after);
  const areaText = areas.length ? `，重点补充 ${areas.join('、')}` : '，让模型更容易理解画面目标';
  if (delta > 0) return `已扩写提示词，增加约 ${delta} 个字符${areaText}。`;
  return '小助手整理了提示词表达，使它更适合当前模型。';
}

function promptFocusAreas(text) {
  const source = String(text || '');
  const checks = [
    {label:'主体细节', pattern:/主体|人物|角色|少年|少女|场景|物体|服装|表情/},
    {label:'画面风格', pattern:/风格|电影|写实|动漫|插画|摄影|高级|质感/},
    {label:'镜头构图', pattern:/镜头|构图|视角|景别|广角|特写|纵深|背景/},
    {label:'光线氛围', pattern:/光|色彩|氛围|清爽|温柔|明亮|阴影|天空/},
    {label:'动态节奏', pattern:/动作|运动|节奏|转身|移动|镜头推进|慢慢/}
  ];
  return checks.filter(item => item.pattern.test(source)).map(item => item.label).slice(0, 3);
}

function modelLabel(plan) {
  if (plan.media_type === 'video') return 'Agnes Video';
  if (plan.model) return plan.model;
  return '默认图片链（优先 SiliconFlow，失败后按配置降级）';
}

function renderAssistantWorking(stage, job) {
  if (!els.planCard) return;
  const labels = [
    ['queued', '接收任务'],
    ['planning', '分析提示词'],
    ['routing', '选择模型与尺寸'],
    ['prompt', '整理可执行 Prompt'],
    ['generating', '提交生成任务']
  ];
  const activeIndex = stage === 'generating' ? 4 : stage === 'planning' ? 1 : 0;
  els.planCard.innerHTML = `<p class="eyebrow">Ange Working</p>
    <div class="assistant-plan-head">
      <div>
        <h3>Ange 正在工作</h3>
        <p>${escapeHtml(job?.message || '正在分析提示词并准备生成计划。')}</p>
      </div>
      <span class="mini-status ok"><i></i>${escapeHtml(jobStatusText(job?.status || stage))}</span>
    </div>
    <ol class="assistant-steps">
      ${labels.map(([key, label], index) => `<li class="${index <= activeIndex ? 'active' : ''}"><span>${escapeHtml(label)}</span><small>${key === 'prompt' ? escapeHtml((job?.prompt || '').slice(0, 42)) : ''}</small></li>`).join('')}
    </ol>`;
}

function renderAssistantIdle() {
  if (!els.planCard || state.pendingPlan) return;
  els.planCard.innerHTML = `<p class="eyebrow">Ange Assistant</p>
    <div class="assistant-note">
      <b>Ange 小助手 WIP/实验功能</b>
      <p>v0.2.0 stable 不把小助手作为主流程；普通生成应继续使用基础路由或 Web Studio。</p>
    </div>`;
}

function renderPlan(plan, originalPrompt, options = {}) {
  const enhanced = plan.prompt || '';
  state.pendingPlan = options.showConfirm ? plan : null;
  state.pendingPrompt = originalPrompt || els.prompt.value;
  const reason = plan.assistant_message || plan.reason || plan.notes || (plan.assistant_mode === 'rule_fallback' ? '当前使用规则版路由。' : '小助手已根据提示词生成计划。');
  const promptSummary = promptDelta(originalPrompt || els.prompt.value, enhanced);
  const changes = Array.isArray(plan.prompt_changes) ? plan.prompt_changes : [];
  const steps = Array.isArray(plan.work_steps) ? plan.work_steps : [];
  const confirmButton = options.showConfirm ? `<div class="button-row inline plan-actions">
    <button type="button" class="primary-button small" data-run-pending-plan="${escapeAttr(options.jobId || '')}">确认并执行</button>
    <button type="button" class="ghost-button small" data-edit-plan-prompt="true">把计划 Prompt 放回输入框</button>
  </div>` : '';
  els.planCard.innerHTML = `<p class="eyebrow">Ange Plan</p>
    <div class="assistant-plan-head">
      <div>
        <h3>${options.showConfirm ? '计划待确认' : '生成计划'}</h3>
        <p>${escapeHtml(reason)}</p>
      </div>
      ${options.showConfirm ? '<span class="mini-status ok"><i></i>等待确认</span>' : ''}
    </div>
    <div class="plan-facts">
      <span><b>类型</b>${escapeHtml(plan.media_type || '-')}</span>
      <span><b>执行模型</b>${escapeHtml(modelLabel(plan))}</span>
      <span><b>尺寸</b>${escapeHtml(plan.size || `${plan.width || '-'}x${plan.height || '-'}`)}</span>
      <span><b>输入模式</b>${escapeHtml(plan.input_mode || plan.mode || '常规')}</span>
    </div>
    <div class="assistant-note"><b>提示词处理</b><p>${escapeHtml(promptSummary)}</p></div>
    ${changes.length ? `<div class="change-chips">${changes.map(item => `<span>${escapeHtml(item)}</span>`).join('')}</div>` : ''}
    ${steps.length ? `<ol class="assistant-steps compact">${steps.map(item => `<li class="active"><span>${escapeHtml(item)}</span></li>`).join('')}</ol>` : ''}
    <div class="compare-grid">
      <div><b>原始</b><p>${escapeHtml(originalPrompt || els.prompt.value)}</p></div>
      <div><b>计划 Prompt</b><p>${escapeHtml(enhanced)}</p></div>
    </div>
    ${confirmButton}`;
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-edit-plan-prompt]');
  if (!button || !state.pendingPlan?.prompt) return;
  event.preventDefault();
  els.prompt.value = state.pendingPlan.prompt;
  toast('已把计划 Prompt 放回输入框，可以继续微调。');
});

async function loadHistory() {
  if (!state.adminAuthenticated) {
    els.historyList.innerHTML = '<div class="empty-state"><p>后台登录后可查看完整生成历史。</p></div>';
    return;
  }
  try {
    const res = await fetch('/v1/history?limit=24', {headers: authHeaders(false)});
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'history failed');
    const rows = data.data || [];
    if (!rows.length) {
      els.historyList.innerHTML = '<div class="empty-state"><p>暂无历史记录。</p></div>';
      return;
    }
    els.historyList.innerHTML = rows.map(row => {
      const url = displayUrl(row.result_url || '');
      const isVideo = row.media_type === 'video' || /\.(mp4|webm|mov)$/i.test(url);
      const preview = url ? (isVideo
        ? `<video class="history-thumb" src="${escapeAttr(url)}" muted playsinline preload="metadata"></video>`
        : `<img class="history-thumb" src="${escapeAttr(url)}" alt="history preview" loading="lazy" />`) : '';
      return `<article class="history-card media-history-card">
      ${preview}
      <div class="history-body">
        <div class="card-row"><h3>${escapeHtml(row.media_type)} · ${escapeHtml(statusLabel(row.status))}</h3><span class="mini-status ${statusTone(row.status)}"><i></i>${escapeHtml(row.provider || row.model || '记录')}</span></div>
        <p>${escapeHtml(new Date(row.created_at).toLocaleString())}</p>
        <p class="history-prompt" title="${escapeAttr(row.prompt)}">${escapeHtml(row.prompt)}</p>
        <div class="media-meta-strip compact">
          <span>模型：${escapeHtml(row.model || row.request_model || '-')}</span>
          <span>耗时：${escapeHtml(humanDuration(row.duration_ms))}</span>
        </div>
        ${url ? `<div class="button-row inline"><button type="button" class="ghost-button small" data-download-url="${escapeAttr(url)}" data-download-filename="${escapeAttr(fileNameFromUrl(url, 'angemedia-history'))}">下载</button><a class="ghost-button small" href="${escapeAttr(url)}" target="_blank" rel="noopener">打开</a></div>` : ''}
      </div>
    </article>`;
    }).join('');
  } catch (err) {
    els.historyList.innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message || '无法读取历史')}</p></div>`;
  }
}

$('refresh-history').addEventListener('click', loadHistory);
$('clear-history').addEventListener('click', async () => {
  if (!state.adminAuthenticated) {
    toast('请先登录管理后台再清空历史');
    return;
  }
  await fetch('/v1/history', {method:'DELETE', headers: authHeaders(false)});
  await loadHistory();
});


function displayUrl(url) { return displayGatewayUrl(url); }

function providerState(id) {
  const value = state.health?.[id];
  if (typeof value === 'object' && value !== null) {
    return {
      enabled: value.enabled !== false,
      configured: value.configured !== false && value.configured !== undefined ? !!value.configured : value !== false
    };
  }
  if (typeof value === 'string') return { enabled: true, configured: value === 'configured' || value === 'available' };
  return { enabled: true, configured: false };
}

function providerForModel(model) {
  if (!model) return null;
  if (model.startsWith('custom:')) return {name:'自定义渠道', ready:true};
  if (['kolors'].includes(model)) {
    const p = providerState('siliconflow');
    return {name:'SiliconFlow', ready:p.enabled && p.configured};
  }
  if (['qwen','qwen-image','flux','flux-krea','z-image','z-turbo','z-image-turbo'].includes(model)) {
    const p = providerState('modelscope');
    return {name:'ModelScope', ready:p.enabled && p.configured};
  }
  if (['openai-image','gpt-image-2'].includes(model)) {
    const p = providerState('openai_image');
    return {name:'OpenAI-compatible', ready:p.enabled && p.configured};
  }
  if (['agnes-image','agnes-2.1','agnes-2.0'].includes(model)) {
    const p = providerState('agnes_image');
    return {name:'Agnes Image', ready:p.enabled && p.configured};
  }
  if (['pollinations'].includes(model)) {
    const p = providerState('pollinations');
    return {name:'Pollinations', ready:p.enabled};
  }
  return null;
}

function preflightModel(job = null) {
  const model = job?.model || els.model.value;
  const p = providerForModel(model);
  if (p && !p.ready) {
    toast(`${p.name} 未配置密钥，请到管理后台 → 渠道配置 中填写。`);
    return false;
  }
  const videoState = providerState('agnes_video');
  if ((job?.media || state.media) === 'video' && (!videoState.enabled || !videoState.configured)) {
    toast('Agnes Video 未配置密钥，请到管理后台 → 渠道配置 中填写 AGNES_API_KEY。');
    return false;
  }
  return true;
}


async function checkAdminSessionForHistory() {
  try {
    const res = await fetch('/v1/admin/session', { headers: authHeaders(false) });
    const data = await res.json();
    state.adminAuthenticated = !!data.authenticated;
  } catch {
    state.adminAuthenticated = false;
  }
  await loadHistory();
}

loadHealth();
checkAdminSessionForHistory();
renderQueue();

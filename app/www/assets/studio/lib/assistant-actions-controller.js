import { createRequestKey } from './request-key.js';

const BASE = '/admin/assistant';
const terminal = (status) => ['succeeded', 'failed', 'canceled'].includes(status);

// 页面只持有当前会话的提案和CSRF证明；确认项与证明不写入浏览器持久存储。
export class AssistantActionsController {
  constructor(api, onChange, onAuthError, { pollMs = 3000 } = {}) {
    this.api = api; this.onChange = onChange; this.onAuthError = onAuthError; this.pollMs = pollMs;
    this.epoch = 0; this.closed = false; this.identity = '';
    this.state = { items: [], total: 0, offset: 0, loading: false, error: null, busy: new Set(), errors: new Map(), assets: new Map() };
    this.revisions = new Map(); this.editing = new Set();
  }
  emit() { if (!this.closed) this.onChange(this.state); }
  alive(epoch) { return !this.closed && epoch === this.epoch; }
  setContext(owner, sessionId, legacy = false) {
    const identity = owner && sessionId && !legacy ? `${owner}:${sessionId}` : '';
    if (identity === this.identity || this.closed) return;
    clearTimeout(this.timer); this.epoch += 1; this.identity = identity; this.owner = owner; this.sessionId = sessionId;
    this.csrf = null; this.revisions.clear(); this.editing.clear();
    Object.assign(this.state, { items: [], total: 0, offset: 0, loading: false, error: null,
      busy: new Set(), errors: new Map(), assets: new Map() });
    this.emit(); if (identity) this.refresh();
  }
  schedule() {
    clearTimeout(this.timer);
    if (this.identity && !this.closed) this.timer = setTimeout(() => this.refresh(), this.pollMs);
    this.timer?.unref?.();
  }
  accept(action) {
    const previous = this.state.items.find((item) => item.id === action.id);
    if (previous && (previous.version > action.version || (previous.version === action.version
      && ['submitted', 'revoked'].includes(previous.status) && action.status === 'draft'))) return previous;
    if (previous?.job?.id === action.job?.id && terminal(previous?.job?.status) && ['queued', 'running'].includes(action.job?.status)) {
      action = { ...action, job: previous.job };
    }
    if (previous && previous.version !== action.version) this.state.errors.delete(action.id);
    return action;
  }
  async refresh() {
    if (!this.identity || this.closed || this.state.loading || this.editing.size) { this.schedule(); return; }
    const epoch = this.epoch; this.state.loading = true;
    try {
      const result = await this.api.get(`${BASE}/conversations/${this.sessionId}/actions?limit=20&offset=${this.state.offset}`);
      if (!this.alive(epoch)) return;
      this.csrf = result.csrf_token; this.state.items = result.items.map((action) => this.accept(action));
      this.state.total = result.total; this.state.error = null;
      for (const action of this.state.items) {
        if (action.job?.status === 'succeeded' && !this.state.assets.has(action.job.id)) this.loadAssets(action.job.id, epoch);
      }
    } catch (error) { if (this.alive(epoch)) this.fail(error); }
    finally { if (this.alive(epoch)) { this.state.loading = false; this.emit(); this.schedule(); } }
  }
  async loadAssets(jobId, epoch) {
    this.state.assets.set(jobId, []);
    try {
      const result = await this.api.get(`/assets?job_id=${encodeURIComponent(jobId)}&limit=10`);
      if (this.alive(epoch)) { this.state.assets.set(jobId, result.data || []); this.emit(); }
    } catch (error) { if (this.alive(epoch)) { this.state.assets.delete(jobId); if (error.status === 401) this.fail(error); } }
  }
  fail(error) {
    if (this.closed) return;
    if (error.status === 401) this.setContext(null, null);
    if ([401, 403, 404].includes(error.status)) {
      this.csrf = null;
      if (error.status !== 403) { this.state.items = []; this.state.total = 0; this.state.assets.clear(); }
    }
    this.state.error = error.safe?.code || (error.status === 401 ? 'authentication_expired' : 'connection_error');
    if (error.status === 401) { this.identity = ''; clearTimeout(this.timer); this.onAuthError?.(error); }
    this.emit();
  }
  async read(id, epoch = this.epoch) {
    const result = await this.api.get(`${BASE}/actions/${id}`);
    if (!this.alive(epoch)) return;
    this.csrf = result.csrf_token;
    const action = this.accept(result.action);
    this.state.items = this.state.items.map((item) => item.id === id ? action : item);
    this.emit(); return action;
  }
  async operate(action, operation, body) {
    if (this.closed || !this.identity || this.state.busy.has(action.id) || !this.csrf) return false;
    const epoch = this.epoch, busy = this.state.busy;
    busy.add(action.id); this.state.errors.delete(action.id); this.emit();
    try {
      const principal = await this.api.get('/admin/me');
      if (!this.alive(epoch)) return false;
      if (principal.username !== this.owner) { const error = new Error('Account changed'); error.status = 401; throw error; }
      const result = await this.api.post(`${BASE}/actions/${action.id}/${operation}`, body, false,
        { 'X-AngeMedia-Action-CSRF': this.csrf });
      if (!this.alive(epoch)) return false;
      const current = this.accept(result.action);
      this.state.items = this.state.items.map((item) => item.id === current.id ? current : item);
      this.editing.delete(action.id); return true;
    } catch (error) {
      if (!this.alive(epoch)) return false;
      if (error.status === 401) this.fail(error);
      else {
        this.state.errors.set(action.id, error.safe?.code || 'response_lost');
        // 所有不确定结果只用GET核对，绝不自动重发确认或修订。
        try { await this.read(action.id, epoch); } catch (readError) { if (this.alive(epoch)) this.fail(readError); }
      }
      return false;
    } finally { if (this.alive(epoch)) { busy.delete(action.id); this.emit(); this.schedule(); } }
  }
  confirm(action, acknowledgements) {
    return this.operate(action, 'confirm', { version: action.version, binding_digest: action.binding_digest, acknowledgements });
  }
  revoke(action) { return this.operate(action, 'revoke', {}); }
  revise(action, changes) {
    const signature = JSON.stringify({ version: action.version, changes });
    let intent = this.revisions.get(action.id);
    if (intent?.signature !== signature) { intent = { signature, key: createRequestKey() }; this.revisions.set(action.id, intent); }
    return this.operate(action, 'revisions', { version: action.version, request_key: intent.key, changes });
  }
  edit(id, value) { value ? this.editing.add(id) : this.editing.delete(id); if (!value) this.refresh(); }
  page(direction) {
    if (this.editing.size || this.state.loading || this.state.busy.size) return;
    this.state.offset = Math.max(0, this.state.offset + direction * 20); this.refresh();
  }
  close() { this.closed = true; this.epoch += 1; this.csrf = null; clearTimeout(this.timer); this.state.items = []; }
}

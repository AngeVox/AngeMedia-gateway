import { assistantStorage } from './assistant-storage.js';
import { isTerminalRun, observeRun } from './assistant-run-observer.js';
import { createRequestKey } from './request-key.js';

const BASE = '/admin/assistant';

// 只管理界面状态与HTTP意图；运行、权限和工具编排仍由服务端拥有。
export class AssistantChatController {
  constructor(api, storage, onChange, language) {
    this.api = api; this.storage = storage; this.onChange = onChange;
    this.language = language;
    this.epoch = 0; this.closed = false;
    this.state = { owner: '', session: null, messages: [], sessions: [], legacy: [], legacyMode: false,
      run: null, events: [], pending: null, loading: true, submitting: false, disconnected: false,
      error: null, runtime: { can_submit: false, state: 'loading' }, knowledge: [], hasOlder: false };
  }
  emit() { if (!this.closed) this.onChange(this.state); }
  alive(epoch) { return !this.closed && epoch === this.epoch; }
  detach() { this.eventReader?.abort(); this.eventReader = null; this.epoch += 1; }
  remember() { this.store?.write({ sessionId: this.state.session?.id, pending: this.state.pending }); }
  fail(error) {
    if (this.closed) return;
    if (error.status === 401) {
      this.store?.clear(); this.detach();
      Object.assign(this.state, { messages: [], sessions: [], legacy: [], session: null, run: null, events: [], pending: null,
        knowledge: [], owner: '', hasOlder: false, legacyMode: false, disconnected: false,
        loading: false, submitting: false, runtime: { can_submit: false, state: 'authentication_expired' } });
    }
    this.state.error = error.status === 401 ? 'authentication_expired' : error.safe?.code || 'connection_error';
    this.emit();
  }
  async refreshLists() {
    const [sessions, legacy] = await Promise.all([
      this.api.get(`${BASE}/conversations?limit=100`), this.api.get(`${BASE}/sessions?limit=30&offset=0`),
    ]);
    if (this.closed) return;
    this.state.sessions = sessions.items || []; this.state.legacy = legacy.items || [];
  }
  async refreshRuntime() {
    try { const runtime = await this.api.get(`${BASE}/runtime`); if (!this.closed) { this.state.runtime = runtime; this.emit(); } }
    catch (error) { this.fail(error); }
  }
  async init() {
    try {
      const principal = await this.api.get('/admin/me');
      if (this.closed) return;
      this.state.owner = principal.username;
      this.store = assistantStorage(this.storage, principal.username);
      const remembered = this.store.read();
      await this.refreshLists();
      this.state.runtime = await this.api.get(`${BASE}/runtime`);
      if (this.closed) return;
      this.state.pending = remembered.pending || null;
      if (remembered.pending) {
        // 请求响应丢失时保留原始键，交给明确的重试操作；不自动制造新请求。
        this.state.session = { id: remembered.pending.sessionId, title: remembered.pending.message.slice(0, 80) };
        this.state.messages = [{ id: 'pending', role: 'user', content: remembered.pending.message }];
      } else if (remembered.sessionId && this.state.sessions.some((s) => s.id === remembered.sessionId)) {
        await this.open(remembered.sessionId);
      }
      this.state.loading = false; this.emit();
    } catch (error) { this.state.loading = false; this.fail(error); }
  }
  async verifyOwner() {
    const principal = await this.api.get('/admin/me');
    if (principal.username !== this.state.owner) { const error = new Error('Account changed'); error.status = 401; throw error; }
  }
  async open(id, legacyMode = false) {
    if (this.state.pending || this.state.submitting) return;
    this.detach(); const epoch = this.epoch;
    Object.assign(this.state, { loading: true, error: null, session: null, messages: [], events: [], run: null, legacyMode, knowledge: [], hasOlder: false }); this.emit();
    try {
      const detail = await this.api.get(`${BASE}/${legacyMode ? 'sessions' : 'conversations'}/${encodeURIComponent(id)}`);
      if (!this.alive(epoch)) return;
      Object.assign(this.state, { session: detail.session, messages: detail.messages || [], hasOlder: !!detail.has_older, before: detail.before });
      this.remember();
      if (!legacyMode) {
        const { run } = await this.api.get(`${BASE}/conversations/${id}/activity`);
        if (!this.alive(epoch)) return;
        if (run) { this.applyRun(run); this.follow(run.id, epoch); }
      }
    } catch (error) { if (this.alive(epoch)) this.fail(error); }
    finally { if (this.alive(epoch)) { this.state.loading = false; this.emit(); } }
  }
  fresh() {
    if (this.state.loading || this.state.pending || this.state.submitting) return;
    this.detach();
    Object.assign(this.state, { session: null, messages: [], run: null, events: [], error: null, legacyMode: false,
      knowledge: [], loading: false, hasOlder: false, disconnected: false });
    this.remember(); this.emit();
  }
  applyRun(run) {
    this.state.run = run;
    if (run.answer && !this.state.messages.some((m) => m.id === run.assistant_message_id)) {
      this.state.messages.push({ id: run.assistant_message_id, role: 'assistant', content: run.answer });
    }
    this.emit();
  }
  async follow(id, epoch = this.epoch) {
    this.eventReader?.abort();
    const eventReader = new AbortController(); this.eventReader = eventReader;
    this.state.events = []; this.state.disconnected = false; this.state.error = null;
    try {
      await observeRun(this.api, id, { signal: eventReader.signal,
        onRun: (run) => { if (this.alive(epoch)) this.applyRun(run); },
        onEvent: (event) => { if (this.alive(epoch)) {
          this.state.events.push(event);
          if (this.state.run && ['started', 'resumed', 'paused'].includes(event.event_type)) {
            this.state.run.status = event.event_type === 'paused' ? 'paused' : 'running';
          }
          if (this.state.run && event.event_type === 'call_reserved') this.state.run[`${event.payload.kind}_calls`] = event.payload.count;
          this.emit();
        } },
        onConnection: (value) => { if (this.alive(epoch)) { this.state.disconnected = value; this.emit(); } },
      });
      if (!this.alive(epoch) || eventReader.signal.aborted) return;
      this.state.disconnected = false;
      const detail = await this.api.get(`${BASE}/conversations/${this.state.session.id}`);
      if (!this.alive(epoch) || eventReader.signal.aborted) return;
      Object.assign(this.state, { messages: detail.messages, hasOlder: detail.has_older, before: detail.before });
      // 同一会话有多个排队请求时继续观察下一项，不能把队首完成误认为全部完成。
      const { run } = await this.api.get(`${BASE}/conversations/${this.state.session.id}/activity`);
      if (!this.alive(epoch) || eventReader.signal.aborted) return;
      if (run && run.id !== id && !isTerminalRun(run)) { this.applyRun(run); this.follow(run.id, epoch); }
      await this.refreshLists(); this.emit();
    } catch (error) { if (this.alive(epoch) && !eventReader.signal.aborted) { this.state.disconnected = true; this.fail(error); } }
  }
  async submit(message) {
    if (this.closed || this.state.loading || this.state.submitting || this.state.pending || this.state.legacyMode
        || !this.state.runtime.can_submit || (this.state.run && !isTerminalRun(this.state.run))) return false;
    message = message.trim();
    if (!message || message.length > 8000) return false;
    const key = createRequestKey(), sessionId = this.state.session?.id || createRequestKey();
    this.detach(); this.state.run = null; this.state.events = []; this.state.disconnected = false;
    this.state.pending = { key, sessionId, message, create: !this.state.session, title: message.slice(0, 80), language: this.language };
    this.state.messages.push({ id: 'pending', role: 'user', content: message });
    this.remember(); await this.retry(); return true;
  }
  async retry() {
    if (this.state.submitting) return;
    const pending = this.state.pending;
    if (!pending) { if (this.state.run) this.follow(this.state.run.id); return; }
    this.state.submitting = true; this.state.error = null; this.emit();
    try {
      await this.verifyOwner(); if (this.closed) return;
      if (pending.create) {
        const { session } = await this.api.post(`${BASE}/conversations`, { request_key: pending.sessionId, title: pending.title });
        if (this.closed) return;
        this.state.session = session; this.remember();
      }
      const { run } = await this.api.post(`${BASE}/conversations/${pending.sessionId}/runs`, { request_key: pending.key, message: pending.message,
        ...(pending.language ? { language: pending.language } : {}) });
      // 关闭期间得到确认仍要清除待提交文本；重新打开只读服务端运行。
      this.state.pending = null; this.remember();
      if (this.closed) return;
      this.state.messages = this.state.messages.map((m) => m.id === 'pending' ? { ...m, id: run.user_message_id } : m);
      this.applyRun(run); this.follow(run.id); await this.refreshLists();
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.status !== 429 && error.status !== 401) {
        this.state.pending = null; this.remember();
      }
      this.fail(error);
    } finally { this.state.submitting = false; this.emit(); }
  }
  async cancel() {
    const run = this.state.run, epoch = this.epoch;
    if (!run || isTerminalRun(run)) return;
    try { const result = await this.api.post(`${BASE}/runs/${run.id}/cancel`); if (this.alive(epoch)) this.applyRun(result.run); }
    catch (error) { if (this.alive(epoch)) this.fail(error); }
  }
  async older() {
    if (!this.state.hasOlder || this.state.loading || !this.state.session) return;
    const epoch = this.epoch; this.state.loading = true; this.emit();
    try {
      const result = await this.api.get(`${BASE}/conversations/${this.state.session.id}?before=${encodeURIComponent(this.state.before)}`);
      if (!this.alive(epoch)) return;
      const ids = new Set(this.state.messages.map((m) => m.id));
      this.state.messages = [...result.messages.filter((m) => !ids.has(m.id)), ...this.state.messages];
      this.state.hasOlder = result.has_older; this.state.before = result.before;
    } catch (error) { if (this.alive(epoch)) this.fail(error); }
    finally { if (this.alive(epoch)) { this.state.loading = false; this.emit(); } }
  }
  async copyLegacy() {
    if (!this.state.legacyMode || !this.state.session || this.state.submitting) return;
    const source = this.state.session, epoch = this.epoch;
    this.state.submitting = true; this.emit();
    try {
      this.forkKey ||= createRequestKey();
      await this.verifyOwner(); if (!this.alive(epoch)) return;
      const { session } = await this.api.post(`${BASE}/conversations`, { title: source.title || 'AngeMedia', request_key: this.forkKey, source_session_id: source.id });
      if (!this.alive(epoch)) return;
      this.forkKey = null; this.state.submitting = false;
      await this.refreshLists(); await this.open(session.id);
    } catch (error) { if (this.alive(epoch)) this.fail(error); }
    finally { if (this.alive(epoch)) { this.state.submitting = false; this.emit(); } }
  }
  async remove(id) {
    if (this.state.pending || this.state.submitting) return;
    try { await this.api.delete(`${BASE}/conversations/${id}`); if (this.closed) return;
      if (this.state.session?.id === id) this.fresh(); await this.refreshLists(); this.emit();
    } catch (error) { this.fail(error); }
  }
  async knowledge(query) {
    if (!query.trim() || query.length > 500) return;
    const epoch = this.epoch;
    try { const result = await this.api.get(`${BASE}/knowledge?query=${encodeURIComponent(query)}`);
      if (this.alive(epoch)) { this.state.knowledge = result.items; this.emit(); }
    } catch (error) { if (this.alive(epoch)) this.fail(error); }
  }
  close() { this.closed = true; this.detach(); }
}

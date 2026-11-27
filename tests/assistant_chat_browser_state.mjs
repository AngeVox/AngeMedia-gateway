import test from 'node:test';
import assert from 'node:assert/strict';
import { createSseParser, eventSequence } from '../app/www/assets/studio/lib/assistant-sse.js';
import { observeRun } from '../app/www/assets/studio/lib/assistant-run-observer.js';
import { AssistantChatController } from '../app/www/assets/studio/lib/assistant-chat-controller.js';
import { assistantStorage } from '../app/www/assets/studio/lib/assistant-storage.js';

const id = 'a'.repeat(32), sid = 'b'.repeat(32);
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { resolve, promise }; };
const memory = () => { const values = new Map(); return { getItem: (k) => values.get(k) || null, setItem: (k, v) => values.set(k, v), removeItem: (k) => values.delete(k) }; };
const run = (status = 'queued') => ({ id, session_id: sid, status, event_seq: 0, user_message_id: 'user-id', assistant_message_id: 'answer-id' });
const controller = (api, storage = memory()) => {
  const c = new AssistantChatController(api, storage, () => {});
  Object.assign(c.state, { owner: 'alice', loading: false, runtime: { can_submit: true } });
  c.store = assistantStorage(storage, 'alice'); c.follow = () => {}; c.refreshLists = async () => {};
  return c;
};

test('new conversation waits for initial history restoration before clearing the selection', async () => {
  const gate = deferred(), storage = memory();
  assistantStorage(storage, 'alice').write({ sessionId: sid });
  const c = new AssistantChatController({ get: async path => {
    if (path === '/admin/me') return { username: 'alice' };
    if (path.includes('/conversations?')) return { items: [{ id: sid }] };
    if (path.includes('/sessions?')) return { items: [] };
    if (path.endsWith('/runtime')) { await gate.promise; return { can_submit: true }; }
    if (path.endsWith('/activity')) return { run: null };
    return { session: { id: sid }, messages: [{ content: 'old history' }] };
  } }, storage, () => {});
  try {
    const init = c.init(); await new Promise(resolve => setImmediate(resolve));
    const epoch = c.epoch; c.fresh(); assert.equal(c.epoch, epoch);
    gate.resolve(); await init; c.fresh();
    assert.equal(c.state.session, null); assert.deepEqual(c.state.messages, []);
    assert.equal(assistantStorage(storage, 'alice').read().sessionId, null);
  } finally { c.close(); }
});

test('SSE handles UTF-8 text, chunked CRLF and multiline data without swallowing callback errors', () => {
  const frames = [], feed = createSseParser((frame) => frames.push(frame));
  for (const part of [': ping\r', '\nevent: tool_started\r\nid: abc:1\r\ndata: {"text":\r', '\ndata: "月光"}\r\n\r', '\n']) feed(part);
  assert.deepEqual(frames, [{ event: 'tool_started', id: 'abc:1', data: { text: '月光' } }]);
  const bad = createSseParser(() => { throw new Error('handler failure'); });
  assert.throws(() => bad('data: {}\n\n'), /handler failure/);
  assert.throws(() => createSseParser(() => {})('data: not-json\n\n'), SyntaxError);
  assert.throws(() => createSseParser(() => {})('data: ' + 'x'.repeat(1048577) + '\n'), /size/);
});

test('event cursors deduplicate replay and reject gaps or a different run', () => {
  const frame = { id: `${id}:2`, data: { run_id: id, sequence: 2 } };
  assert.equal(eventSequence(frame, id, 1), 2);
  assert.equal(eventSequence(frame, id, 2), null);
  assert.throws(() => eventSequence(frame, id, 0), /Missing/);
  assert.throws(() => eventSequence(frame, sid, 1), /Invalid/);
});

test('reconnect only reads saved events and resumes after the last delivered sequence', async () => {
  const paths = [], events = [], states = [];
  const api = { async stream(path, { onEvent }) {
    paths.push(path); const sequence = paths.length;
    onEvent({ id: `${id}:${sequence}`, event: 'started', data: { run_id: id, sequence } });
    if (paths.length === 1) throw new TypeError('socket closed');
    onEvent({ event: 'done', data: { run: { ...run('succeeded'), event_seq: 2 } } });
  }, post() { assert.fail('断线不能再次提交'); } };
  await observeRun(api, id, { signal: new AbortController().signal, onRun: (value) => states.push(value), onEvent: (event) => events.push(event), onConnection() {} });
  assert.deepEqual(paths.map((path) => path.split('after=')[1]), ['0', '1']);
  assert.deepEqual(events.map((event) => event.sequence), [1, 2]);
  assert.equal(states.at(-1).status, 'succeeded');
});

test('terminal 204 response recovers status with a GET and needs no new submission', async () => {
  let gets = 0, latest;
  await observeRun({ stream: async () => {}, get: async () => { gets += 1; return { run: run('failed') }; } }, id,
    { signal: new AbortController().signal, onRun: (value) => { latest = value; }, onEvent() {}, onConnection() {} });
  assert.equal(gets, 1); assert.equal(latest.status, 'failed');
});

test('double Enter and an uncertain admission retry keep one intent and the same keys', async () => {
  const gate = deferred(), posts = [], admitted = new Set(); let first = true;
  const api = { get: async () => ({ username: 'alice' }), async post(path, payload) {
    posts.push({ path, payload });
    if (path.endsWith('/conversations')) { await gate.promise; return { session: { id: payload.request_key, title: payload.title } }; }
    admitted.add(payload.request_key);
    if (first) { first = false; throw new TypeError('response lost'); }
    return { run: run() };
  } };
  const c = controller(api), sending = c.submit('最近失败的任务');
  assert.equal(await c.submit('重复回车'), false);
  gate.resolve(); await sending;
  assert.ok(c.state.pending); const key = c.state.pending.key;
  await c.retry();
  const submissions = posts.filter((p) => p.path.endsWith('/runs'));
  assert.equal(submissions.length, 2);
  assert.equal(submissions[0].payload.request_key, key);
  assert.deepEqual(submissions[0], submissions[1]);
  assert.equal(admitted.size, 1); assert.equal(c.state.pending, null);
});

test('closing during an acknowledged POST clears pending text and never attaches a hidden subscriber', async () => {
  const gate = deferred(), storage = memory(); let followed = 0;
  const c = controller({ get: async () => ({ username: 'alice' }), post: async (path, payload) => path.endsWith('/conversations')
    ? { session: { id: payload.request_key } } : gate.promise }, storage);
  c.follow = () => { followed += 1; };
  const sending = c.submit('私有描述'); await new Promise((r) => setImmediate(r)); c.close();
  gate.resolve({ run: run() }); await sending;
  assert.equal(followed, 0); assert.equal(assistantStorage(storage, 'alice').read().pending, null);
});

test('account changes prevent a pending text from being submitted and clear its browser record', async () => {
  let posts = 0;
  const storage = memory(), c = controller({ get: async () => ({ username: 'bob' }), post: async () => { posts += 1; } }, storage);
  await c.submit('Alice 的私有输入');
  assert.equal(posts, 0); assert.equal(c.state.error, 'authentication_expired');
  assert.deepEqual(assistantStorage(storage, 'alice').read(), {});
});

test('a new account cannot restore another account pending text', () => {
  const storage = memory();
  assistantStorage(storage, 'alice').write({ sessionId: sid, pending: { key: id, sessionId: sid, message: '只给 Alice' } });
  assert.deepEqual(assistantStorage(storage, 'bob').read(), {});
});

test('late history from the previous conversation cannot overwrite the selected conversation', async () => {
  const old = deferred();
  const c = controller({ get: async (path) => path.endsWith('/activity') ? { run: null } : path.endsWith('/old') ? old.promise
    : { session: { id: sid }, messages: [{ id: 'new', content: '当前对话' }] } });
  const opening = c.open('old'); await c.open(sid);
  old.resolve({ session: { id: 'old' }, messages: [{ id: 'old', content: '迟到回复' }] }); await opening;
  assert.equal(c.state.session.id, sid); assert.equal(c.state.messages[0].content, '当前对话');
});

test('stopping uses explicit cancellation while closing only detaches the subscription', async () => {
  const posts = [], c = controller({ post: async (path) => { posts.push(path); return { run: run('canceled') }; } });
  c.state.run = run(); await c.cancel(); assert.equal(c.state.run.status, 'canceled');
  c.close(); assert.deepEqual(posts, [`/admin/assistant/runs/${id}/cancel`]);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { AssistantActionsController } from '../app/www/assets/studio/lib/assistant-actions-controller.js';
// 同时加载真实卡片与装配模块，使界面语法错误进入常规运行门禁。
import '../app/www/assets/studio/components/assistant-action-card.js';
import '../app/www/assets/studio/components/assistant-actions.js';

const sid = 'a'.repeat(32), id = 'b'.repeat(32), digest = 'c'.repeat(64);
const draft = () => ({ id, session_id: sid, version: 1, current_version: 1, status: 'draft', binding_digest: digest,
  review: { allowed: true, request: { prompt: '山谷' }, required_acknowledgements: ['may_incur_cost'] } });
const response = (action = draft()) => ({ items: [action], total: 1, csrf_token: 'd'.repeat(64) });
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const ready = async (api) => {
  const controller = new AssistantActionsController(api, () => {}, () => {}, { pollMs: 999999 });
  controller.setContext('alice', sid); await new Promise((r) => setImmediate(r)); return controller;
};

test('expired login clears proposals, assets, revision intents and CSRF proof', async () => {
  const c = await ready({ get: async () => response() });
  try {
    c.state.assets.set('job', [{ prompt: 'private asset metadata' }]);
    c.revisions.set(id, { signature: 'private input' });
    c.fail({ status: 401 });
    assert.equal(c.csrf, null); assert.equal(c.identity, '');
    assert.deepEqual(c.state.items, []); assert.equal(c.state.total, 0);
    assert.equal(c.state.assets.size, 0); assert.equal(c.revisions.size, 0);
  } finally { c.close(); }
});

test('repeated confirm clicks use one POST with session CSRF and original action revision', async () => {
  const gate = deferred(), posts = [];
  const api = { get: async (path) => path === '/admin/me' ? { username: 'alice' } : response(),
    async post(path, body, login, headers) { posts.push({ path, body, headers }); await gate.promise; return { action: { ...draft(), status: 'submitted', job: { id: 'job', status: 'queued' } } }; } };
  const c = await ready(api);
  try {
    const task = c.confirm(draft(), ['may_incur_cost']);
    assert.equal(await c.confirm(draft(), ['may_incur_cost']), false);
    gate.resolve(); await task;
    assert.equal(posts.length, 1); assert.equal(posts[0].body.binding_digest, digest);
    assert.equal(posts[0].headers['X-AngeMedia-Action-CSRF'], response().csrf_token);
  } finally { c.close(); }
});

test('lost confirmation response reads saved receipt without automatically resubmitting', async () => {
  let posts = 0, reads = 0;
  const submitted = { ...draft(), status: 'submitted', job: { id: 'job', status: 'queued' } };
  const c = await ready({ get: async (path) => {
    if (path === '/admin/me') return { username: 'alice' };
    if (path.endsWith('/' + id)) { reads++; return { action: submitted, csrf_token: response().csrf_token }; }
    return response();
  }, post: async () => { posts++; throw new TypeError('response lost'); } });
  try { await c.confirm(draft(), ['may_incur_cost']); assert.equal(posts, 1); assert.equal(reads, 1); assert.equal(c.state.items[0].status, 'submitted'); }
  finally { c.close(); }
});

test('revision response loss keeps original request key on an explicit identical retry', async () => {
  const posts = [];
  const c = await ready({ get: async (path) => path === '/admin/me' ? { username: 'alice' }
    : path.endsWith('/' + id) ? { action: draft(), csrf_token: response().csrf_token } : response(),
    post: async (path, body) => { posts.push(body); throw new TypeError('response lost'); } });
  try { await c.revise(draft(), { prompt: '新的山谷' }); await c.revise(draft(), { prompt: '新的山谷' });
    assert.equal(posts.length, 2); assert.equal(posts[0].request_key, posts[1].request_key); }
  finally { c.close(); }
});

test('account switch prevents POST and clears previously loaded proposal data', async () => {
  let posts = 0;
  const c = await ready({ get: async (path) => path === '/admin/me' ? { username: 'bob' } : response(), post: async () => { posts++; } });
  try { await c.confirm(draft(), ['may_incur_cost']); assert.equal(posts, 0); assert.equal(c.state.items.length, 0); assert.equal(c.csrf, null); }
  finally { c.close(); }
});

test('old list responses cannot turn a consumed action back into a draft', async () => {
  const c = await ready({ get: async () => response() });
  try { c.state.items = [{ ...draft(), status: 'submitted', job: { id: 'job', status: 'queued' } }];
    await c.refresh(); assert.equal(c.state.items[0].status, 'submitted'); }
  finally { c.close(); }
});

test('a response from an old conversation cannot populate the newly opened one', async () => {
  const gate = deferred(); let calls = 0;
  const c = new AssistantActionsController({ get: async () => ++calls === 1 ? gate.promise : { items: [], total: 0, csrf_token: 'new' } }, () => {}, () => {});
  try { c.setContext('alice', sid); c.setContext('bob', 'e'.repeat(32)); gate.resolve(response());
    await new Promise((r) => setImmediate(r)); assert.equal(c.state.items.length, 0); assert.equal(c.owner, 'bob'); }
  finally { c.close(); }
});

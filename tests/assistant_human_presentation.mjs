import test from 'node:test';
import assert from 'node:assert/strict';
import { setLanguage } from '../app/www/assets/studio/i18n.js';
import { mediaProgressKey, mediaProgress, isActionAsset } from '../app/www/assets/studio/lib/assistant-media-presentation.js';
import { runEventText, runSummary, runText } from '../app/www/assets/studio/lib/assistant-run-copy.js';
import { actionPolicyText, actionText, actionValue, actionVersion } from '../app/www/assets/studio/lib/assistant-action-copy.js';
import { displayJobStage, displayJobEventType, displayJobProvider, displayJobErrorHint, showJobErrorMessage } from '../app/www/assets/studio/lib/job-display.js';
import '../app/www/assets/studio/components/assistant-progress.js';
import '../app/www/assets/studio/components/assistant-action-card.js';

const preferences = new Map();
globalThis.localStorage = { getItem: key => preferences.get(key), setItem: (key, value) => preferences.set(key, value) };
const job = { id: 'a'.repeat(32), kind: 'video', status: 'running', stage: 'video_poll', external_task_present: true };
const asset = { job_id: job.id, url_path: '/generated/video.mp4' };

test('a completed assistant reply never implies media generation finished', () => {
  setLanguage('zh-CN');
  const summary = runSummary({ run: { status: 'succeeded' } });
  assert.match(summary, /答复/); assert.doesNotMatch(summary, /作品|生成成功/);
  assert.equal(mediaProgressKey(job), 'video');
});
test('saved requires a completed job and its own controlled asset', () => {
  const done = { ...job, status: 'succeeded' };
  assert.equal(mediaProgressKey(done), 'completed');
  assert.equal(mediaProgressKey(done, [{ ...asset, job_id: 'other' }]), 'completed');
  assert.equal(mediaProgressKey(done, [{ ...asset, url_path: 'https://elsewhere.invalid/result' }]), 'completed');
  assert.equal(mediaProgressKey(done, [asset]), 'saved');
  assert.notEqual(mediaProgressKey(job, [asset]), 'saved');
});
test('asset links reject traversal, signed URLs, and other jobs', () => {
  for (const path of ['/generated/../x', '/generated/%2e%2e/x', '/generated/a.mp4?token=secret', '/generated/%2fx']) {
    assert.equal(isActionAsset({ ...asset, url_path: path }, job), false);
  }
  assert.equal(isActionAsset(asset, job), true);
});
test('polling and saving remain distinct observations without invented percentage', () => {
  assert.equal(mediaProgressKey(job), 'video');
  assert.equal(mediaProgressKey({ ...job, external_task_present: false }), 'checking');
  assert.equal(mediaProgressKey({ ...job, stage: 'asset_import' }), 'saving');
  assert.equal(mediaProgressKey({ ...job, error_code: 'video_poll_failed' }), 'poll_retry');
  assert.doesNotMatch(mediaProgress(job), /\d+%|分钟后|轮询/);
});
test('unknown submission and exhausted waiting never suggest automatic resubmission', () => {
  setLanguage('zh-CN');
  const uncertain = { ...job, status: 'failed', error_code: 'video_submit_ambiguous' };
  assert.equal(mediaProgressKey(uncertain), 'uncertain'); assert.match(mediaProgress(uncertain), /不能确认|不确定/);
  assert.doesNotMatch(mediaProgress(uncertain), /点击重试|自动重试|重新生成即可/);
  assert.equal(mediaProgressKey({ ...uncertain, error_code: 'video_poll_timeout' }), 'timeout');
});
test('confirmed video queue rejection uses the same localized copy in Jobs and Assistant', () => {
  const failure = { ...job, status: 'failed', error_code: 'video_submit_queue_full', error_message: 'HTTP 503' };
  for (const [locale, copy] of [['zh-CN', /队列已满.*本次未受理/], ['en-US', /queue is full.*request was not accepted/]]) {
    setLanguage(locale);
    const hint = displayJobErrorHint(failure);
    assert.match(hint, copy);
    assert.equal(mediaProgress(failure), hint);
    assert.equal(showJobErrorMessage(failure), false);
    const ambiguous = { ...failure, error_code: 'video_submit_ambiguous' };
    assert.doesNotMatch(displayJobErrorHint(ambiguous), copy);
    assert.equal(showJobErrorMessage(ambiguous), true);
  }
});
test('failed saves and authentication cannot be mislabeled as successful work or content refusal', () => {
  const failure = { ...job, status: 'failed' };
  assert.equal(mediaProgressKey({ ...failure, error_code: 'video_asset_import_failed' }, [asset]), 'save_failed');
  assert.equal(mediaProgressKey({ ...failure, error_category: 'auth_failed' }), 'auth');
  assert.equal(mediaProgressKey({ ...failure, error_category: 'content_filtered' }), 'filtered');
  assert.equal(mediaProgressKey({ ...failure, error_category: 'future_secret_code' }), 'failed');
  for (const category of ['__proto__', 'constructor', 'toString']) assert.equal(mediaProgressKey({ ...failure, error_category: category }), 'failed');
});
test('tool success does not claim a matching result and unknown names never reach prose', () => {
  const text = runEventText({ event_type: 'tool_finished', payload: { name: 'asset_search', status: 'success' } });
  assert.doesNotMatch(text, /已找到|生成成功/);
  assert.doesNotMatch(runEventText({ event_type: 'tool_started', payload: { name: '<secret-tool-body>' } }), /secret-tool/);
  assert.equal(runEventText({ event_type: 'future_raw_event' }), null);
  assert.doesNotMatch(runEventText({ event_type: 'tool_finished', payload: { name: 'propose_image', status: 'error' } }), /已整理|已生成/);
  assert.doesNotMatch(runEventText({ event_type: 'tool_finished', payload: { name: 'propose_image', status: 'future_status' } }), /已整理|已生成/);
});
test('unknown tools distinguish an active check from a completed, failed, or unconfirmed step in both languages', () => {
  for (const language of ['zh-CN', 'en-US']) {
    setLanguage(language);
    const payload = { name: '<private-future-tool>' };
    const started = runEventText({ event_type: 'tool_started', payload });
    const finished = runEventText({ event_type: 'tool_finished', payload: { ...payload, status: 'success' } });
    const failed = runEventText({ event_type: 'tool_finished', payload: { ...payload, status: 'error' } });
    const unconfirmed = runEventText({ event_type: 'tool_finished', payload: { ...payload, status: 'future-status' } });
    assert.equal(new Set([started, finished, failed, unconfirmed]).size, 4);
    for (const value of [started, finished, failed, unconfirmed]) {
      assert.equal(typeof value, 'string');
      assert.doesNotMatch(value, /private-future|assistant\.run\./);
    }
    assert.equal(runText('__proto__'), runText('unavailable'));
    assert.equal(runText('constructor'), runText('unavailable'));
    assert.equal(runEventText({ event_type: '__proto__' }), null);
  }
});
test('English uses the same safety distinctions and preserves exact creative values', () => {
  setLanguage('en-US');
  assert.match(mediaProgress({ ...job, status: 'succeeded' }, [asset]), /saved/);
  assert.match(mediaProgress({ ...job, status: 'failed', error_code: 'video_submit_ambiguous' }), /unclear/);
  const creative = 'auto submit video_poll <tag> 自然光';
  assert.equal(actionValue('prompt', creative), creative);
  assert.equal(actionValue('model', 'agnes-video-v2.5'), 'agnes-video-v2.5');
  assert.equal(actionValue('response_format', 'url'), 'Work link');
});
test('confirmation copy translates the revision and falls back for unknown policy and error codes', () => {
  for (const [language, confirm, submitted, version, yes] of [
    ['zh-CN', '确认并提交', '已确认并提交', '第 3 版', '是'],
    ['en-US', 'Confirm and submit', 'Confirmed and submitted', 'Revision 3', 'Yes'],
  ]) {
    setLanguage(language);
    assert.equal(actionText('confirm'), confirm);
    assert.equal(actionText('submitted'), submitted);
    assert.equal(actionVersion(3), version);
    assert.equal(actionValue('watermark', true), yes);
    assert.equal(actionValue('output_format', 'png'), 'PNG');
    assert.equal(actionText('__proto__'), actionText('generic_error'));
    assert.equal(actionPolicyText('billing', 'future_code'), actionPolicyText('billing', 'unknown'));
    assert.equal(actionPolicyText('review', 'future_code'), actionPolicyText('review', 'unknown'));
    assert.notEqual(actionPolicyText('billing', 'unknown'), actionPolicyText('review', 'unknown'));
  }
});

test('shared job stage and event labels remove worker and polling jargon', () => {
  setLanguage('zh-CN');
  for (const stage of ['video_submit', 'video_poll', 'asset_import', 'provider_poll']) assert.doesNotMatch(displayJobStage(stage), /轮询|资产导入|video_/);
  assert.equal(displayJobProvider('agnes_video'), 'Agnes 视频');
  assert.equal(displayJobProvider('custom:real-channel-id'), 'custom:real-channel-id');
  for (const event of ['dispatch_published', 'worker_attempt_started', 'worker_attempt_succeeded']) assert.doesNotMatch(displayJobEventType(event), /Worker|worker_/);
});

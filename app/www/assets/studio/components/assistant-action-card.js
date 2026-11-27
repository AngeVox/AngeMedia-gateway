import { api } from '../api.js';
import { el } from './dom.js';
import { button } from './buttons.js';
import { actionText as text, actionField, actionVersion, actionValue, actionProvider, actionPolicyText } from '../lib/assistant-action-copy.js';
import { formatDate } from '../lib/format.js';
import { mediaProgress, isActionAsset } from '../lib/assistant-media-presentation.js';

const needsReview = new Set(['action_environment_changed', 'action_policy_denied', 'action_expired', 'action_binding_conflict']);

function parameters(request) {
  const rows = Object.entries(request).filter(([key]) => key !== 'prompt').map(([key, value]) =>
    el('div', {}, el('dt', {}, actionField(key)), el('dd', {},
      actionValue(key, value))));
  return el('details', {}, el('summary', {}, text('parameters')),
    el('dl', { class: 'assistant-action-parameters' }, ...rows));
}

function routeEvidence(route) {
  const policy = route.policy || {};
  const description = [text(route.capability_evidence, 'custom_or_unverified'), actionPolicyText('billing', policy.billing),
    actionPolicyText('review', policy.review_status)].join(' · ');
  const details = el('details', {}, el('summary', {}, text('evidenceDetails')),
    el('p', { class: 'card-subtitle' }, policy.reviewed_at
      ? `${text('reviewedAt')} ${policy.reviewed_at}${policy.review_after ? ` · ${text('reviewAfter')} ${policy.review_after}` : ''}` : text('noReview')),
    ...(policy.sources || []).map((source) => el('p', { class: 'card-subtitle' }, source)));
  return el('li', {}, el('strong', {}, `${actionProvider(route.provider)} · ${route.model}`),
    el('p', { class: 'card-subtitle' }, description), details);
}

// 卡片保存本次输入焦点和勾选；后台读取不会重建同版本表单，新版本才使旧确认失效。
export class AssistantActionCard {
  constructor(controller, action, onNavigate) {
    this.onNavigate = onNavigate;
    this.controller = controller; this.acknowledged = new Set();
    this.header = el('strong'); this.body = el('div'); this.confirmations = el('div', { class: 'assistant-action-checks' });
    this.expiry = el('p', { class: 'card-subtitle', hidden: true });
    this.notice = el('p', { class: 'assistant-action-notice', role: 'status', ariaLive: 'polite' });
    this.editor = el('div', { class: 'assistant-action-editor', hidden: true });
    this.progress = el('p', { class: 'assistant-media-progress', role: 'status', ariaLive: 'polite' });
    this.job = el('div');
    this.submit = button(text('confirm'), { variant: 'primary', onClick: () => controller.confirm(this.action, [...this.acknowledged]) });
    this.edit = button(text('edit'), { onClick: () => this.openEditor() });
    this.recheck = button(text('recheck'), { onClick: () => controller.revise(this.action, { prompt: this.action.review.request.prompt }) });
    this.revoke = button(text('withdraw'), { onClick: () => controller.revoke(this.action) });
    this.refresh = button(text('refresh'), { size: 'sm', onClick: () => controller.read(this.action.id).catch((error) => controller.fail(error)) });
    this.controls = el('div', { class: 'action-row assistant-action-controls' }, this.submit, this.edit, this.recheck, this.revoke, this.refresh);
    this.node = el('article', { class: 'assistant-action-card', dataset: { actionId: action.id } }, this.header, this.body,
      this.expiry, this.confirmations, this.notice, this.editor, this.controls,
      el('div', { class: 'assistant-action-job' }, this.progress, this.job));
    this.update(action, {});
  }
  update(action, { busy = false, error = null, assets = [] }) {
    const versionChanged = this.action && (this.action.version !== action.version || this.action.binding_digest !== action.binding_digest);
    this.action = action; this.busy = busy; this.error = error;
    if (versionChanged) this.acknowledged.clear();
    this.header.textContent = `${text(action.review?.kind || 'proposal')} · ${text(action.status)} · ${actionVersion(action.version)}`;
    const key = JSON.stringify([action.version, action.review]);
    if (key !== this.reviewKey) {
      this.reviewKey = key; this.body.textContent = ''; this.confirmations.textContent = '';
      const review = action.review;
      if (review) {
        this.body.append(el('h4', {}, text('prompt')),
          el('div', { class: 'assistant-action-prompt', tabindex: 0 }, review.request.prompt),
          parameters(review.request), el('h4', {}, text('routes')),
          el('ul', {}, ...(review.routes || []).map(routeEvidence)),
          el('p', { class: 'card-subtitle' }, text('evidence')), el('p', {}, text('cost')));
        if (!review.allowed) this.body.append(el('p', { class: 'assistant-action-blocked' },
          text('blocked'), ' ', (review.blocked_reasons || []).map((reason) => text(reason)).join(' · ')));
        for (const acknowledgement of review.required_acknowledgements || []) {
          const checkbox = el('input', { type: 'checkbox', checked: this.acknowledged.has(acknowledgement),
            dataset: { acknowledgement }, onchange: (event) => {
              event.target.checked ? this.acknowledged.add(acknowledgement) : this.acknowledged.delete(acknowledgement);
              this.buttons();
            } });
          this.confirmations.append(el('label', {}, checkbox, el('span', {}, text(acknowledgement))));
        }
      }
    }
    this.expiry.hidden = action.status !== 'draft' || !action.review || !action.expires_ms;
    this.expiry.textContent = this.expiry.hidden ? '' : text('validUntil').replace('{date}', formatDate(action.expires_ms));
    this.notice.textContent = this.editVersion && this.editVersion !== action.version ? text('versionChanged') : error ? text(error) : busy ? text('busy') : '';
    const jobKey = JSON.stringify([action.job, assets]);
    if (jobKey !== this.jobKey) {
      this.jobKey = jobKey; this.job.textContent = '';
      const visibleAssets = assets.filter((asset) => isActionAsset(asset, action.job));
      const progress = action.job ? mediaProgress(action.job, visibleAssets) : '';
      if (progress !== this.progress.textContent) this.progress.textContent = progress;
      if (action.job) {
        if (['queued', 'running'].includes(action.job.status)) this.job.append(el('p', { class: 'card-subtitle' }, text('notCancel')));
        this.job.append(el('details', {}, el('summary', {}, text('receipt')), el('p', {}, action.job.id)));
        if (/^[a-f0-9]{32}$/.test(action.job.id)) this.job.append(el('a', { href: `#/jobs/${action.job.id}`,
          class: 'assistant-action-detail', onclick: () => this.onNavigate?.() }, text('jobDetails')));
        if (action.job.status === 'succeeded') {
          for (const asset of visibleAssets) {
            const href = asset.id ? `#/assets/${encodeURIComponent(asset.id)}` : api.assetUrl(asset.url_path, { preview: true });
            if (!href) continue;
            this.job.append(el('a', { href, class: 'assistant-action-asset',
              ...(asset.id ? { onclick: () => this.onNavigate?.() } : { target: '_blank', rel: 'noopener noreferrer' }),
            }, [text('artwork'), asset.filename].filter(Boolean).join(' · ')));
          }
        }
      }
    }
    this.buttons();
  }
  buttons() {
    const action = this.action, draft = action.status === 'draft', editable = ['draft', 'expired'].includes(action.status) && !!action.review;
    const required = action.review?.required_acknowledgements || [];
    const invalid = this.busy || !!this.editVersion;
    this.submit.hidden = !draft;
    this.submit.disabled = invalid || !action.review?.allowed || needsReview.has(this.error)
      || action.version !== action.current_version || required.some((item) => !this.acknowledged.has(item));
    this.confirmations.hidden = !draft;
    this.confirmations.querySelectorAll('input').forEach((input) => { input.disabled = invalid; });
    this.edit.hidden = !editable; this.edit.disabled = invalid;
    this.recheck.hidden = !editable; this.recheck.disabled = invalid;
    this.revoke.hidden = !editable; this.revoke.disabled = invalid;
    this.refresh.disabled = this.busy;
    if (this.save) this.save.disabled = this.busy || !editable || this.editVersion !== action.version;
    if (this.cancelEdit) this.cancelEdit.disabled = this.busy;
    this.editor.querySelectorAll('input,textarea').forEach((input) => { input.disabled = this.busy; });
  }
  openEditor() {
    const action = this.action; this.editVersion = action.version;
    this.controller.edit(action.id, true); this.editor.hidden = false; this.editor.textContent = '';
    const fields = {}, original = action.review.request;
    for (const key of ['prompt', 'size', ...(action.review.kind === 'video' ? ['seconds', 'aspect_ratio'] : [])]) {
      // 仅显示该请求已经拥有的协议字段，不能把2.5字段塞进旧视频协议。
      if (!(key in original)) continue;
      const input = key === 'prompt' ? el('textarea', { rows: 4, maxLength: 8000, value: original[key] })
        : el('input', { type: 'text', value: original[key], maxLength: 64 });
      fields[key] = input;
      this.editor.append(el('label', {}, el('span', {}, key === 'prompt' ? text('prompt') : actionField(key)), input));
    }
    this.save = button(text('save'), { variant: 'primary', onClick: async () => {
      const changes = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));
      const source = { ...action, version: this.editVersion };
      if (await this.controller.revise(source, changes)) this.closeEditor();
    } });
    this.cancelEdit = button(text('cancel'), { onClick: () => this.closeEditor() });
    this.editor.append(el('p', { class: 'card-subtitle' }, text('editHint')),
      el('div', { class: 'action-row' }, this.save, this.cancelEdit));
    this.buttons(); fields.prompt?.focus();
  }
  closeEditor() {
    this.editVersion = null; this.editor.hidden = true; this.editor.textContent = ''; this.save = this.cancelEdit = null;
    this.controller.edit(this.action.id, false); this.buttons(); this.edit.focus();
  }
}

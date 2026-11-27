import { api } from '../api.js';
import { button } from './buttons.js';
import { copyTextButton } from './clipboard.js';
import { el } from './dom.js';
import { t, getLanguage } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { toast } from './toast.js';
import { createModal } from './modal.js?v=web-studio-2j';

function displayLanguage() {
  return getLanguage().startsWith('zh') ? 'zh' : 'en';
}

function modeLabel(mode) {
  const safeMode = mode && mode !== 'null' ? mode : 'unknown';
  const key = `promptCopilot.mode.${safeMode}`;
  const label = t(key);
  return label === key ? t('promptCopilot.mode.unknown') : label;
}

function textBlock(className, value, fallback = '-') {
  return el('div', { class: className }, value || fallback);
}

function renderNotes(items) {
  const notes = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!notes.length) return el('p', { class: 'card-subtitle' }, t('promptCopilot.noNotes'));
  return el('ul', { class: 'prompt-copilot-notes' }, notes.map((item) => el('li', {}, item)));
}

function renderWarnings(items) {
  const warnings = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!warnings.length) return null;
  return el('div', { class: 'prompt-copilot-warning' },
    el('strong', {}, t('promptCopilot.warnings')),
    el('ul', {}, warnings.map((item) => el('li', {}, item))),
  );
}

function assistantModeLabel(status) {
  if (status?.llm_used) return t('promptCopilot.llmMode');
  if (status?.mode === 'llm_fallback') return t('promptCopilot.fallbackMode');
  return t('promptCopilot.localMode');
}

function timelineLabel(item) {
  const id = item.skill || item.tool || item.type || '-';
  const label = t(`promptCopilot.timeline.${id}`);
  return label === `promptCopilot.timeline.${id}` ? id : label;
}

function timelineStatus(item) {
  if (!item.status) return '';
  const label = t(`promptCopilot.timelineStatus.${item.status}`);
  return label === `promptCopilot.timelineStatus.${item.status}` ? item.status : label;
}

function renderTimeline(items) {
  const timeline = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!timeline.length) return null;
  return el('section', { class: 'prompt-copilot-section prompt-copilot-section-wide' },
    el('h3', {}, t('promptCopilot.timeline')),
    el('ul', { class: 'prompt-copilot-timeline' }, timeline.map((item) => {
      const summary = [timelineStatus(item), item.summary || ''].filter(Boolean).join(' · ');
      return el('li', {},
        el('span', { class: 'prompt-copilot-timeline-label' }, timelineLabel(item)),
        summary ? el('span', { class: 'prompt-copilot-timeline-summary' }, summary) : null,
      );
    })),
  );
}

function applyPrompt({ promptInput, result, modal, onApply }) {
  if (!modal.isActive() || !promptInput.isConnected || !result?.model_prompt_en) return;
  promptInput.value = result.model_prompt_en;
  promptInput.dispatchEvent(new Event('input', { bubbles: true }));
  let appliedControls = true;
  if (typeof onApply === 'function') {
    appliedControls = onApply(result) !== false;
  }
  toast(appliedControls ? t('promptCopilot.applied') : t('promptCopilot.appliedPromptOnly'), appliedControls ? 'success' : 'warning');
  modal.close();
  promptInput.focus();
}

function renderPreview({ modal, promptInput, originalPrompt, result, onApply, language }) {
  const { overlay } = modal;
  const body = overlay.querySelector('[data-prompt-copilot-body]');
  body.textContent = '';
  body.append(...[
    el('div', { class: 'prompt-copilot-summary' },
      el('span', {}, modeLabel(result.mode)),
      el('span', {}, ['image', 'video'].includes(result.input_summary?.media_type) ?
        t(`jobDisplay.media.${result.input_summary.media_type}`) : t('promptCopilot.mode.unknown')),
      el('span', {}, assistantModeLabel(result.assistant_status)),
    ),
    renderWarnings(result.warnings),
    el('div', { class: 'prompt-copilot-grid' },
      el('section', { class: 'prompt-copilot-section' },
        el('h3', {}, t('promptCopilot.original')),
        textBlock('prompt-copilot-text', originalPrompt),
      ),
      el('section', { class: 'prompt-copilot-section' },
        el('h3', {}, t('promptCopilot.preview')),
        textBlock('prompt-copilot-text', result.user_display_prompt || (language === 'zh' ? result.user_display_prompt_zh : '')),
      ),
      el('section', { class: 'prompt-copilot-section prompt-copilot-section-wide' },
        el('h3', {}, t('promptCopilot.englishPrompt')),
        textBlock('prompt-copilot-code', result.model_prompt_en),
      ),
      el('section', { class: 'prompt-copilot-section prompt-copilot-section-wide' },
        el('h3', {}, t('promptCopilot.notes')),
        renderNotes(result.notes || (language === 'zh' ? result.notes_zh : [])),
      ),
      renderTimeline(result.timeline),
    ),
  ].filter(Boolean));

  const footer = overlay.querySelector('[data-prompt-copilot-footer]');
  footer.textContent = '';
  footer.append(
    button(t('promptCopilot.keepOriginal'), { onClick: modal.close }),
    copyTextButton(result.model_prompt_en, { label: t('promptCopilot.copyEnglish'), isActive: modal.isActive }),
    button(t('promptCopilot.applyEnglish'), {
      variant: 'primary',
      onClick: () => applyPrompt({ promptInput, result, modal, onApply }),
    }),
  );
}

let activeCopilot = null;

export function openPromptCopilot({ promptInput, mediaType, onApply } = {}) {
  if (!promptInput?.isConnected) return;
  const originalPrompt = promptInput.value.trim();
  if (!originalPrompt) {
    toast(t('promptCopilot.promptRequired'), 'error');
    promptInput.focus();
    return;
  }

  if (activeCopilot?.isActive()) return;
  const modal = createModal({ title: t('promptCopilot.title'), className: 'prompt-copilot-modal', layerClassName: 'prompt-copilot-layer' });
  activeCopilot = modal;
  const language = displayLanguage();
  const body = el('div', { class: 'prompt-copilot-body', dataset: { promptCopilotBody: 'true' } });
  const footer = el('div', { class: 'action-row prompt-copilot-actions', dataset: { promptCopilotFooter: 'true' } });
  const close = button(t('promptCopilot.close'), { onClick: modal.close });
  let busy = false;

  async function requestSuggestion() {
    if (busy || !modal.isActive()) return;
    busy = true;
    body.setAttribute('aria-busy', 'true');
    body.textContent = '';
    body.append(el('p', { class: 'card-subtitle', role: 'status' }, t('promptCopilot.loading')));
    footer.textContent = '';
    footer.append(button(t('promptCopilot.close'), { onClick: modal.close }));
    if (!document.activeElement?.isConnected || document.activeElement === document.body) close.focus();
    // 重试仍针对打开时的原稿快照；只有明确应用才能写入仍存活的创作表单。
    try {
      const result = await api.post('/assistant/prompt-copilot', {
        prompt: originalPrompt,
        media_type: mediaType,
        language,
        target_language: 'en',
        strength: 'auto',
      });
      if (!modal.isActive()) return;
      if (typeof result?.model_prompt_en !== 'string' || !result.model_prompt_en.trim()) throw new Error();
      renderPreview({ modal, promptInput, originalPrompt, result, onApply, language });
    } catch (error) {
      if (!modal.isActive()) return;
      body.textContent = '';
      body.append(el('div', { class: 'diagnostic-card', role: 'alert' },
        el('p', { class: 'card-title' }, t('promptCopilot.error')),
        el('p', { class: 'card-subtitle' }, safeErrorMessage(error, t('promptCopilot.error'))),
      ));
      footer.textContent = '';
      footer.append(
        button(t('promptCopilot.keepOriginal'), { onClick: modal.close }),
        button(t('promptCopilot.retry'), { variant: 'primary', onClick: requestSuggestion }),
      );
    } finally {
      busy = false;
      if (modal.isActive()) {
        body.removeAttribute('aria-busy');
        // 重试按钮会随状态切换移除，焦点回到持续存在的关闭按钮。
        if (!document.activeElement?.isConnected || document.activeElement === document.body) close.focus();
      }
    }
  }

  modal.body.append(
    el('div', { class: 'prompt-copilot-header' },
      el('div', {},
        el('p', { class: 'kicker' }, t('assistantSettings.kicker')),
        el('h2', {}, t('promptCopilot.title')),
      ),
      close,
    ),
    el('p', { class: 'modal-copy' }, t('promptCopilot.copy')),
    body,
    footer,
  );
  modal.show(close);
  requestSuggestion();
}

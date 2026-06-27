import { api } from '../api.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { t, getLanguage } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { toast } from './toast.js';

function displayLanguage() {
  return getLanguage().startsWith('zh') ? 'zh' : 'en';
}

function modeLabel(mode) {
  const key = `promptCopilot.mode.${mode || 'unknown'}`;
  const label = t(key);
  return label === key ? (mode || '-') : label;
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

function closeOverlay(overlay) {
  overlay.remove();
}

function applyPrompt({ promptInput, result, overlay }) {
  if (!result?.model_prompt_en) return;
  promptInput.value = result.model_prompt_en;
  promptInput.dispatchEvent(new Event('input', { bubbles: true }));
  toast(t('promptCopilot.applied'), 'success');
  closeOverlay(overlay);
  promptInput.focus();
}

async function copyEnglish(result) {
  try {
    await navigator.clipboard.writeText(result?.model_prompt_en || '');
    toast(t('common.copied'), 'success');
  } catch (_) {
    toast(t('common.copyFailed'), 'error');
  }
}

function renderPreview({ overlay, promptInput, originalPrompt, result }) {
  const body = overlay.querySelector('[data-prompt-copilot-body]');
  body.textContent = '';
  body.append(
    el('div', { class: 'prompt-copilot-summary' },
      el('span', {}, modeLabel(result.mode)),
      el('span', {}, result.input_summary?.media_type || '-'),
      el('span', {}, t('promptCopilot.localMode')),
    ),
    renderWarnings(result.warnings),
    el('div', { class: 'prompt-copilot-grid' },
      el('section', { class: 'prompt-copilot-section' },
        el('h3', {}, t('promptCopilot.original')),
        textBlock('prompt-copilot-text', originalPrompt),
      ),
      el('section', { class: 'prompt-copilot-section' },
        el('h3', {}, t('promptCopilot.zhPreview')),
        textBlock('prompt-copilot-text', result.user_display_prompt_zh),
      ),
      el('section', { class: 'prompt-copilot-section prompt-copilot-section-wide' },
        el('h3', {}, t('promptCopilot.englishPrompt')),
        textBlock('prompt-copilot-code', result.model_prompt_en),
      ),
      el('section', { class: 'prompt-copilot-section prompt-copilot-section-wide' },
        el('h3', {}, t('promptCopilot.notes')),
        renderNotes(result.notes_zh),
      ),
    ),
  );

  const footer = overlay.querySelector('[data-prompt-copilot-footer]');
  footer.textContent = '';
  footer.append(
    button(t('promptCopilot.keepOriginal'), { onClick: () => closeOverlay(overlay) }),
    button(t('promptCopilot.copyEnglish'), { onClick: () => copyEnglish(result) }),
    button(t('promptCopilot.applyEnglish'), {
      variant: 'primary',
      onClick: () => applyPrompt({ promptInput, result, overlay }),
    }),
  );
}

export function openPromptCopilot({ promptInput, mediaType }) {
  const originalPrompt = promptInput.value.trim();
  if (!originalPrompt) {
    toast(t('promptCopilot.promptRequired'), 'error');
    promptInput.focus();
    return;
  }

  const overlay = el('div', { class: 'modal-overlay prompt-copilot-layer' });
  const body = el('div', { class: 'prompt-copilot-body', dataset: { promptCopilotBody: 'true' } },
    el('p', { class: 'card-subtitle' }, t('promptCopilot.loading')),
  );
  const footer = el('div', { class: 'action-row prompt-copilot-actions', dataset: { promptCopilotFooter: 'true' } },
    button(t('promptCopilot.close'), { onClick: () => closeOverlay(overlay) }),
  );
  overlay.appendChild(el('div', { class: 'modal prompt-copilot-modal', role: 'dialog', ariaModal: 'true' },
    el('div', { class: 'prompt-copilot-header' },
      el('div', {},
        el('p', { class: 'kicker' }, 'PROMPT COPILOT'),
        el('h2', {}, t('promptCopilot.title')),
      ),
      button(t('promptCopilot.close'), { onClick: () => closeOverlay(overlay) }),
    ),
    el('p', { class: 'modal-copy' }, t('promptCopilot.copy')),
    body,
    footer,
  ));
  document.body.appendChild(overlay);

  api.post('/prompt/enhance', {
    prompt: originalPrompt,
    media_type: mediaType,
    language: displayLanguage(),
    target_language: 'en',
    strength: 'auto',
  }).then((result) => {
    renderPreview({ overlay, promptInput, originalPrompt, result });
  }).catch((error) => {
    body.textContent = '';
    body.appendChild(el('div', { class: 'diagnostic-card' },
      el('p', { class: 'card-title' }, t('promptCopilot.error')),
      el('p', { class: 'card-subtitle' }, safeErrorMessage(error, t('promptCopilot.error'))),
    ));
  });
}

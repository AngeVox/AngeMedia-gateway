import { api } from '../api.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { t, getLanguage } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { toast } from './toast.js';
import { navigate } from '../router.js';
import { openAssistantSettings } from './assistant-settings.js?v=web-studio-2h';

const APPLY_KEY = 'studio_assistant_plan_apply';

function displayLanguage() {
  return getLanguage().startsWith('zh') ? 'zh' : 'en';
}

function closeOverlay(overlay) {
  overlay.remove();
}

function targetHash(targetPage) {
  if (targetPage === 'generate-video') return '#/generate/video';
  return '#/generate/image';
}

function mediaTypeForPage(targetPage) {
  return targetPage === 'generate-video' ? 'video' : 'image';
}

function currentPagePromptInput(targetMediaType) {
  const expectedHash = targetMediaType === 'video' ? '#/generate/video' : '#/generate/image';
  if ((location.hash || '#/dashboard') !== expectedHash) return null;
  return document.querySelector('textarea[name="prompt"]');
}

function statusLabel(result) {
  const status = result.assistant_status || {};
  if (status.mode === 'config_error') return t('assistantPlan.configErrorMode');
  if (status.llm_used) return t('assistantPlan.llmMode');
  if (status.llm_configured && status.llm_enabled) return t('assistantPlan.localModeLlmAvailable');
  return t('assistantPlan.localMode');
}

function textBlock(className, value, fallback = '-') {
  return el('div', { class: className }, value || fallback);
}

function renderSteps(items) {
  const steps = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!steps.length) return el('p', { class: 'card-subtitle' }, t('assistantPlan.noSteps'));
  return el('ol', { class: 'assistant-plan-steps' }, steps.map((item) => el('li', {}, item)));
}

function applyPlan({ result, promptInput, sourceMediaType, overlay }) {
  const prompt = result.prompt?.model_prompt_en || '';
  if (!prompt) {
    toast(t('assistantPlan.noPrompt'), 'error');
    return;
  }
  const targetPage = result.route?.target_page || 'generate-image';
  const targetMediaType = mediaTypeForPage(targetPage);
  const directInput = (promptInput && sourceMediaType === targetMediaType) ?
    promptInput :
    currentPagePromptInput(targetMediaType);
  if (directInput) {
    directInput.value = prompt;
    directInput.dispatchEvent(new Event('input', { bubbles: true }));
    toast(t('assistantPlan.applied'), 'success');
    closeOverlay(overlay);
    directInput.focus();
    return;
  }
  sessionStorage.setItem('studio_assistant_plan_apply', JSON.stringify({
    prompt,
    media_type: targetMediaType,
    target_page: targetPage,
    created_at: Date.now(),
  }));
  closeOverlay(overlay);
  const hash = targetHash(targetPage);
  if ((location.hash || '#/dashboard') === hash) {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else {
    navigate(hash);
  }
}

async function copyEnglish(result) {
  try {
    await navigator.clipboard.writeText(result.prompt?.model_prompt_en || '');
    toast(t('common.copied'), 'success');
  } catch (_) {
    toast(t('common.copyFailed'), 'error');
  }
}

function renderPlan({ overlay, result, promptInput, sourceMediaType }) {
  const body = overlay.querySelector('[data-assistant-plan-body]');
  body.textContent = '';
  body.append(
    el('div', { class: 'assistant-plan-summary' },
      el('span', {}, statusLabel(result)),
      el('span', {}, result.media_type || '-'),
      el('span', {}, t('assistantPlan.noAutoGenerate')),
    ),
    el('div', { class: 'assistant-plan-grid' },
      el('section', { class: 'assistant-plan-section assistant-plan-section-wide' },
        el('h3', {}, t('assistantPlan.message')),
        textBlock('prompt-copilot-text', result.assistant_message),
      ),
      el('section', { class: 'assistant-plan-section' },
        el('h3', {}, t('assistantPlan.route')),
        textBlock('prompt-copilot-text', result.route?.reason || result.route?.reason_zh),
      ),
      el('section', { class: 'assistant-plan-section' },
        el('h3', {}, t('assistantPlan.steps')),
        renderSteps(result.work_steps),
      ),
      el('section', { class: 'assistant-plan-section' },
        el('h3', {}, t('assistantPlan.zhPreview')),
        textBlock('prompt-copilot-text', result.prompt?.user_display_prompt_zh),
      ),
      el('section', { class: 'assistant-plan-section assistant-plan-section-wide' },
        el('h3', {}, t('assistantPlan.englishPrompt')),
        textBlock('prompt-copilot-code', result.prompt?.model_prompt_en),
      ),
    ),
  );

  const footer = overlay.querySelector('[data-assistant-plan-footer]');
  footer.textContent = '';
  const actionLabel = result.media_type === 'video' ? t('assistantPlan.applyToVideo') : t('assistantPlan.applyToImage');
  footer.append(
    button(t('assistantPlan.settings'), { onClick: openAssistantSettings }),
    button(t('assistantPlan.close'), { onClick: () => closeOverlay(overlay) }),
    button(t('assistantPlan.copyEnglish'), { onClick: () => copyEnglish(result) }),
    button(actionLabel, {
      variant: 'primary',
      onClick: () => applyPlan({ result, promptInput, sourceMediaType, overlay }),
    }),
  );
}

export function openAssistantPlanner({ initialMessage = '', currentPage = '', promptInput = null, mediaType = 'auto' } = {}) {
  const overlay = el('div', { class: 'modal-overlay assistant-plan-layer' });
  const messageInput = el('textarea', {
    class: 'assistant-plan-input',
    maxLength: 4000,
    rows: 4,
    value: initialMessage || promptInput?.value?.trim() || '',
    placeholder: t('assistantPlan.placeholder'),
  });
  const body = el('div', { class: 'assistant-plan-body', dataset: { assistantPlanBody: 'true' } },
    el('p', { class: 'card-subtitle' }, t('assistantPlan.copy')),
  );
  const submit = button(t('assistantPlan.request'), { variant: 'primary' });
  const footer = el('div', { class: 'action-row assistant-plan-actions', dataset: { assistantPlanFooter: 'true' } },
    button(t('assistantPlan.settings'), { onClick: openAssistantSettings }),
    button(t('assistantPlan.close'), { onClick: () => closeOverlay(overlay) }),
    submit,
  );

  async function requestPlan() {
    const message = messageInput.value.trim();
    if (!message) {
      toast(t('assistantPlan.messageRequired'), 'error');
      messageInput.focus();
      return;
    }
    submit.disabled = true;
    submit.textContent = t('assistantPlan.loading');
    body.textContent = '';
    body.appendChild(el('p', { class: 'card-subtitle' }, t('assistantPlan.loading')));
    try {
      const result = await api.post('/assistant/plan', {
        message,
        media_type: mediaType,
        language: displayLanguage(),
        target_prompt_language: 'en',
        context: {
          current_page: currentPage,
          current_prompt: promptInput?.value || '',
        },
      });
      renderPlan({ overlay, result, promptInput, sourceMediaType: mediaType });
    } catch (error) {
      body.textContent = '';
      body.appendChild(el('div', { class: 'diagnostic-card' },
        el('p', { class: 'card-title' }, t('assistantPlan.error')),
        el('p', { class: 'card-subtitle' }, safeErrorMessage(error, t('assistantPlan.error'))),
      ));
    } finally {
      submit.disabled = false;
      submit.textContent = t('assistantPlan.request');
    }
  }

  submit.addEventListener('click', requestPlan);

  overlay.appendChild(el('div', { class: 'modal assistant-plan-modal', role: 'dialog', ariaModal: 'true' },
    el('div', { class: 'prompt-copilot-header' },
      el('div', {},
        el('p', { class: 'kicker' }, 'ANGE ASSISTANT'),
        el('h2', {}, t('assistantPlan.title')),
      ),
      button(t('assistantPlan.close'), { onClick: () => closeOverlay(overlay) }),
    ),
    el('p', { class: 'modal-copy' }, t('assistantPlan.copy')),
    el('label', { class: 'field-label' }, t('assistantPlan.inputLabel')),
    messageInput,
    body,
    footer,
  ));
  document.body.appendChild(overlay);
  messageInput.focus();
}

export function applyAssistantPlanPrefill(promptInput, mediaType) {
  let payload = null;
  try {
    payload = JSON.parse(sessionStorage.getItem(APPLY_KEY) || 'null');
  } catch (_) {
    payload = null;
  }
  if (!payload || payload.media_type !== mediaType || !payload.prompt) return;
  sessionStorage.removeItem(APPLY_KEY);
  promptInput.value = payload.prompt;
  promptInput.dispatchEvent(new Event('input', { bubbles: true }));
  toast(t('assistantPlan.applied'), 'success');
}

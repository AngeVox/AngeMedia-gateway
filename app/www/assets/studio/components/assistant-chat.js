import { api } from '../api.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { t, getLanguage } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { toast } from './toast.js';
import { openAssistantSettings } from './assistant-settings.js?v=web-studio-2h';

function displayLanguage() {
  return getLanguage().startsWith('zh') ? 'zh' : 'en';
}

function closeOverlay(overlay) {
  overlay.remove();
}

function timelineLabel(item) {
  const id = item.tool || item.skill || item.type || '-';
  return t(`assistantChat.timeline.${id}`);
}

function timelineSummary(item) {
  const id = item.tool || item.skill || item.type || '-';
  const status = item.status ? t(`assistantChat.timelineStatus.${item.status}`) : '';
  const summary = item.summary || '';
  return [status, summary].filter(Boolean).join(' · ');
}

function renderTimeline(items) {
  const timeline = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!timeline.length) return null;
  return el('ul', { class: 'assistant-chat-timeline' }, timeline.map((item) => (
    el('li', {},
      el('span', { class: 'assistant-chat-tool' }, timelineLabel(item)),
      el('span', {}, timelineSummary(item)),
    )
  )));
}

function renderMessages(target, messages, timeline) {
  const safeMessages = Array.isArray(messages) ? messages : [];
  target.textContent = '';
  if (!safeMessages.length) {
    target.appendChild(el('div', { class: 'assistant-chat-empty' },
      el('p', { class: 'card-title' }, t('assistantChat.emptyTitle')),
      el('p', { class: 'card-subtitle' }, t('assistantChat.emptyCopy')),
    ));
    return;
  }
  safeMessages.forEach((message) => {
    target.appendChild(el('article', { class: `assistant-chat-message assistant-chat-message-${message.role}` },
      el('p', { class: 'assistant-chat-role' }, message.role === 'user' ? t('assistantChat.user') : t('assistantChat.assistant')),
      el('div', { class: 'assistant-chat-content' }, message.content || ''),
      message.role === 'assistant' ? renderTimeline(timeline) : null,
    ));
  });
  target.scrollTop = target.scrollHeight;
}

export function openAssistantChat() {
  const overlay = el('div', { class: 'modal-overlay assistant-chat-layer' });
  let sessionId = null;
  let latestTimeline = [];
  const messagesTarget = el('div', { class: 'assistant-chat-messages' });
  const input = el('textarea', {
    class: 'assistant-chat-input',
    rows: 3,
    maxLength: 4000,
    placeholder: t('assistantChat.placeholder'),
  });
  const status = el('p', { class: 'assistant-chat-status' }, t('assistantChat.scope'));
  const send = button(t('assistantChat.send'), { variant: 'primary' });

  async function submit() {
    const message = input.value.trim();
    if (!message) {
      toast(t('assistantChat.messageRequired'), 'error');
      input.focus();
      return;
    }
    send.disabled = true;
    send.textContent = t('assistantChat.sending');
    status.textContent = t('assistantChat.sending');
    try {
      const result = await api.post('/assistant/chat', {
        session_id: sessionId,
        message,
        language: displayLanguage(),
      });
      sessionId = result.session_id || sessionId;
      latestTimeline = result.timeline || [];
      input.value = '';
      status.textContent = result.status === 'refused' ? t('assistantChat.refused') : t('assistantChat.ready');
      renderMessages(messagesTarget, result.messages, latestTimeline);
    } catch (error) {
      status.textContent = safeErrorMessage(error, t('assistantChat.error'));
    } finally {
      send.disabled = false;
      send.textContent = t('assistantChat.send');
    }
  }

  send.addEventListener('click', submit);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  overlay.appendChild(el('div', { class: 'modal assistant-chat-modal', role: 'dialog', ariaModal: 'true' },
    el('div', { class: 'prompt-copilot-header' },
      el('div', {},
        el('p', { class: 'kicker' }, 'ANGE ASSISTANT'),
        el('h2', {}, t('assistantChat.title')),
      ),
      el('div', { class: 'action-row assistant-chat-header-actions' },
        button(t('assistantChat.settings'), { onClick: openAssistantSettings }),
        button(t('common.close'), { onClick: () => closeOverlay(overlay) }),
      ),
    ),
    el('p', { class: 'modal-copy' }, t('assistantChat.copy')),
    messagesTarget,
    status,
    el('div', { class: 'assistant-chat-composer' },
      input,
      el('div', { class: 'action-row assistant-chat-actions' },
        button(t('assistantChat.settings'), { onClick: openAssistantSettings }),
        button(t('common.close'), { onClick: () => closeOverlay(overlay) }),
        send,
      ),
    ),
  ));
  document.body.appendChild(overlay);
  renderMessages(messagesTarget, [], latestTimeline);
  input.focus();
}

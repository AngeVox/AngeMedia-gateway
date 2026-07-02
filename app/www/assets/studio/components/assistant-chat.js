import { api } from '../api.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { t, getLanguage } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { toast } from './toast.js';
import { openAssistantSettings } from './assistant-settings.js?v=web-studio-2h';

const SESSION_STORAGE_KEY = 'studio_assistant_chat_session_id';

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
  let sessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;
  let latestTimeline = [];
  let messages = [];
  let pendingTimer = null;
  let pendingStartedAt = 0;
  const messagesTarget = el('div', { class: 'assistant-chat-messages' });
  const input = el('textarea', {
    class: 'assistant-chat-input',
    rows: 3,
    maxLength: 4000,
    placeholder: t('assistantChat.placeholder'),
  });
  const status = el('p', { class: 'assistant-chat-status' }, t('assistantChat.scope'));
  const send = button(t('assistantChat.send'), { variant: 'primary' });

  function closeChat() {
    stopPendingTimer();
    closeOverlay(overlay);
  }

  function setMessages(nextMessages, timeline = latestTimeline) {
    messages = Array.isArray(nextMessages) ? nextMessages : [];
    renderMessages(messagesTarget, messages, timeline);
  }

  function stopPendingTimer() {
    if (pendingTimer) window.clearTimeout(pendingTimer);
    pendingTimer = null;
  }

  function updatePendingStatus() {
    const seconds = Math.max(0, Math.floor((Date.now() - pendingStartedAt) / 1000));
    status.textContent = t('assistantChat.waiting').replace('{seconds}', String(seconds));
    pendingTimer = window.setTimeout(updatePendingStatus, 1000);
  }

  async function restoreSession() {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    try {
      const result = await api.get(`/admin/assistant/sessions/${encodeURIComponent(sessionId)}`);
      setMessages(result?.messages || []);
      status.textContent = t('assistantChat.ready');
    } catch (_) {
      localStorage.removeItem(SESSION_STORAGE_KEY);
      sessionId = null;
      setMessages([]);
    }
  }

  async function submit() {
    const message = input.value.trim();
    if (!message) {
      toast(t('assistantChat.messageRequired'), 'error');
      input.focus();
      return;
    }
    send.disabled = true;
    send.textContent = t('assistantChat.waitingButton');
    input.value = '';
    pendingStartedAt = Date.now();
    setMessages([
      ...messages,
      { id: `local-user-${pendingStartedAt}`, role: 'user', content: message },
      { id: `local-pending-${pendingStartedAt}`, role: 'assistant', content: t('assistantChat.pendingReply') },
    ], []);
    updatePendingStatus();
    try {
      const result = await api.post('/assistant/chat', {
        session_id: sessionId,
        message,
        language: displayLanguage(),
      });
      sessionId = result.session_id || sessionId;
      if (sessionId) localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
      latestTimeline = result.timeline || [];
      status.textContent = result.status === 'refused' ? t('assistantChat.refused') : t('assistantChat.ready');
      setMessages(result.messages, latestTimeline);
    } catch (error) {
      status.textContent = safeErrorMessage(error, t('assistantChat.error'));
      setMessages(messages.filter((item) => !String(item.id || '').startsWith('local-pending-')), []);
    } finally {
      stopPendingTimer();
      send.disabled = false;
      send.textContent = t('assistantChat.send');
      input.focus();
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
        button(t('common.close'), { onClick: closeChat }),
      ),
    ),
    el('p', { class: 'modal-copy' }, t('assistantChat.copy')),
    messagesTarget,
    status,
    el('div', { class: 'assistant-chat-composer' },
      input,
      el('div', { class: 'action-row assistant-chat-actions' },
        button(t('assistantChat.settings'), { onClick: openAssistantSettings }),
        button(t('common.close'), { onClick: closeChat }),
        send,
      ),
    ),
  ));
  document.body.appendChild(overlay);
  restoreSession();
  input.focus();
}

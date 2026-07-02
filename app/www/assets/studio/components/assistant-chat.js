import { api } from '../api.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { confirmModal } from './modal.js?v=web-studio-2h';
import { t, getLanguage } from '../i18n.js';
import { safeErrorMessage } from '../lib/safe-error.js';
import { safeText } from '../lib/security.js';
import { toast } from './toast.js';
import { openAssistantSettings } from './assistant-settings.js?v=web-studio-2h';

const SESSION_STORAGE_KEY = 'studio_assistant_chat_session_id';
const SESSION_LIMIT = 30;

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
  const status = item.status ? t(`assistantChat.timelineStatus.${item.status}`) : '';
  const summary = safeText(item.summary || '', 180);
  return [status, summary].filter(Boolean).join(' · ');
}

function renderTimeline(items) {
  const timeline = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!timeline.length) return null;
  return el('details', { class: 'assistant-chat-timeline-panel' },
    el('summary', {}, t('assistantChat.timelineTitle')),
    el('ul', { class: 'assistant-chat-timeline' }, timeline.map((item) => (
      el('li', {},
        el('span', { class: 'assistant-chat-tool' }, timelineLabel(item)),
        el('span', {}, timelineSummary(item)),
      )
    ))),
  );
}

function renderPlainContent(content) {
  const text = safeText(content || '', 8000);
  const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return el('p', {}, '-');
  return el('div', { class: 'assistant-chat-content' },
    lines.map((line) => el('p', {}, line)),
  );
}

function renderMessages(target, messages, latestTimeline) {
  const safeMessages = Array.isArray(messages) ? messages : [];
  target.textContent = '';
  if (!safeMessages.length) {
    target.appendChild(el('div', { class: 'assistant-chat-empty' },
      el('p', { class: 'card-title' }, t('assistantChat.emptyTitle')),
      el('p', { class: 'card-subtitle' }, t('assistantChat.emptyCopy')),
    ));
    return;
  }
  safeMessages.forEach((message, index) => {
    const isLastAssistant = message.role === 'assistant' && index === safeMessages.length - 1;
    target.appendChild(el('article', { class: `assistant-chat-message assistant-chat-message-${message.role}` },
      el('p', { class: 'assistant-chat-role' }, message.role === 'user' ? t('assistantChat.user') : t('assistantChat.assistant')),
      renderPlainContent(message.content || ''),
      isLastAssistant ? renderTimeline(latestTimeline) : null,
    ));
  });
  target.scrollTop = target.scrollHeight;
}

function sessionTitle(item) {
  return safeText(item?.title || t('assistantChat.untitledSession'), 64);
}

function renderSessions(target, sessions, activeSessionId, { openSession, deleteSession }) {
  target.textContent = '';
  if (!sessions.length) {
    target.appendChild(el('p', { class: 'card-subtitle assistant-chat-session-empty' }, t('assistantChat.noSessions')));
    return;
  }
  sessions.forEach((session) => {
    const row = el('article', {
      class: `assistant-chat-session ${session.id === activeSessionId ? 'is-active' : ''}`,
    },
      el('button', {
        type: 'button',
        class: 'assistant-chat-session-main',
        onclick: () => openSession(session.id),
      },
        el('strong', {}, sessionTitle(session)),
        el('span', {}, safeText(session.updated_at || session.created_at || '', 32)),
      ),
      button(t('assistantChat.deleteSession'), {
        size: 'sm',
        variant: 'danger',
        onClick: () => deleteSession(session.id),
      }),
    );
    target.appendChild(row);
  });
}

function parseSseLines(buffer, onEvent) {
  const parts = buffer.split('\n\n');
  const rest = parts.pop() || '';
  parts.forEach((part) => {
    let event = 'message';
    let data = '';
    part.split('\n').forEach((line) => {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      if (line.startsWith('data:')) data += line.slice(5).trim();
    });
    if (!data) return;
    try {
      onEvent(event, JSON.parse(data));
    } catch (_) {
      onEvent(event, { content: data });
    }
  });
  return rest;
}

export function openAssistantChat() {
  const overlay = el('div', { class: 'modal-overlay assistant-chat-layer' });
  let sessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;
  let latestTimeline = [];
  let messages = [];
  let sessions = [];
  let pendingTimer = null;
  let pendingStartedAt = 0;
  let pendingContent = '';

  const sessionTarget = el('div', { class: 'assistant-chat-sessions' });
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

  async function loadSessions() {
    const result = await api.get(`/admin/assistant/sessions?limit=${SESSION_LIMIT}&offset=0`);
    sessions = Array.isArray(result?.items) ? result.items : [];
    renderSessions(sessionTarget, sessions, sessionId, { openSession, deleteSession });
  }

  async function openSession(nextSessionId) {
    if (!nextSessionId) return;
    sessionId = nextSessionId;
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    latestTimeline = [];
    status.textContent = t('assistantChat.loadingSession');
    const result = await api.get(`/admin/assistant/sessions/${encodeURIComponent(sessionId)}`);
    setMessages(result?.messages || []);
    status.textContent = t('assistantChat.ready');
    renderSessions(sessionTarget, sessions, sessionId, { openSession, deleteSession });
  }

  function newSession() {
    sessionId = null;
    latestTimeline = [];
    localStorage.removeItem(SESSION_STORAGE_KEY);
    setMessages([]);
    status.textContent = t('assistantChat.newSessionReady');
    renderSessions(sessionTarget, sessions, sessionId, { openSession, deleteSession });
    input.focus();
  }

  function deleteSession(targetSessionId) {
    confirmModal({
      title: t('assistantChat.deleteSessionTitle'),
      message: t('assistantChat.deleteSessionMessage'),
      confirmLabel: t('assistantChat.deleteSessionConfirm'),
      cancelLabel: t('common.cancel'),
      danger: true,
      onConfirm: async () => {
        await api.delete(`/admin/assistant/sessions/${encodeURIComponent(targetSessionId)}`);
        if (sessionId === targetSessionId) {
          sessionId = null;
          localStorage.removeItem(SESSION_STORAGE_KEY);
          setMessages([]);
        }
        await loadSessions();
        if (!sessionId && sessions[0]) await openSession(sessions[0].id);
        status.textContent = t('assistantChat.sessionDeleted');
      },
    });
  }

  async function restoreSession() {
    await loadSessions();
    if (sessionId && sessions.some((item) => item.id === sessionId)) {
      await openSession(sessionId);
      return;
    }
    sessionId = sessions[0]?.id || null;
    if (sessionId) {
      localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
      await openSession(sessionId);
      return;
    }
    setMessages([]);
  }

  function setPendingMessages(message) {
    pendingStartedAt = Date.now();
    pendingContent = '';
    setMessages([
      ...messages,
      { id: `local-user-${pendingStartedAt}`, role: 'user', content: message },
      { id: `local-pending-${pendingStartedAt}`, role: 'assistant', content: t('assistantChat.pendingReply') },
    ], []);
    updatePendingStatus();
  }

  function updatePendingContent(content) {
    pendingContent += content;
    setMessages([
      ...messages.filter((item) => !String(item.id || '').startsWith('local-pending-')),
      { id: `local-pending-${pendingStartedAt}`, role: 'assistant', content: pendingContent || t('assistantChat.pendingReply') },
    ], latestTimeline);
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
    latestTimeline = [];
    setPendingMessages(message);
    try {
      const response = await fetch('/v1/assistant/chat/stream', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message, language: displayLanguage() }),
      });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let streamSessionId = sessionId;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = parseSseLines(buffer, (event, payload) => {
          if (payload.session_id) streamSessionId = payload.session_id;
          if (event === 'chunk' && payload.content) updatePendingContent(payload.content);
          if (event === 'timeline') latestTimeline = payload.items || [];
          if (event === 'error') throw new Error(payload.message || t('assistantChat.error'));
        });
      }
      if (streamSessionId) {
        sessionId = streamSessionId;
        localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
        await openSession(sessionId);
        await loadSessions();
      }
      status.textContent = t('assistantChat.ready');
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
        button(t('assistantChat.newSession'), { onClick: newSession }),
        button(t('assistantChat.settings'), { onClick: openAssistantSettings }),
        button(t('common.close'), { onClick: closeChat }),
      ),
    ),
    el('p', { class: 'modal-copy' }, t('assistantChat.copy')),
    el('div', { class: 'assistant-chat-shell' },
      el('aside', { class: 'assistant-chat-sidebar' },
        el('div', { class: 'assistant-chat-sidebar-head' },
          el('strong', {}, t('assistantChat.history')),
          button(t('assistantChat.newSession'), { size: 'sm', onClick: newSession }),
        ),
        sessionTarget,
      ),
      el('section', { class: 'assistant-chat-main' },
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
      ),
    ),
  ));
  document.body.appendChild(overlay);
  restoreSession().catch((error) => {
    status.textContent = safeErrorMessage(error, t('assistantChat.error'));
    setMessages([]);
  });
  input.focus();
}

import { api } from '../api.js';
import { button } from './buttons.js';
import { el } from './dom.js';
import { confirmModal, containModalFocus, lockModalScroll } from './modal.js?v=web-studio-2j';
import { t, getLanguage } from '../i18n.js';
import { safeText, redactInlineSecret } from '../lib/security.js';
import { AssistantChatController } from '../lib/assistant-chat-controller.js';
import { isTerminalRun } from '../lib/assistant-run-observer.js';
import { runText, runSummary } from '../lib/assistant-run-copy.js';
import { createAssistantSettings } from './assistant-settings.js?v=web-studio-2j';
import { assistantActions } from './assistant-actions.js';
import { assistantProgress } from './assistant-progress.js';

// 仅识别同源作品路径；HTML、外链和带签名参数的地址仍是普通文本。
function contentNode(value) {
  const node = el('div', { class: 'assistant-chat-content' });
  const text = redactInlineSecret(value || '').slice(0, 8000);
  const pattern = /\/(?:generated|uploads)\/[A-Za-z0-9_./%-]+/g;
  let start = 0;
  for (const match of text.matchAll(pattern)) {
    node.append(document.createTextNode(text.slice(start, match.index)));
    const path = match[0];
    const unsafe = text[match.index - 1]?.match(/[A-Za-z0-9:/]/) || /[?#]/.test(text[match.index + path.length] || '')
      || /(?:^|\/)\.{1,2}(?:\/|$)|%2e|%2f|%5c/i.test(path);
    node.append(unsafe ? document.createTextNode(path) : el('a', { href: path, target: '_blank', rel: 'noopener noreferrer' }, path));
    start = match.index + path.length;
  }
  node.append(document.createTextNode(text.slice(start))); return node;
}

export function openAssistantChat() {
  const existing = document.querySelector('[data-assistant-dialog]');
  if (existing) {
    [...existing.querySelectorAll('button,textarea')].find((node) => !node.disabled && node.getClientRects().length)?.focus();
    return;
  }
  const previousFocus = document.activeElement;
  const releaseScroll = lockModalScroll();
  const overlay = el('div', { class: 'modal-overlay', dataset: { assistantDialog: 'true' } });
  const sessionTarget = el('div', { class: 'assistant-chat-sessions' });
  const messages = el('div', { class: 'assistant-chat-messages', role: 'log', ariaLabel: t('assistantChat.history') });
  const progress = el('div', { class: 'assistant-chat-progress' });
  const status = el('p', { class: 'assistant-chat-status', role: 'status', ariaLive: 'polite' });
  const input = el('textarea', { class: 'assistant-chat-input', rows: 2, maxLength: 8000,
    placeholder: t('assistantChat.placeholder'), ariaLabel: t('assistantChat.placeholder') });
  const older = button(runText('older'), { size: 'sm', onClick: () => controller.older() });
  const send = button(t('assistantChat.send'), { variant: 'primary', onClick: submit });
  const stop = button(runText('stop'), { variant: 'danger', onClick: () => controller.cancel() });
  const retry = button(runText('retry'), { onClick: () => controller.retry() });
  const fork = button(runText('copyLegacy'), { onClick: () => controller.copyLegacy() });
  const fresh = button(t('assistantChat.newSession'), { size: 'sm', onClick: () => controller.fresh() });
  const knowledge = button(runText('knowledge'), { onClick: () => controller.knowledge(input.value) });
  const knowledgeTarget = el('div', { class: 'assistant-chat-knowledge' });
  const controller = new AssistantChatController(api, sessionStorage, render, getLanguage().startsWith('zh') ? 'zh' : 'en');
  const proposals = assistantActions(api, (error) => controller.fail(error), close);
  let lastMessages = '', lastSessions = '', lastProgress = '';
  let settingsView = null, closed = false;
  const settingsTarget = el('div', { class: 'assistant-chat-settings', hidden: true });
  const settingsButton = button(t('assistantChat.settings'), { onClick: showSettings });

  function showSettings() {
    if (settingsView) return;
    settingsView = createAssistantSettings({
      onClose: hideSettings,
      closeLabel: t('assistantSettings.backToChat'),
      onSaved: () => { if (!closed) return controller.refreshRuntime(); },
    });
    [...dialog.children].forEach((node) => { node.hidden = true; });
    settingsTarget.replaceChildren(settingsView.node); settingsTarget.hidden = false;
    dialog.classList.add('is-settings');
    dialog.removeAttribute('aria-labelledby'); dialog.setAttribute('aria-label', t('assistantSettings.title'));
    settingsView.focus();
  }
  function hideSettings() {
    settingsView?.dispose(); settingsView = null;
    settingsTarget.replaceChildren();
    [...dialog.children].forEach((node) => { node.hidden = false; });
    settingsTarget.hidden = true; dialog.classList.remove('is-settings');
    dialog.removeAttribute('aria-label'); dialog.setAttribute('aria-labelledby', 'assistant-dialog-title');
    if (settingsButton.isConnected) settingsButton.focus();
  }

  function submit() {
    const value = input.value;
    const task = controller.submit(value);
    if (controller.state.pending?.message === value.trim()) input.value = '';
    task.catch((error) => controller.fail(error));
  }
  function render(state) {
    proposals.setContext(state.owner, state.session?.id, state.legacyMode);
    const locked = state.runtime.state === 'authentication_expired';
    const busy = state.loading || state.submitting || !!state.pending || locked;
    const running = state.run && !isTerminalRun(state.run);
    send.disabled = busy || !!running || state.legacyMode || !state.runtime.can_submit;
    input.disabled = busy || state.legacyMode;
    fresh.disabled = state.loading || state.submitting || !!state.pending || locked;
    stop.hidden = !running || state.legacyMode; stop.disabled = state.submitting;
    retry.hidden = !state.pending && !state.disconnected; retry.disabled = state.submitting;
    retry.textContent = runText(state.pending ? 'retry' : 'reconnect');
    fork.hidden = !state.legacyMode; fork.disabled = state.submitting;
    older.hidden = !state.hasOlder || state.legacyMode; older.disabled = state.loading;
    knowledge.hidden = state.runtime.can_submit || state.legacyMode;
    knowledge.disabled = state.loading || !state.owner || state.error === 'authentication_expired';
    status.textContent = state.legacyMode ? runText('legacyHint') : runSummary(state);
    const sessionKey = JSON.stringify([state.sessions, state.legacy, state.session?.id, busy]);
    if (sessionKey !== lastSessions) {
      lastSessions = sessionKey; sessionTarget.textContent = '';
      for (const [title, list, legacyMode] of [[runText('personal'), state.sessions, false], [runText('legacy'), state.legacy, true]]) {
        sessionTarget.append(el('strong', { class: 'assistant-chat-history-label' }, title));
        if (!list.length) sessionTarget.append(el('p', { class: 'card-subtitle' }, t('assistantChat.noSessions')));
        list.forEach((item) => sessionTarget.append(el('article', { class: `assistant-chat-session ${item.id === state.session?.id ? 'is-active' : ''}` },
          el('button', { type: 'button', class: 'assistant-chat-session-main', disabled: busy,
            onclick: () => controller.open(item.id, legacyMode) }, el('strong', {}, safeText(item.title, 120))),
          legacyMode ? null : button(t('assistantChat.deleteSession'), { size: 'sm', variant: 'danger', disabled: busy,
            onClick: () => confirmModal({ title: t('assistantChat.deleteSessionTitle'), message: t('assistantChat.deleteSessionMessage'),
              confirmLabel: t('assistantChat.deleteSessionConfirm'), cancelLabel: t('common.cancel'), danger: true,
              onConfirm: () => controller.remove(item.id) }) }))));
      }
    }
    const messageKey = JSON.stringify(state.messages);
    if (messageKey !== lastMessages) {
      lastMessages = messageKey;
      const bottom = messages.scrollTop + messages.clientHeight >= messages.scrollHeight - 80;
      messages.textContent = '';
      if (!state.messages.length) messages.append(el('div', { class: 'assistant-chat-empty' }, el('p', {}, runText('empty')),
        button(runText('example'), { onClick: () => { input.value = runText('example'); input.focus(); } })));
      state.messages.forEach((message) => messages.append(el('article', { class: `assistant-chat-message assistant-chat-message-${message.role === 'user' ? 'user' : 'assistant'}` },
        el('p', { class: 'assistant-chat-role' }, t(message.role === 'user' ? 'assistantChat.user' : 'assistantChat.assistant')), contentNode(message.content))));
      if (bottom) messages.scrollTop = messages.scrollHeight;
    }
    const progressKey = JSON.stringify([state.events, state.run]);
    if (progressKey !== lastProgress) {
      lastProgress = progressKey;
      const expanded = progress.querySelector('details')?.open;
      progress.textContent = '';
      const timeline = assistantProgress(state.events, state.run, !!expanded);
      if (timeline) progress.append(timeline);
    }
    knowledgeTarget.textContent = '';
    if (state.knowledge.length) knowledgeTarget.append(el('strong', {}, runText('knowledgeTitle')),
      ...state.knowledge.map((item) => contentNode(item.summary || item.title)));
  }
  function close() {
    if (closed) return;
    closed = true; settingsView?.dispose();
    window.removeEventListener('hashchange', close);
    proposals.controller.close(); controller.close(); document.removeEventListener('keydown', keys); overlay.remove();
    releaseScroll();
    if (previousFocus?.isConnected) previousFocus.focus();
  }
  function keys(event) {
    if (!overlay.isConnected || document.querySelector('dialog[open]')
        || [...document.querySelectorAll('.modal-overlay')].at(-1) !== overlay) return;
    if (event.key === 'Escape') { event.preventDefault(); if (settingsView) hideSettings(); else close(); }
    containModalFocus(overlay, event);
  }
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); submit(); }
  });
  const dialog = el('div', { class: 'modal assistant-chat-modal', role: 'dialog', ariaModal: 'true', 'aria-labelledby': 'assistant-dialog-title' },
    el('div', { class: 'prompt-copilot-header' }, el('h2', { id: 'assistant-dialog-title' }, t('assistantChat.title')),
      el('div', { class: 'action-row' }, settingsButton, button(t('common.close'), { onClick: close }))),
    el('p', { class: 'modal-copy' }, runText('privacy')),
    el('div', { class: 'assistant-chat-shell' },
      el('aside', { class: 'assistant-chat-sidebar' }, el('div', { class: 'assistant-chat-sidebar-head' }, el('strong', {}, t('assistantChat.history')), fresh), sessionTarget),
      el('section', { class: 'assistant-chat-main' }, el('div', { class: 'assistant-chat-conversation' }, older, messages, progress, proposals.node, knowledgeTarget), status,
        el('div', { class: 'assistant-chat-composer' }, input, el('div', { class: 'action-row assistant-chat-actions' }, fork, knowledge, retry, stop, send)))),
    settingsTarget);
  overlay.append(dialog);
  document.body.append(overlay); document.addEventListener('keydown', keys);
  window.addEventListener('hashchange', close, { once: true });
  render(controller.state); controller.init(); input.focus();
}

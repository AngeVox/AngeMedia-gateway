import { captureSessionScope, clearSession, getSession, getSessionContext, invalidateExternalSession, isSessionNoticeCurrent,
  readSessionNotice, SESSION_CHANGE_KEY } from '../auth.js';
import { setChromeVisible } from '../layout.js?v=web-studio-2j';
import { t } from '../i18n.js';
import { el, mount } from './dom.js';
import { button } from './buttons.js';
import { loadingState } from './states.js';

let lifecycle = null;

// 通知只使旧页面失效；身份事实仍由服务器确认，普通请求另有会话标记保护。
export function startSessionLifecycle(onChanged) {
  if (lifecycle) return lifecycle;
  const content = document.getElementById('content');
  const feedback = el('div', { class: 'modal-overlay session-verification', hidden: true, role: 'status', ariaLive: 'polite' });
  document.body.append(feedback);
  let gate = null, resolveGate = null, checking = false, generation = 0, returnFocus = null, upgrading = false;

  function upgrade() {
    if (upgrading) return;
    upgrading = true; generation += 1; checking = false;
    resolveGate?.(false); gate = resolveGate = null;
    const draft = content.querySelector('textarea[name="prompt"]')?.value || '';
    clearSession();
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    content.hidden = true; setChromeVisible(false); feedback.hidden = false;
    mount(feedback, el('section', { class: 'modal' },
      el('p', { role: 'alert', textContent: t('auth.upgradeRequired') }),
      draft ? el('textarea', { readOnly: true, value: draft, rows: 5, ariaLabel: t('auth.upgradeDraft') }) : null,
      el('p', { class: 'field-help', textContent: t('auth.upgradeHelp') }),
      button(t('auth.upgradeReload'), { variant: 'primary', onClick: () => location.reload() }),
    ));
    feedback.querySelector('button')?.focus();
  }

  function reveal() {
    feedback.hidden = true; mount(feedback); content.hidden = false;
    setChromeVisible(location.hash !== '#/login');
    if (returnFocus?.isConnected && !returnFocus.disabled) returnFocus.focus();
    returnFocus = null;
  }

  function changed() {
    generation += 1; checking = false;
    invalidateExternalSession();
    resolveGate?.(false); gate = resolveGate = null;
    reveal(); onChanged();
  }

  function display(failed = false) {
    content.hidden = true; setChromeVisible(false); feedback.hidden = false;
    mount(feedback, el('section', { class: 'modal' },
      el('div', { class: 'modal-body' },
        failed ? el('p', { role: 'alert', textContent: t('auth.verifyFailed') }) : loadingState(t('auth.checkingSession')),
        failed ? button(t('auth.verifyRetry'), { onClick: attempt, variant: 'primary' }) : null,
      ),
    ));
    if (failed) feedback.querySelector('button')?.focus();
  }

  async function attempt() {
    if (checking) return;
    checking = true;
    const current = generation;
    const isCurrent = captureSessionScope();
    display();
    try {
      const session = await getSession({ refresh: true });
      if (current !== generation) return;
      if (!session) { changed(); return; }
      checking = false; reveal(); resolveGate?.(true); gate = resolveGate = null;
    } catch (error) {
      if (current !== generation) return;
      if (error.name === 'AbortError' && !isCurrent()) {
        checking = false; reveal(); resolveGate?.(false); gate = resolveGate = null; return;
      }
      checking = false; display(true);
    }
  }

  function check() {
    if (upgrading) return Promise.resolve(false);
    if (location.hash === '#/login' || !getSessionContext()) return Promise.resolve(false);
    if (!isSessionNoticeCurrent()) { changed(); return Promise.resolve(false); }
    if (!gate) {
      returnFocus = document.activeElement;
      gate = new Promise(resolve => { resolveGate = resolve; });
      const waiting = gate;
      attempt();
      return waiting;
    }
    return gate;
  }

  function storage(event) {
    if (upgrading) {
      const draft = feedback.querySelector('textarea');
      if (event.key === SESSION_CHANGE_KEY && draft) draft.value = '';
      return;
    }
    if (event.key === SESSION_CHANGE_KEY && event.newValue === readSessionNotice() && !isSessionNoticeCurrent()) changed();
  }
  function shown(event) { if (event.persisted) check(); }
  function routeChanged() {
    if (gate && location.hash === '#/login') {
      generation += 1; checking = false;
      resolveGate?.(false); gate = resolveGate = null; reveal();
    }
  }
  const focus = () => { if (getSessionContext()) check(); };
  window.addEventListener('storage', storage);
  window.addEventListener('pageshow', shown, true);
  window.addEventListener('focus', focus);
  window.addEventListener('hashchange', routeChanged);
  lifecycle = { check, upgrade };
  return lifecycle;
}

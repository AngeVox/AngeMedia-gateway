import { el } from './dom.js';
import { button } from './buttons.js';
import { AssistantActionsController } from '../lib/assistant-actions-controller.js';
import { AssistantActionCard } from './assistant-action-card.js';
import { actionText as text } from '../lib/assistant-action-copy.js';

export function assistantActions(api, onAuthError, onNavigate) {
  const cards = new Map(), list = el('div', { class: 'assistant-action-list' });
  const notice = el('p', { role: 'status', ariaLive: 'polite' }), count = el('span');
  const previous = button(text('previous'), { size: 'sm', onClick: () => controller.page(-1) });
  const next = button(text('next'), { size: 'sm', onClick: () => controller.page(1) });
  const node = el('section', { class: 'assistant-actions', hidden: true, 'aria-label': text('title') },
    el('h3', {}, text('title')), notice, list, el('div', { class: 'action-row' }, previous, count, next));
  const controller = new AssistantActionsController(api, (state) => {
    notice.textContent = state.error ? text(state.error) : state.loading && !state.items.length ? text('busy') : '';
    const ids = new Set(state.items.map((action) => action.id));
    for (const [id, card] of cards) if (!ids.has(id)) { card.node.remove(); cards.delete(id); }
    for (const [index, action] of state.items.entries()) {
      let card = cards.get(action.id);
      if (!card) { card = new AssistantActionCard(controller, action, onNavigate); cards.set(action.id, card); }
      card.update(action, { busy: state.busy.has(action.id), error: state.errors.get(action.id), assets: state.assets.get(action.job?.id) || [] });
      // 轮询保持已有节点位置，避免移动正在操作的复选框而丢失键盘焦点。
      if (list.children[index] !== card.node) list.insertBefore(card.node, list.children[index] || null);
    }
    list.querySelector('[data-action-empty]')?.remove();
    if (!state.items.length && !state.loading) list.append(el('p', { class: 'card-subtitle', dataset: { actionEmpty: 'true' } }, text('empty')));
    previous.hidden = state.offset === 0; next.hidden = state.offset + state.items.length >= state.total;
    previous.disabled = next.disabled = !!state.busy.size || state.loading || !!controller.editing.size;
    count.textContent = state.total ? `${state.offset + 1}–${state.offset + state.items.length} / ${state.total}` : '';
  }, onAuthError);
  return { node, controller, setContext(owner, sessionId, legacy) {
    node.hidden = !owner || !sessionId || legacy;
    controller.setContext(owner, sessionId, legacy);
  } };
}

import { el } from './dom.js';
import { t } from '../i18n.js';
import { runText, runEventText } from '../lib/assistant-run-copy.js';

export function assistantProgress(events, run, expanded = false) {
  const rows = events.map(runEventText).filter(Boolean);
  if (!rows.length && !run) return null;
  const node = el('details', { class: 'assistant-chat-timeline-panel', open: expanded },
    el('summary', {}, runText('process')),
    el('ol', { class: 'assistant-chat-timeline' }, ...rows.map((row) => el('li', {}, row))));
  if (run) node.append(el('p', { class: 'assistant-chat-run-counts' },
    t('assistant.run.counts')
      .replace('{model}', run.model_calls).replace('{maxModel}', run.max_model_calls)
      .replace('{tools}', run.tool_calls).replace('{maxTools}', run.max_tool_calls)));
  return node;
}

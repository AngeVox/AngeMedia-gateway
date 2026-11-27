import { t } from '../i18n.js';

export function runText(key) {
  const translationKey = 'assistant.run.' + key;
  const translated = t(translationKey);
  return translated === translationKey ? t('assistant.run.unavailable') : translated;
}

export function runEventText(event) {
  const type = event.event_type, payload = event.payload || {};
  if (type === 'tool_started' || type === 'tool_finished') {
    if (type === 'tool_finished' && payload.status === 'error') return runText('stepFailed');
    if (type === 'tool_finished' && payload.status !== 'success') return runText('stepUnconfirmed');
    const key = 'assistant.run.tool.' + payload.name + (type === 'tool_started' ? '.started' : '.finished');
    const translated = t(key);
    return translated === key ? runText(type === 'tool_started' ? 'stepChecking' : 'stepCompleted') : translated;
  }
  if (['admitted', 'started', 'resumed'].includes(type)) return t('assistant.run.event.' + type);
  return ['paused', 'succeeded', 'failed', 'refused', 'canceled', 'timed_out', 'budget_exceeded'].includes(type) ? runText(type) : null;
}

export function runSummary(state) {
  if (state.error) return runText(state.error);
  if (state.pending && !state.submitting) return runText('pending');
  if (state.disconnected) return runText('reconnecting');
  if (state.loading) return runText('loading');
  if (state.run?.status === 'running') {
    const latest = [...(state.events || [])].reverse().find((event) => ['tool_started', 'tool_finished'].includes(event.event_type));
    if (latest?.event_type === 'tool_started') return runEventText(latest);
  }
  if (state.run) return state.run.output?.error_code ? runText(state.run.output.error_code) : runText(state.run.status);
  return runText(state.runtime.can_submit ? 'ready' : state.runtime.state);
}

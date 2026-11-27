import { eventSequence } from './assistant-sse.js';

export const isTerminalRun = (run) => !!run && !['queued', 'running', 'paused'].includes(run.status);

function pause(ms, signal) {
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}

// 重连只读GET，绝不通过重发POST来恢复订阅。
export async function observeRun(api, runId, { signal, onRun, onEvent, onConnection }) {
  let after = 0, failures = 0;
  const path = `/admin/assistant/runs/${encodeURIComponent(runId)}`;
  while (!signal.aborted) {
    try {
      let complete = false;
      await api.stream(`${path}/stream?after=${after}`, { signal, onEvent(frame) {
        if (signal.aborted) return;
        if (frame.event === 'error') {
          const error = new Error('Assistant event stream stopped');
          error.status = frame.data.code === 'authentication_expired' ? 401 : frame.data.code === 'run_not_found' ? 404 : 503;
          throw error;
        }
        if (frame.event === 'done') {
          if (frame.data.run?.id !== runId || frame.data.run.event_seq !== after) throw new Error('Incomplete assistant events');
          onRun(frame.data.run); complete = true; return;
        }
        const sequence = eventSequence(frame, runId, after);
        if (sequence === null) return;
        onEvent(frame.data); after = sequence; failures = 0; onConnection(false);
      } });
      if (signal.aborted || complete) return;
      const { run } = await api.get(path);
      if (signal.aborted) return;
      onRun(run);
      if (isTerminalRun(run) && after >= run.event_seq) return;
      throw new Error('Assistant stream disconnected');
    } catch (error) {
      if (signal.aborted || error.name === 'AbortError') return;
      if (error.status >= 400 && error.status < 500 && error.status !== 429) throw error;
      onConnection(true);
      if (++failures > 5) throw error;
      await pause(Math.min(500 * 2 ** (failures - 1), 5000), signal);
    }
  }
}

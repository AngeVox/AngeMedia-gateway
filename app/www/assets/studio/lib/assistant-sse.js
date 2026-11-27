// 按行解析持久事件；只在完整帧交付后由调用方推进游标。
export function createSseParser(dispatch) {
  let buffer = '', event = '', id = '', data = [], size = 0;
  function line(value) {
    if (!value) {
      if (data.length) dispatch({ event: event || 'message', id, data: JSON.parse(data.join('\n')) });
      event = ''; data = []; size = 0;
      return;
    }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const name = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (name === 'data') {
      size += content.length;
      if (size > 1048576) throw new Error('Invalid assistant event size');
      data.push(content);
    }
    if (name === 'event') event = content;
    if (name === 'id' && !content.includes('\0')) id = content;
  }
  return (chunk) => {
    buffer += chunk;
    if (buffer.length > 1048576) throw new Error('Invalid assistant event size');
    let match;
    while ((match = /\r\n|\n|\r/.exec(buffer))) {
      if (match[0] === '\r' && match.index === buffer.length - 1) break;
      const value = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      line(value);
    }
  };
}

export function eventSequence(frame, runId, after) {
  const match = /^([a-f0-9]{32}):([0-9]+)$/.exec(frame.id);
  const sequence = Number(match?.[2]);
  if (!match || match[1] !== runId || !Number.isSafeInteger(sequence)
      || frame.data.run_id !== runId || frame.data.sequence !== sequence) throw new Error('Invalid assistant event');
  if (sequence <= after) return null;
  if (sequence !== after + 1) throw new Error('Missing assistant event');
  return sequence;
}

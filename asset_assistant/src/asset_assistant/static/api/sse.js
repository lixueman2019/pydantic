import { ApiError } from './errors.js';

const types = new Set(['run_started', 'text_delta', 'tool_started', 'tool_completed',
  'tool_error', 'retry', 'citation', 'chart', 'run_completed', 'run_error']);

/** Parse UTF-8 SSE across arbitrary chunk boundaries, with no automatic replay. */
export async function consumeSSE(body, { signal, onEvent }) {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '', lines = [], runId = null, seq = -1, terminal = false;
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal.throwIfAborted();
  signal.addEventListener('abort', abort, { once: true });

  function dispatch() {
    const data = lines.filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).replace(/^ /, '')).join('\n');
    const namedType = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
    lines = [];
    if (!data) return;
    let event;
    try { event = JSON.parse(data); }
    catch { throw new ApiError('INVALID_STREAM', '事件不是有效 JSON，已停止读取。'); }
    event.type ??= namedType;
    if (!types.has(event.type) || typeof event.run_id !== 'string' || !event.run_id ||
        !Number.isSafeInteger(event.seq) || event.seq < 0 || event.seq <= seq ||
        typeof event.time !== 'string' || !Number.isFinite(Date.parse(event.time)) ||
        !event.payload || typeof event.payload !== 'object' || Array.isArray(event.payload) ||
        (runId !== null && event.run_id !== runId) || (runId === null && event.type !== 'run_started')) {
      throw new ApiError('INVALID_STREAM', '事件契约、运行标识或顺序无效。');
    }
    runId = event.run_id;
    seq = event.seq;
    terminal = event.type === 'run_completed' || event.type === 'run_error';
    onEvent(event);
  }

  function drain(final = false) {
    while (!terminal) {
      const match = /\r\n|\r|\n/.exec(buffer);
      if (!match || (!final && match[0] === '\r' && match.index === buffer.length - 1)) break;
      const line = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      if (!line) dispatch();
      else if (!line.startsWith(':')) lines.push(line);
      if (lines.join('').length > 1048576) throw new ApiError('STREAM_TOO_LARGE', '事件超过安全大小限制。');
    }
    if (buffer.length > 1048576) throw new ApiError('STREAM_TOO_LARGE', '事件超过安全大小限制。');
  }

  try {
    while (!terminal) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) {
        buffer += decoder.decode();
        drain(true);
        // A terminal frame must be fully delimited; truncated data is never success.
        if (!terminal) throw new ApiError('STREAM_DISCONNECTED', '连接在完成事件前中断，回答未完成；请手动重试。');
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      drain();
    }
  } finally {
    signal.removeEventListener('abort', abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
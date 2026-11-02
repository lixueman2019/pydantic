import { ApiError, controlledId, documentUrl, chartUrl } from './errors.js';
import { consumeSSE } from './sse.js';

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { Accept: 'application/json', ...options.headers },
  });
  if (!response.ok) {
    let envelope;
    try { envelope = await response.json(); } catch { /* Do not expose raw server HTML. */ }
    throw new ApiError(envelope?.error?.code ?? `HTTP_${response.status}`,
      envelope?.error?.message ?? '请求失败，请检查本地服务状态。', envelope?.error?.details);
  }
  return response;
}

/** @type {import('./types.js').ApiClient} */
export const liveClient = {
  async health() { return (await request('/api/health')).json(); },
  async createSession() { return (await request('/api/sessions', { method: 'POST' })).json(); },
  async deleteSession(sessionId) {
    await request(`/api/sessions/${controlledId(sessionId)}`, { method: 'DELETE' });
  },
  async chat(sessionId, message, options) {
    const response = await request('/api/chat', {
      method: 'POST', signal: options.signal,
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    if (!response.headers.get('content-type')?.toLowerCase().startsWith('text/event-stream') || !response.body) {
      throw new ApiError('INVALID_STREAM', '服务器未返回 SSE 事件流。');
    }
    await consumeSSE(response.body, options);
  },
  documentUrl,
  chartUrl,
};
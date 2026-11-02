export class ApiError extends Error {
  constructor(code, message, details = null) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.details = details;
  }
}

export function controlledId(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)) {
    throw new ApiError('INVALID_RESOURCE_ID', '服务器资源标识无效；已拒绝构造链接。');
  }
  return encodeURIComponent(value);
}

export function documentUrl(sourceId, page) {
  const path = `/api/documents/${controlledId(sourceId)}`;
  return Number.isSafeInteger(page) && page > 0 ? `${path}#page=${page}` : path;
}

export function chartUrl(sessionId, chartId) {
  return `/api/sessions/${controlledId(sessionId)}/charts/${controlledId(chartId)}`;
}
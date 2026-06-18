const BASE = '/v1';

let unauthorizedHandler = null;

export class ApiError extends Error {
  constructor(message, { status, detail, safe } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.safe = safe || {};
  }
}

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

function safeDetail(detail) {
  if (typeof detail === 'string') {
    return { message: detail };
  }
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return {};
  }
  const safe = {};
  ['message', 'code', 'error_category', 'human_hint', 'retryable', 'gateway_stage'].forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(detail, key)) {
      safe[key] = detail[key];
    }
  });
  if (detail.existing_job && typeof detail.existing_job === 'object') {
    safe.existing_job = {
      job_id: detail.existing_job.job_id,
      kind: detail.existing_job.kind,
      status: detail.existing_job.status,
      created_at: detail.existing_job.created_at,
    };
  }
  return safe;
}

async function parseErrorResponse(res) {
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    const safe = safeDetail(detail);
    const message = safe.human_hint || safe.message || res.statusText || `HTTP ${res.status}`;
    return new ApiError(message, { status: res.status, detail, safe });
  }
  return new ApiError(res.statusText || `HTTP ${res.status}`, { status: res.status });
}

async function request(method, path, body, isLoginRequest = false) {
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
  const opts = {
    method,
    credentials: 'include',
    headers: isFormData ? {} : { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = isFormData ? body : JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401 && !isLoginRequest && unauthorizedHandler) {
    unauthorizedHandler();
    throw new ApiError('Session expired', { status: 401 });
  }
  if (!res.ok) {
    throw await parseErrorResponse(res);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body, isLoginRequest = false) => request('POST', path, body, isLoginRequest),
  upload: (path, formData) => request('POST', path, formData),
  patch: (path, body) => request('PATCH', path, body),
  delete: (path) => request('DELETE', path),
};

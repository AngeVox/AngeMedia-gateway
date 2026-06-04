(() => {
  function authHeaders(json = true) {
    const headers = {};
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
  }

  async function fetchJson(url, options = {}) {
    const headers = options.headers || authHeaders(false);
    const res = await fetch(url, { credentials: 'same-origin', ...options, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent('angemedia:admin-unauthorized'));
        const err = new Error(message || '需要登录管理后台');
        err.status = 401;
        throw err;
      }
      throw new Error(message || '请求失败');
    }
    return data;
  }

  function postJson(url, payload) {
    return fetchJson(url, { method: 'POST', headers: authHeaders(), body: JSON.stringify(payload) });
  }

  function deleteJson(url) {
    return fetchJson(url, { method: 'DELETE', headers: authHeaders(false) });
  }

  window.AngeAdminApi = { authHeaders, fetchJson, postJson, deleteJson };
})();

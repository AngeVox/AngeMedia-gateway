window.AngeUtils = (() => {
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function humanSize(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
  }

  function gatewayPathPrefix() {
    const path = window.location.pathname || '/';
    const known = ['/admin', '/studio', '/api-docs'];
    for (const suffix of known) {
      if (path.endsWith(suffix)) {
        return path.slice(0, -suffix.length) || '';
      }
    }
    return path.endsWith('/') ? path.slice(0, -1) : '';
  }

  function displayGatewayUrl(url) {
    const text = String(url || '');
    if (!text) return '';
    const prefix = gatewayPathPrefix();
    try {
      const parsed = new URL(text, window.location.href);
      const isLocalhost = parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1';
      const isStaticMedia = parsed.pathname.startsWith('/generated/') || parsed.pathname.startsWith('/uploads/');
      if ((isLocalhost || parsed.origin === window.location.origin) && isStaticMedia) {
        return `${prefix}${parsed.pathname}${parsed.search}`;
      }
      return text;
    } catch {
      if (text.startsWith('/generated/') || text.startsWith('/uploads/')) {
        return `${prefix}${text}`;
      }
      return text;
    }
  }

  function fileNameFromUrl(url, fallback = 'angemedia-media') {
    const text = String(url || '');
    if (!text) return fallback;
    if (text.startsWith('data:image/')) return `${fallback}.png`;
    if (text.startsWith('data:video/')) return `${fallback}.mp4`;
    try {
      const parsed = new URL(displayGatewayUrl(text), window.location.href);
      const name = decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '');
      return name || fallback;
    } catch {
      const clean = text.split('?')[0].split('#')[0].split('/').pop();
      return clean || fallback;
    }
  }

  async function downloadToBrowser(url, filename) {
    const href = displayGatewayUrl(url);
    const name = filename || fileNameFromUrl(href);
    const clickAnchor = (targetHref, downloadName, newTab = false) => {
      const anchor = document.createElement('a');
      anchor.href = targetHref;
      anchor.download = downloadName;
      if (newTab) {
        anchor.target = '_blank';
        anchor.rel = 'noopener';
      }
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    };

    if (href.startsWith('data:')) {
      clickAnchor(href, name);
      return { ok: true, mode: 'data-url' };
    }

    try {
      const response = await fetch(href, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      clickAnchor(objectUrl, name);
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      return { ok: true, mode: 'blob' };
    } catch (error) {
      clickAnchor(href, name, true);
      return { ok: false, mode: 'open', error };
    }
  }

  function bindDownloadButtons(toast) {
    if (document.documentElement.dataset.angeDownloadBound === 'true') return;
    document.documentElement.dataset.angeDownloadBound = 'true';
    document.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-download-url]');
      if (!button) return;
      event.preventDefault();
      const url = button.dataset.downloadUrl || '';
      if (!url) return;
      button.disabled = true;
      try {
        const result = await downloadToBrowser(url, button.dataset.downloadFilename || '');
        if (toast) toast(result.ok ? '已开始下载到本地' : '已打开媒体链接，请在浏览器中保存');
      } catch (error) {
        if (toast) toast(error?.message || '下载失败');
      } finally {
        button.disabled = false;
      }
    });
  }

  return {
    escapeHtml,
    escapeAttr,
    humanSize,
    displayGatewayUrl,
    gatewayPathPrefix,
    fileNameFromUrl,
    downloadToBrowser,
    bindDownloadButtons
  };
})();

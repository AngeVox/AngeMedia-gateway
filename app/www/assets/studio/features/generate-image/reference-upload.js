import { t } from '../../i18n.js';
import { el, mount } from '../../components/dom.js';
import { api, ApiError } from '../../api.js';
import { safeAssetHref } from '../../lib/asset-url.js';
import { formatBytes } from '../../lib/format.js';

const UPLOAD_MAX_BYTES = 20 * 1024 * 1024;
const UPLOAD_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif';
const UPLOAD_MIME_TYPES = new Set(UPLOAD_ACCEPT.split(','));

function isSupportedImage(file) {
  const type = String(file?.type || '').toLowerCase();
  const name = String(file?.name || '').toLowerCase();
  return UPLOAD_MIME_TYPES.has(type) || /\.(png|jpe?g|webp|gif)$/.test(name);
}

async function postUpload(file) {
  const form = new FormData();
  form.append('files', file);
  form.append('roles', 'reference');
  return api.upload('/uploads', form);
}

function uploadedReferencePath(result) {
  const first = result?.data?.[0] || result?.[0] || {};
  const safePath = safeAssetHref(first.url_path || first.url);
  if (safePath?.startsWith('/uploads/')) return safePath;
  const filename = String(first.filename || '').trim();
  if (filename && !/[\\/]/.test(filename)) {
    return `/uploads/${encodeURIComponent(filename)}`;
  }
  throw new ApiError(t('generateImage.uploadInvalidResponse'));
}

export function createReferenceUpload({ target }) {
  let selectedFile = null;
  let uploadedPath = null;
  let previewUrl = null;

  const preview = el('div', { class: 'ref-upload-preview', hidden: true });
  const fileInfo = el('span', { class: 'ref-upload-info' });
  const removeBtn = el('button', {
    type: 'button',
    class: 'btn btn-secondary btn-sm',
    textContent: '×',
    title: t('generateImage.uploadRemove'),
  });
  const statusText = el('span', { class: 'ref-upload-status field-help' });
  const fileInput = el('input', {
    type: 'file',
    accept: UPLOAD_ACCEPT,
    class: 'ref-upload-input',
  });

  const previewImg = el('img', { class: 'ref-upload-thumb', alt: '' });
  mount(preview, previewImg, fileInfo, removeBtn);

  const wrapper = el('div', { class: 'ref-upload-control' },
    fileInput,
    preview,
    statusText,
  );
  mount(target, wrapper);

  function releasePreviewUrl() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  function resetPreview() {
    releasePreviewUrl();
    selectedFile = null;
    uploadedPath = null;
    preview.hidden = true;
    previewImg.src = '';
    fileInfo.textContent = '';
    statusText.textContent = '';
    fileInput.value = '';
  }

  function showPreview(file) {
    releasePreviewUrl();
    selectedFile = file;
    uploadedPath = null;
    previewUrl = URL.createObjectURL(file);
    previewImg.src = previewUrl;
    fileInfo.textContent = `${file.name} (${formatBytes(file.size)})`;
    statusText.textContent = '';
    preview.hidden = false;
  }

  fileInput.addEventListener('change', () => {
    const file = fileInput.files?.[0];
    if (!file) {
      resetPreview();
      return;
    }
    if (file.size > UPLOAD_MAX_BYTES) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadTooLarge');
      return;
    }
    if (!isSupportedImage(file)) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadInvalidType');
      return;
    }
    showPreview(file);
  });

  removeBtn.addEventListener('click', () => {
    resetPreview();
  });

  async function prepare() {
    if (!selectedFile) return null;
    if (uploadedPath) return uploadedPath;
    statusText.textContent = t('generateImage.uploading');
    try {
      const result = await postUpload(selectedFile);
      uploadedPath = uploadedReferencePath(result);
      statusText.textContent = t('generateImage.uploadDone');
      return uploadedPath;
    } catch (error) {
      statusText.textContent = error.message || t('generateImage.uploadFailed');
      throw error;
    }
  }

  function value() {
    return uploadedPath || null;
  }

  function hasPendingFile() {
    return selectedFile !== null;
  }

  function clear() {
    resetPreview();
  }

  return { prepare, value, hasPendingFile, clear };
}

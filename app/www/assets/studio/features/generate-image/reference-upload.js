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

async function postUpload(files, role) {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  form.append('roles', files.map(() => role).join(','));
  return api.upload('/uploads', form);
}

function uploadedReferencePaths(result) {
  const rows = Array.isArray(result?.data) ? result.data : (Array.isArray(result) ? result : []);
  if (!rows.length) throw new ApiError(t('generateImage.uploadInvalidResponse'));
  return rows.map((item) => {
    const safePath = safeAssetHref(item?.url_path || item?.url);
    if (safePath?.startsWith('/uploads/')) return safePath;
    const filename = String(item?.filename || '').trim();
    if (filename && !/[\\/]/.test(filename)) {
      return '/uploads/' + encodeURIComponent(filename);
    }
    throw new ApiError(t('generateImage.uploadInvalidResponse'));
  });
}

export function createReferenceUpload({ target, multiple = false, maxFiles = 1, role = 'reference' }) {
  let selectedFiles = [];
  let uploadedPaths = [];
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
    multiple,
    class: 'ref-upload-input',
  });

  const previewImg = el('img', { class: 'ref-upload-thumb', alt: '' });
  mount(preview, previewImg, fileInfo, removeBtn);
  mount(target, el('div', { class: 'ref-upload-control' }, fileInput, preview, statusText));

  function releasePreviewUrl() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }

  function resetPreview() {
    releasePreviewUrl();
    selectedFiles = [];
    uploadedPaths = [];
    preview.hidden = true;
    previewImg.src = '';
    fileInfo.textContent = '';
    statusText.textContent = '';
    fileInput.value = '';
  }

  function showPreview(files) {
    releasePreviewUrl();
    selectedFiles = files;
    uploadedPaths = [];
    if (files[0]) {
      previewUrl = URL.createObjectURL(files[0]);
      previewImg.src = previewUrl;
    }
    const total = files.reduce((sum, file) => sum + Number(file.size || 0), 0);
    fileInfo.textContent = files.length === 1
      ? files[0].name + ' (' + formatBytes(total) + ')'
      : String(files.length) + ' ' + t('generateImage.referenceFiles') + ' (' + formatBytes(total) + ')';
    statusText.textContent = '';
    preview.hidden = false;
  }

  fileInput.addEventListener('change', () => {
    const files = Array.from(fileInput.files || []);
    if (!files.length) {
      resetPreview();
      return;
    }
    if (files.length > maxFiles) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadTooMany').replace('{count}', String(maxFiles));
      return;
    }
    if (files.some((file) => file.size > UPLOAD_MAX_BYTES)) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadTooLarge');
      return;
    }
    if (files.some((file) => !isSupportedImage(file))) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadInvalidType');
      return;
    }
    showPreview(files);
  });

  removeBtn.addEventListener('click', resetPreview);

  async function prepare() {
    if (!selectedFiles.length) return multiple ? [] : null;
    if (uploadedPaths.length) return multiple ? [...uploadedPaths] : uploadedPaths[0];
    statusText.textContent = t('generateImage.uploading');
    try {
      uploadedPaths = uploadedReferencePaths(await postUpload(selectedFiles, role));
      statusText.textContent = t('generateImage.uploadDone');
      return multiple ? [...uploadedPaths] : uploadedPaths[0];
    } catch (error) {
      statusText.textContent = error.message || t('generateImage.uploadFailed');
      throw error;
    }
  }

  function value() {
    if (multiple) return [...uploadedPaths];
    return uploadedPaths[0] || null;
  }

  function hasPendingFile() {
    return selectedFiles.length > 0;
  }

  return { prepare, value, hasPendingFile, clear: resetPreview };
}

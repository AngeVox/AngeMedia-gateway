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

function changedSelectionError() {
  const message = t('generateImage.referenceChanged');
  return new ApiError(message, { safe: { code: 'reference_selection_changed', message } });
}

export function createReferenceUpload({ target, multiple = false, maxFiles = 1, role = 'reference' }) {
  let selectedFiles = [];
  let uploadedPaths = [];
  let previewUrl = null;
  let preparing = null;
  let disposed = false, suspended = false, selectionVersion = 0;

  function isCurrent(files, version, isActive) {
    return !disposed && !suspended && selectedFiles === files && selectionVersion === version && isActive();
  }

  function selectionLimit() {
    const value = Number(typeof maxFiles === 'function' ? maxFiles() : maxFiles);
    return Number.isInteger(value) && value > 0 ? value : 1;
  }

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
    accept: role === 'markup' ? 'image/png,.png' : UPLOAD_ACCEPT,
    multiple,
    class: 'ref-upload-input',
  });

  const previewImg = el('img', { class: 'ref-upload-thumb', alt: '' });
  mount(preview, previewImg, fileInfo, removeBtn);
  mount(target, el('div', { class: 'ref-upload-control' }, fileInput, preview, statusText));

  function releasePreviewUrl() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    previewImg.src = '';
  }

  function resetPreview() {
    selectionVersion += 1;
    preparing = null;
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
    selectionVersion += 1;
    preparing = null;
    releasePreviewUrl();
    selectedFiles = files;
    uploadedPaths = [];
    if (files[0] && !suspended) {
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

  function changeSelection() {
    if (disposed) return;
    const files = Array.from(fileInput.files || []);
    if (!files.length) {
      resetPreview();
      return;
    }
    const limit = selectionLimit();
    if (files.length > limit) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadTooMany').replace('{count}', String(limit));
      return;
    }
    if (files.some((file) => file.size > UPLOAD_MAX_BYTES)) {
      resetPreview();
      statusText.textContent = t('generateImage.uploadTooLarge');
      return;
    }
    const isAllowed = role === 'markup' ? file => /\.png$/i.test(file.name) : isSupportedImage;
    if (files.some((file) => !isAllowed(file))) {
      resetPreview();
      statusText.textContent = t(role === 'markup' ? 'generateImage.image_markup_overlay_unreadable' : 'generateImage.uploadInvalidType');
      return;
    }
    showPreview(files);
  }

  fileInput.addEventListener('change', changeSelection);

  removeBtn.addEventListener('click', resetPreview);

  async function uploadSelection(files, limit, isActive, version) {
    statusText.textContent = t('generateImage.uploading');
    try {
      if (files.length > limit) {
        throw new ApiError(t('generateImage.uploadTooMany').replace('{count}', String(limit)));
      }
      const response = await api.get('/uploads?limit=1');
      if (!isCurrent(files, version, isActive)) throw changedSelectionError();
      const batchSize = Number(response?.limits?.max_files);
      if (!Number.isInteger(batchSize) || batchSize < 1) throw new ApiError(t('generateImage.uploadInvalidResponse'));
      // 型号总图数与后台单次上传容量分别遵守；每个已确认成功的批次仅上传一次。
      while (uploadedPaths.length < files.length) {
        if (!isCurrent(files, version, isActive)) throw changedSelectionError();
        const batch = files.slice(uploadedPaths.length, uploadedPaths.length + batchSize);
        const paths = uploadedReferencePaths(await postUpload(batch, role));
        if (!isCurrent(files, version, isActive)) throw changedSelectionError();
        if (paths.length !== batch.length) throw new ApiError(t('generateImage.uploadInvalidResponse'));
        uploadedPaths.push(...paths);
      }
      statusText.textContent = t('generateImage.uploadDone');
      return multiple ? [...uploadedPaths] : uploadedPaths[0];
    } catch (error) {
      if (isCurrent(files, version, isActive)) statusText.textContent = error.message || t('generateImage.uploadFailed');
      throw error;
    }
  }

  async function prepare(files = selectedFiles, limit = selectionLimit(), isActive = () => true, version = selectionVersion) {
    if (disposed || suspended) throw changedSelectionError();
    if (!files.length) return multiple ? [] : null;
    if (!isCurrent(files, version, isActive)) throw changedSelectionError();
    if (uploadedPaths.length === files.length) return multiple ? [...uploadedPaths] : uploadedPaths[0];
    if (preparing?.files === files && preparing.version === version) return preparing.promise;
    const promise = uploadSelection(files, limit, isActive, version);
    preparing = { files, version, promise };
    try {
      return await promise;
    } finally {
      if (preparing?.promise === promise) preparing = null;
    }
  }

  function value() {
    if (multiple) return [...uploadedPaths];
    return uploadedPaths[0] || null;
  }

  function hasPendingFile() {
    return selectedFiles.length > 0;
  }

  function capture(isActive = () => true) {
    const files = selectedFiles, limit = selectionLimit(), version = selectionVersion;
    return () => prepare(files, limit, isActive, version);
  }

  function suspend() {
    if (disposed || suspended) return;
    suspended = true;
    selectionVersion += 1;
    preparing = null;
    releasePreviewUrl();
    statusText.textContent = '';
  }

  function resume() {
    if (disposed || !suspended) return;
    suspended = false;
    if (selectedFiles[0]) {
      previewUrl = URL.createObjectURL(selectedFiles[0]);
      previewImg.src = previewUrl;
    }
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    resetPreview();
    fileInput.removeEventListener('change', changeSelection);
    removeBtn.removeEventListener('click', resetPreview);
    fileInput.disabled = removeBtn.disabled = true;
  }

  return { prepare, capture, value, hasPendingFile, clear: resetPreview, suspend, resume, dispose };
}

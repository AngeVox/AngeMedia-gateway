import { getLanguage } from '../i18n.js';

export function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString(getLanguage());
}

export function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export function formatDuration(ms) {
  if (ms === null || ms === undefined || ms === '') return '-';
  const value = Number(ms);
  if (!Number.isFinite(value) || value < 0) return '-';
  const unit = (amount, name, maximumFractionDigits = 0) => new Intl.NumberFormat(getLanguage(), {
    style: 'unit', unit: name, maximumFractionDigits,
  }).format(amount);
  if (value < 1000) return unit(value, 'millisecond');
  const seconds = Math.round(value / 100) / 10;
  if (seconds < 60) return unit(seconds, 'second', 1);
  const wholeSeconds = Math.round(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  const rest = wholeSeconds % 60;
  return rest ? `${unit(minutes, 'minute')} ${unit(rest, 'second')}` : unit(minutes, 'minute');
}

export function shortId(value, length = 8) {
  if (!value) return '-';
  return String(value).slice(0, length);
}

export function truncateText(value, max = 120) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

export function extensionFromName(value, fallback = '') {
  const name = String(value || '');
  const match = name.match(/\.([a-z0-9]{2,8})$/i);
  return match ? `.${match[1].toLowerCase()}` : fallback;
}

export function safeFilenamePart(value, fallback = 'asset') {
  const cleaned = String(value || '')
    .normalize('NFKD')
    .replace(/[^\w.-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
    .slice(0, 48);
  return cleaned || fallback;
}

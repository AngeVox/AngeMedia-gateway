import { t } from '../../i18n.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';

const PROVIDER_SECRET_RESPONSE_FIELDS = [
  'api_key',
  '_api_key',
  'key',
  'secret',
  '_secret',
  'token',
  'access_token',
  'password',
  'raw',
  'raw_response',
  'raw_error',
  'exception',
  'stack',
];

export function hasProviderSecretField(item) {
  if (!item || typeof item !== 'object') return false;
  if (Array.isArray(item)) return item.some(hasProviderSecretField);
  return Object.keys(item).some((key) => PROVIDER_SECRET_RESPONSE_FIELDS.includes(key)) ||
    Object.values(item).some(hasProviderSecretField);
}

export function providerCreateErrorMessage(error) {
  const detail = typeof error?.detail === 'string' ? error.detail : '';
  const message = [
    error?.safe?.human_hint,
    error?.safe?.message,
    error?.message,
    detail,
  ].filter(Boolean).join(' ');
  const safeDetail = detail ? ` ${t('providers.errorDetailPrefix')} ${safeText(detail, 180)}` : '';

  if (/只允许\s*http|http\s*or\s*https|missing.*scheme|invalid.*url|URL.*scheme/i.test(message)) {
    return `${t('providers.baseUrlMissingProtocol')}${safeDetail}`;
  }

  if (/内网|保留地址|私网|private|reserved|loopback|link-local|localhost|127\.0\.0\.1|::1/i.test(message)) {
    return `${t('providers.privateUrlPolicy')}${safeDetail}`;
  }

  return safeErrorMessage(error, t('providers.createError'));
}

export function validateProviderBaseUrl(value) {
  const text = value.trim();
  if (!/^https?:\/\//i.test(text)) return t('providers.baseUrlMissingProtocol');

  let url;
  try {
    url = new URL(text);
  } catch (_) {
    return t('providers.baseUrlInvalid');
  }

  if (/\/images\/generations\/?$/i.test(url.pathname) || /\/images\/generations\//i.test(url.pathname)) {
    return t('providers.baseUrlNoEndpoint');
  }

  return '';
}

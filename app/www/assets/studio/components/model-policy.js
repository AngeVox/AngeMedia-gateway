import { t } from '../i18n.js';
import { badge } from './badges.js';
import { el } from './dom.js';
import { safeText } from '../lib/security.js';

export function isLegacyModel(policy) {
  return policy?.reviewed_lifecycle === 'legacy_unverified' || policy?.lifecycle === 'legacy_unverified';
}

function documentationUrl(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    if (Array.from(url.searchParams.keys()).some((key) => /^(key|api_key|token|secret|password)$/i.test(key))) return null;
    return url.href;
  } catch (_) {
    return null;
  }
}

export function modelStatusBadge(status) {
  const key = 'catalog.status.' + status;
  const translated = t(key);
  return badge(translated === key ? safeText(status || '-', 24) : translated,
    status === 'release' ? 'success' : 'warning');
}

export function renderModelPolicy(policy) {
  const sources = (Array.isArray(policy?.sources) ? policy.sources : []).map(documentationUrl).filter(Boolean);
  return el('details', { class: 'list-disclosure' },
    el('summary', {}, t('modelPolicy.source')),
    isLegacyModel(policy) ? el('p', { class: 'field-help' }, t('modelPolicy.legacyWarning')) : null,
    sources.length ? el('div', { class: 'action-row' }, sources.map((href, index) => el('a', { href, target: '_blank', rel: 'noopener noreferrer' }, `${t('modelPolicy.source')} ${index + 1}`))) : null,
  );
}

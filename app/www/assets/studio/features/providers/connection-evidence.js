import { t } from '../../i18n.js';
import { badge } from '../../components/badges.js';
import { el } from '../../components/dom.js';
import { formatDate } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

const META = Object.freeze({
  success: ['providers.connectionSuccess', 'success'],
  failed: ['providers.connectionFailed', 'danger'],
  unsupported: ['providers.connectionUnsupported', 'muted'],
  not_configured: ['providers.notConfigured', 'warning'],
  disabled: ['providers.disabled', 'muted'],
  not_tested: ['providers.connectionNotTested', 'muted'],
  testing: ['providers.builtinTesting', 'muted'],
  expired: ['providers.connectionExpired', 'warning'],
  configuration_changed: ['providers.connectionChanged', 'warning'],
});
const expiryTimers = new WeakMap();

export function disposeConnectionEvidence(container) {
  clearTimeout(expiryTimers.get(container));
  expiryTimers.delete(container);
}

export function prepareConnectionEvidence(result, receivedAt = Date.now()) {
  const expires = Date.parse(result?.expires_at);
  const serverTime = Date.parse(result?.server_time);
  return {
    ...result,
    // 只换算一次服务端剩余时长，抽屉重绘不会延长证据寿命。
    deadline: Number.isFinite(expires) && Number.isFinite(serverTime) ? receivedAt + expires - serverTime : null,
  };
}

export function connectionEvidencePresentation(result, now = Date.now()) {
  let status = Object.hasOwn(META, result?.status) ? result.status : 'not_tested';
  if (Object.hasOwn(META, result?.evidence_state)) status = result.evidence_state;
  if (['current', 'testing'].includes(result?.evidence_state) && Number.isFinite(result.deadline) && now >= result.deadline) status = 'expired';
  if (status === 'success' && (result?.evidence_state !== 'current' || !Number.isFinite(result?.deadline))) status = 'not_tested';
  const [label, tone] = META[status];
  let message = safeText(result?.message || '', 180);
  if (['expired', 'configuration_changed'].includes(status)) {
    message = t('providers.connectionRetestHint');
  } else if (status === 'success') {
    message = t('providers.connectionScopeHint');
    const visibility = result.model_list_validated === true ? result.project_model_visibility : null;
    if (Array.isArray(visibility?.listed) && Array.isArray(visibility?.not_listed)) {
      const listed = visibility.listed.map(model => safeText(model, 120)).join(', ');
      const missing = visibility.not_listed.map(model => safeText(model, 120)).join(', ');
      message = [
        t('providers.connectionGenerationUnverified'),
        t('providers.connectionModelsListed'),
        listed || t('providers.connectionModelsNone'),
        t('providers.connectionModelsNotListed'),
        missing || t('providers.connectionModelsNone'),
      ].join('\n');
    }
  } else if (status === 'unsupported' && result?.provider_id === 'modelscope') {
    message = t('providers.connectionModelScopeOfficialOnly');
  }
  return { status, label: t(label), tone, message };
}

export function renderConnectionEvidence(container, result, { compact = false } = {}) {
  disposeConnectionEvidence(container);
  const view = connectionEvidencePresentation(result);
  const checked = result?.checked_at ? `${t('providers.connectionCheckedAt')}: ${formatDate(result.checked_at)}` : '';
  const expires = result?.expires_at ? `${t('providers.connectionExpiresAt')}: ${formatDate(result.expires_at)}` : '';
  const timestamps = [checked, expires].filter(Boolean).join(' · ');
  const detail = [view.message, timestamps].filter(Boolean).join('\n');
  container.dataset.status = view.status;
  container.title = detail;
  container.replaceChildren(badge(view.label, view.tone), ...(!compact && detail ? [el('span', {}, detail)] : []));
  container.hidden = false;
  if (['current', 'testing'].includes(result?.evidence_state) && result.deadline > Date.now()) {
    // 定时器只持有弱引用，离开页面后不会保留整棵DOM。
    const reference = new WeakRef(container);
    const timer = setTimeout(() => {
      const node = reference.deref();
      if (node?.isConnected) renderConnectionEvidence(node, result, { compact });
    }, result.deadline - Date.now() + 1);
    expiryTimers.set(container, timer);
  }
}

import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { el } from '../../components/dom.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';
import { hasProviderSecretField } from './provider-validation.js';

const providerTestResults = new Map();

export function providerTestSummary(provider) {
  const result = providerTestResults.get(provider.id);
  if (!result) return null;
  const ok = result.ok === true;
  const status = safeText(result.status || '-', 48);
  const message = safeText(result.message || (ok ? t('providers.testSuccess') : t('providers.testFailed')), 160);
  const modelFound = result.model_found === true ? t('providers.modelFoundYes') : t('providers.modelFoundNo');
  const elapsed = Number.isFinite(Number(result.elapsed_ms)) ? `${Number(result.elapsed_ms)}ms` : '-';
  return el('div', { class: `provider-test-result ${ok ? 'ok' : 'failed'}` },
    el('span', {}, `${t('providers.testStatus')}: ${status}`),
    el('span', {}, `${t('providers.modelFound')}: ${modelFound}`),
    el('span', {}, `${t('providers.elapsedMs')}: ${elapsed}`),
    el('p', {}, message),
  );
}

export async function testProvider(provider, reload) {
  try {
    const result = await api.post(`/admin/providers/${encodeURIComponent(provider.id)}/test`);
    if (hasProviderSecretField(result)) {
      toast(t('providers.securityError'), 'error');
      return;
    }
    providerTestResults.set(provider.id, result);
    toast(result.ok ? t('providers.testSuccess') : t('providers.testFailed'), result.ok ? 'success' : 'warning');
    await reload();
  } catch (error) {
    const detail = error?.detail;
    if (detail?.status === 'test_not_supported') {
      providerTestResults.set(provider.id, {
        ok: false,
        status: 'test_not_supported',
        message: detail.message || t('providers.testUnsupported'),
      });
      await reload();
      return;
    }
    toast(safeErrorMessage(error, t('providers.testFailed')), 'error');
  }
}

import { t } from '../../i18n.js';
import { el } from '../../components/dom.js';
import { formatDate } from '../../lib/format.js';
import { safeText } from '../../lib/security.js';

export function providerTestSummary(provider, result) {
  const status = result?.status || provider.last_test_status;
  if (!status) return null;
  const ok = result ? result.ok === true : status === 'ok';
  const elapsed = result?.elapsed_ms ?? provider.last_response_ms;
  return el('div', { class: `provider-test-result ${ok ? 'ok' : 'failed'}`, role: 'status' },
    el('span', {}, `${t('providers.testStatus')}: ${safeText(status, 48)}`),
    typeof result?.model_found === 'boolean' ? el('span', {},
      `${t('providers.modelFound')}: ${t(result.model_found ? 'providers.modelFoundYes' : 'providers.modelFoundNo')}`) : null,
    elapsed != null && Number.isFinite(Number(elapsed)) ? el('span', {}, `${t('providers.elapsedMs')}: ${Number(elapsed)}ms`) : null,
    provider.last_test_at ? el('span', {}, `${t('providers.registryLastTest')}: ${formatDate(provider.last_test_at)}`) : null,
    result ? el('p', {}, safeText(result.message || t(ok ? 'providers.testSuccess' : 'providers.testFailed'), 160)) : null,
  );
}

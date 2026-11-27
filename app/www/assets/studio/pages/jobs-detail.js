import { jobDetailContent } from '../components/job-detail-content.js';
import { api } from '../api.js';
import { t } from '../i18n.js';
import { button } from '../components/buttons.js';
import { el, mount } from '../components/dom.js';
import { pageHeader, panel } from '../components/page.js';
import { emptyState, errorState, loadingState } from '../components/states.js';
import { formatDate, shortId } from '../lib/format.js';
import { navigate, createRenderScope } from '../router.js';

function renderDetail(content, detail) {
  mount(content,
    pageHeader({
      kicker: t('jobs.kicker'),
      title: `${t('jobs.detail')} · ${shortId(detail.job_id)}`,
      subtitle: formatDate(detail.created_at),
      actions: [
        button(t('jobs.title'), { onClick: () => navigate('#/jobs') }),
        button(t('assets.title'), {
          onClick: () => {
            sessionStorage.setItem('studio_asset_filter_job_id', detail.job_id);
            navigate('#/assets');
          },
        }),
      ],
    }),
    el('div', { class: 'detail-page panel job-detail-page' }, jobDetailContent(detail, 'h2')),
  );
}

export async function render(params = {}) {
  const content = document.getElementById('content');
  const scope = createRenderScope(content);
  if (!params.id) {
    mount(content,
      pageHeader({ kicker: t('jobs.kicker'), title: t('jobs.detail'), subtitle: t('detail.missingId') }),
      panel({ className: 'detail-section' }, emptyState(t('detail.missingId'), t('detail.missingIdCopy'))),
    );
    return;
  }
  mount(content, loadingState(t('jobs.detailLoading')));
  try {
    const result = await api.get(`/admin/jobs/${encodeURIComponent(params.id)}`);
    if (!scope.isActive()) return;
    renderDetail(content, result?.data || {});
  } catch (_) {
    if (!scope.isActive()) return;
    mount(content,
      pageHeader({
        kicker: t('jobs.kicker'),
        title: t('jobs.detail'),
        subtitle: shortId(params.id),
        actions: [button(t('jobs.title'), { onClick: () => navigate('#/jobs') })],
      }),
      panel({ className: 'detail-section' }, errorState(t('jobs.detailError'), t('detail.notFound'))),
    );
  }
}

import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { recentJobCard } from '../../components/recent-job-card.js';
import { el } from '../../components/dom.js';
import { panel } from '../../components/page.js';
import { emptyState } from '../../components/states.js';

export async function loadRecentImageJobs() {
  const result = await api.get('/jobs?kind=image&limit=4&offset=0');
  return Array.isArray(result?.data) ? result.data : [];
}

export function recentImagesPanel(recentJobs) {
  return panel({ title: t('generateImage.recentImages') },
    el('div', { class: 'panel-body' },
      recentJobs.length ? el('div', { class: 'recent-strip' }, recentJobs.map(recentJobCard)) :
        emptyState(t('generateImage.noRecentJobs')),
    ),
  );
}

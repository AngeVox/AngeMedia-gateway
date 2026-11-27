import { t } from '../i18n.js';
import { displayJobProvider } from '../lib/job-display.js';
import { formatDate, shortId } from '../lib/format.js';
import { safeText } from '../lib/security.js';
import { statusBadge } from './badges.js';
import { linkButton } from './buttons.js';
import { el } from './dom.js';
import { metaGrid } from './page.js';

export function recentJobCard(job) {
  const id = job.id || job.job_id;
  const details = id ? linkButton(t('jobs.detail'), '#/jobs/' + encodeURIComponent(id), { size: 'sm' }) : null;
  details?.setAttribute('aria-label', t('jobs.recentDetail').replace('{id}', shortId(id)));
  return el('article', { class: 'job-card recent-job-card' + (job.status === 'failed' ? ' failed' : '') },
    el('div', { class: 'job-card-header' },
      el('p', { class: 'card-subtitle' }, t('jobs.' + (job.kind || 'image')) + ' · ' + shortId(id)),
      statusBadge(job.status, t('jobs.' + job.status)),
    ),
    el('p', { class: 'card-title recent-job-prompt' }, safeText(job.prompt || t('jobs.noPrompt'), 180)),
    metaGrid([
      { label: t('jobs.provider'), value: safeText(displayJobProvider(job.provider) || '-', 80) },
      { label: t('jobs.model'), value: safeText(job.model || '-', 80) },
      { label: t('jobs.created'), value: formatDate(job.created_at) },
    ]),
    details,
  );
}

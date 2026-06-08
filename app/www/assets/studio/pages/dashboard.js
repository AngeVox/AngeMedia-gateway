import { api } from '../api.js';
import { t } from '../i18n.js';

async function fetchHealth() {
  const res = await fetch('/health', { credentials: 'include' });
  if (!res.ok) throw new Error('Health check failed');
  return res.json();
}

function createStatCard(title) {
  const card = document.createElement('div');
  card.className = 'card stat-card';
  const label = document.createElement('p');
  label.className = 'stat-label';
  label.textContent = title;
  const value = document.createElement('p');
  value.className = 'stat-value';
  card.append(label, value);
  return { card, value };
}

export async function render() {
  const content = document.getElementById('content');
  content.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'page-header';
  const heading = document.createElement('h1');
  heading.className = 'page-heading';
  heading.textContent = t('nav.dashboard');
  header.appendChild(heading);
  content.appendChild(header);

  const loading = document.createElement('div');
  loading.className = 'card section-card text-muted';
  loading.textContent = t('dashboard.loading');
  content.appendChild(loading);

  const statGrid = document.createElement('div');
  statGrid.className = 'stat-grid';
  content.appendChild(statGrid);

  const healthCard = createStatCard(t('dashboard.health'));
  statGrid.appendChild(healthCard.card);

  const sessionCard = createStatCard(t('dashboard.session'));
  statGrid.appendChild(sessionCard.card);

  try {
    const [health, session] = await Promise.all([
      fetchHealth().catch(() => ({ status: t('dashboard.unavailable') })),
      api.get('/admin/session').catch(() => ({ authenticated: false })),
    ]);

    healthCard.value.textContent = `${t('dashboard.statusPrefix')}${health.status || t('dashboard.error')}`;

    if (session.authenticated) {
      sessionCard.value.textContent = `${t('dashboard.loggedInPrefix')}${session.username || t('dashboard.unknown')}`;
    } else {
      sessionCard.value.textContent = t('dashboard.notAuthenticated');
    }
  } catch (err) {
    healthCard.value.textContent = `${t('dashboard.statusPrefix')}${t('dashboard.error')}`;
    sessionCard.value.textContent = t('dashboard.unableToLoadSession');
  } finally {
    loading.remove();
  }
}

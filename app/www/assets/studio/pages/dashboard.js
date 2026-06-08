import { api } from '../api.js';
import { t } from '../i18n.js';

async function fetchHealth() {
  const res = await fetch('/health', { credentials: 'include' });
  if (!res.ok) throw new Error('Health check failed');
  return res.json();
}

function createCard(title) {
  const card = document.createElement('div');
  card.className = 'card';
  const h2 = document.createElement('h2');
  h2.textContent = title;
  card.appendChild(h2);
  return card;
}

export async function render() {
  const content = document.getElementById('content');
  content.innerHTML = '';

  const loading = document.createElement('div');
  loading.className = 'card';
  loading.textContent = t('dashboard.loading');
  content.appendChild(loading);

  const healthCard = createCard(t('dashboard.health'));
  const healthStatus = document.createElement('p');
  healthCard.appendChild(healthStatus);
  content.appendChild(healthCard);

  const sessionCard = createCard(t('dashboard.session'));
  const sessionInfo = document.createElement('p');
  sessionCard.appendChild(sessionInfo);
  content.appendChild(sessionCard);

  try {
    const [health, session] = await Promise.all([
      fetchHealth().catch(() => ({ status: t('dashboard.unavailable') })),
      api.get('/admin/session').catch(() => ({ authenticated: false })),
    ]);

    healthStatus.textContent = `${t('dashboard.statusPrefix')}${health.status || t('dashboard.error')}`;

    if (session.authenticated) {
      sessionInfo.textContent = `${t('dashboard.loggedInPrefix')}${session.username || t('dashboard.unknown')}`;
    } else {
      sessionInfo.textContent = t('dashboard.notAuthenticated');
    }
  } catch (err) {
    healthStatus.textContent = `${t('dashboard.statusPrefix')}${t('dashboard.error')}`;
    sessionInfo.textContent = t('dashboard.unableToLoadSession');
  } finally {
    loading.remove();
  }
}

import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el, mount } from '../../components/dom.js';
import { field, input } from '../../components/forms.js';
import { confirmModal } from '../../components/modal.js?v=web-studio-2j';
import { pageHeader, panel, metricCard } from '../../components/page.js';
import { errorState, loadingState } from '../../components/states.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';
import { safeText } from '../../lib/security.js';

function payload(result) {
  return result?.data && typeof result.data === 'object' ? result.data : {};
}

function kv(items) {
  return el('div', { class: 'diagnostics-kv-list' }, items.map((item) =>
    el('div', { class: 'diagnostics-kv' },
      el('span', {}, item.label),
      el('strong', {}, safeText(item.value, 120)),
    ),
  ));
}

function processText(processes) {
  const dispatcher = processes?.dispatcher ? t('system.running') : t('system.stopped');
  const worker = processes?.worker ? t('system.running') : t('system.stopped');
  return t('system.dispatcher') + ': ' + dispatcher + ' · ' + t('system.worker') + ': ' + worker;
}

function renderDetection(target, result) {
  const data = payload(result);
  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  target.textContent = '';
  if (!candidates.length) {
    target.appendChild(el('p', { class: 'card-subtitle' }, t('system.redisNoResult')));
    return;
  }
  target.appendChild(el('div', { class: 'diagnostics-list' }, candidates.map((item) =>
    el('article', { class: 'diagnostics-row' },
      el('div', {},
        el('strong', {}, item.reachable ? t('system.redisReachable') : t('system.redisUnreachable')),
        el('p', { class: 'card-subtitle' }, safeText(item.host || '-', 253) + ':' + String(Number(item.port || 0)) + ' · ' + (item.tls ? 'TLS' : 'TCP')),
      ),
      el('span', { class: 'soft-pill' }, safeText(item.source || '-', 32)),
    ),
  )));
}

function renderSystem(content, summary, reload) {
  const busy = Number(summary.active_jobs || 0) > 0 || Number(summary.active_dispatches || 0) > 0;
  const redisInput = input({
    type: 'password',
    autocomplete: 'off',
    placeholder: 'redis://127.0.0.1:6379/0',
  });
  const detection = el('div', { class: 'system-detection-result' });

  const detect = button(t('system.detectRedis'), {
    onClick: async () => {
      detect.disabled = true;
      try {
        const value = redisInput.value.trim();
        const result = await api.post('/admin/system/queue/redis/detect', { redis_url: value || null });
        renderDetection(detection, result);
      } catch (error) {
        detection.textContent = '';
        detection.appendChild(errorState(safeErrorMessage(error, t('system.detectFailed'))));
      } finally {
        detect.disabled = false;
      }
    },
  });

  async function switchBackend(backend) {
    const toCelery = backend === 'celery';
    confirmModal({
      title: toCelery ? t('system.switchRedisTitle') : t('system.switchLocalTitle'),
      message: toCelery ? t('system.switchRedisMessage') : t('system.switchLocalMessage'),
      confirmLabel: t('common.confirm'),
      cancelLabel: t('common.cancel'),
      onConfirm: async () => {
        try {
          const value = redisInput.value.trim();
          await api.post('/admin/system/queue/switch', {
            backend,
            redis_url: toCelery && value ? value : null,
          });
          redisInput.value = '';
          toast(toCelery ? t('system.switchedRedis') : t('system.switchedLocal'), 'success');
          await reload();
        } catch (error) {
          toast(safeErrorMessage(error, t('system.switchFailed')), 'error');
        }
      },
    });
  }

  const canSwitch = summary.can_switch === true;
  mount(content,
    pageHeader({
      kicker: t('system.kicker'),
      title: t('system.title'),
      subtitle: t('system.subtitle'),
      actions: [button(t('common.refresh'), { onClick: reload })],
    }),
    el('div', { class: 'metric-grid' },
      metricCard({
        label: t('system.currentBackend'),
        value: safeText(summary.backend || '-', 32),
        meta: summary.backend === 'local' ? t('system.localRecommended') : t('system.redisMode'),
        tone: summary.backend === 'local' ? 'teal' : 'blue',
      }),
      metricCard({
        label: t('system.activeJobs'),
        value: String(Number(summary.active_jobs || 0)),
        meta: t('system.activeDispatches') + ': ' + String(Number(summary.active_dispatches || 0)),
        tone: busy ? 'gold' : 'teal',
      }),
      metricCard({
        label: t('system.runtimeControl'),
        value: canSwitch ? t('system.available') : t('system.externalManaged'),
        meta: safeText(summary.platform || '-', 32),
        tone: canSwitch ? 'violet' : 'gold',
      }),
    ),
    el('div', { class: 'diagnostics-grid' },
      panel({
        title: t('system.queueTitle'),
        subtitle: t('system.queueCopy'),
        className: 'diagnostics-section',
      },
        kv([
          { label: t('system.backend'), value: summary.backend || '-' },
          { label: t('system.redisConfigured'), value: summary.redis_configured ? t('system.yes') : t('system.no') },
          { label: t('system.processes'), value: processText(summary.processes || {}) },
          { label: t('system.switchState'), value: busy ? t('system.busyBlocked') : (canSwitch ? t('system.readyToSwitch') : t('system.externalManaged')) },
        ]),
        el('div', { class: 'action-row' },
          button(t('system.useLocal'), {
            onClick: () => switchBackend('local'),
            disabled: !canSwitch || busy || summary.backend === 'local',
          }),
        ),
      ),
      panel({
        title: t('system.redisTitle'),
        subtitle: t('system.redisCopy'),
        className: 'diagnostics-section',
      },
        field(t('system.redisUrl'), redisInput, { help: t('system.redisUrlHelp') }),
        el('div', { class: 'action-row' },
          detect,
          button(summary.backend === 'celery' ? t('system.rebindRedis') : t('system.useRedis'), {
            variant: 'primary',
            onClick: () => switchBackend('celery'),
            disabled: !canSwitch || busy,
          }),
        ),
        detection,
      ),
      panel({
        title: t('system.safetyTitle'),
        subtitle: t('system.safetyCopy'),
        className: 'diagnostics-section diagnostics-section-wide',
      },
        el('p', { class: 'card-subtitle' }, t('system.safetyDetail')),
      ),
    ),
  );
}

export async function render() {
  const content = document.getElementById('content');
  async function reload() {
    mount(content, loadingState(t('system.loading')));
    try {
      const result = await api.get('/admin/system/queue');
      renderSystem(content, payload(result), reload);
    } catch (error) {
      mount(content,
        pageHeader({ kicker: t('system.kicker'), title: t('system.title'), subtitle: t('system.subtitle') }),
        errorState(safeErrorMessage(error, t('system.loadFailed'))),
      );
    }
  }
  await reload();
}

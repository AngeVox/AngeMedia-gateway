import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input } from '../../components/forms.js';
import { panel } from '../../components/page.js';
import { toast } from '../../components/toast.js';
import { safeErrorMessage } from '../../lib/safe-error.js';

function settingValue(config, key, fallback) {
  const value = config?.settings?.[key];
  if (value !== undefined && value !== null && value !== '') return String(value);
  return fallback;
}

function timeoutInput(value, { min, max }) {
  return input({
    type: 'number',
    min: String(min),
    max: String(max),
    step: '1',
    value,
    inputmode: 'numeric',
  });
}

export function renderRuntimeSettingsPanel(config) {
  const imageTimeout = timeoutInput(
    settingValue(config, 'IMAGE_PROVIDER_TIMEOUT', settingValue(config, 'HTTP_TIMEOUT', '60')),
    { min: 5, max: 600 },
  );
  const videoTimeout = timeoutInput(
    settingValue(config, 'VIDEO_PROVIDER_TIMEOUT', settingValue(config, 'AGNES_VIDEO_SUBMIT_TIMEOUT', '900')),
    { min: 30, max: 1800 },
  );
  const status = el('p', { class: 'field-help provider-runtime-status' }, t('providers.runtimeTimeoutHelp'));
  const save = button(t('providers.runtimeTimeoutSave'), {
    size: 'sm',
    variant: 'primary',
    onClick: async () => {
      save.disabled = true;
      try {
        await api.post('/admin/config', {
          settings: {
            IMAGE_PROVIDER_TIMEOUT: imageTimeout.value.trim(),
            VIDEO_PROVIDER_TIMEOUT: videoTimeout.value.trim(),
          },
        });
        status.textContent = t('providers.runtimeTimeoutSaved');
        toast(t('providers.runtimeTimeoutSaved'), 'success');
      } catch (error) {
        status.textContent = safeErrorMessage(error, t('providers.runtimeTimeoutError'));
        toast(status.textContent, 'error');
      } finally {
        save.disabled = false;
      }
    },
  });

  return panel({
    title: t('providers.runtimeTimeoutTitle'),
    subtitle: t('providers.runtimeTimeoutSubtitle'),
    className: 'provider-runtime-panel',
    actions: [save],
  },
    el('div', { class: 'provider-runtime-grid' },
      field(t('providers.imageTimeout'), imageTimeout, { help: t('providers.imageTimeoutHelp') }),
      field(t('providers.videoTimeout'), videoTimeout, { help: t('providers.videoTimeoutHelp') }),
    ),
    status,
  );
}

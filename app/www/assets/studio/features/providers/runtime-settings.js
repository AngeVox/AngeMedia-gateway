import { api } from '../../api.js';
import { t } from '../../i18n.js';
import { button } from '../../components/buttons.js';
import { el } from '../../components/dom.js';
import { field, input } from '../../components/forms.js';
import { panel } from '../../components/page.js';
import { loadAdminConfig } from './provider-api.js';
import { bindSettingsForm } from './settings-form.js';

function timeoutInput(min, max) {
  return input({ type: 'number', min: String(min), max: String(max), step: 'any', inputmode: 'decimal' });
}

export function renderRuntimeSettingsPanel() {
  const imageTimeout = timeoutInput(5, 600);
  const videoTimeout = timeoutInput(30, 1800);
  const status = el('p', { class: 'field-help provider-runtime-status', role: 'status' });
  const save = button(t('providers.runtimeTimeoutSave'), {
    size: 'sm',
    variant: 'primary',
    onClick: () => {
      if (form.isLocked() || !imageTimeout.reportValidity() || !videoTimeout.reportValidity()) return;
      return form.save({ settings: {
        IMAGE_PROVIDER_TIMEOUT: imageTimeout.value.trim(),
        VIDEO_PROVIDER_TIMEOUT: videoTimeout.value.trim(),
      } }, 'providers.runtimeTimeoutSaved', result => {
        apply(result);
        return t('providers.runtimeTimeoutSaved');
      });
    },
  });
  const section = panel({
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
  function apply({ settings }) {
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error();
    imageTimeout.value = String(settings.IMAGE_PROVIDER_TIMEOUT || settings.HTTP_TIMEOUT || '60');
    videoTimeout.value = String(settings.VIDEO_PROVIDER_TIMEOUT || settings.AGNES_VIDEO_SUBMIT_TIMEOUT || '900');
    return t('providers.runtimeTimeoutHelp');
  }
  const form = bindSettingsForm(section, {
    load: loadAdminConfig,
    write: payload => api.post('/admin/config', payload),
    saveError: 'providers.runtimeTimeoutError',
    status,
    apply,
    sync: locked => { save.disabled = imageTimeout.disabled = videoTimeout.disabled = locked; },
  });
  return section;
}

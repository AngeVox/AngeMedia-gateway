import { t } from '../i18n.js';
export { displayJobProvider as actionProvider } from './job-display.js';

export function actionText(key, fallback = 'generic_error') {
  const translationKey = 'assistant.action.' + key;
  const translated = t(translationKey);
  return translated === translationKey ? t('assistant.action.' + fallback) : translated;
}

export const actionVersion = (version) => t('assistant.action.version').replace('{version}', version);

export function actionPolicyText(field, value) {
  return actionText(field + '.' + value, field === 'billing' ? 'billingUnknown' : 'reviewUnknown');
}

export function actionValue(key, value) {
  if (value === null || value === undefined) return t('assistant.action.value.unspecified');
  if (typeof value === 'boolean') return t(value ? 'common.yes' : 'common.no');
  if (typeof value === 'string') {
    const imageKey = 'generateImage.value.' + key + '.' + value;
    const imageValue = t(imageKey);
    if (imageValue !== imageKey) return imageValue;
    const actionKey = 'assistant.action.value.' + key + '.' + value;
    const localized = t(actionKey);
    if (localized !== actionKey) return localized;
    if (key === 'mode') {
      const modeKey = 'generateVideo.mode.' + (value === 'keyframes' ? 'keyframe' : value);
      const mode = t(modeKey);
      if (mode !== modeKey) return mode;
    }
  }
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}

export function actionField(key) {
  const imageKey = 'generateImage.param.' + key;
  const localized = t(imageKey);
  if (localized !== imageKey) return localized;
  if (key === 'seconds') return t('generateVideo.param.seconds');
  const actionKey = 'assistant.action.field.' + key;
  const field = t(actionKey);
  return field === actionKey ? key : field;
}

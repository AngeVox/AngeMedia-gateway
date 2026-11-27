import { t } from '../i18n.js';
import { noticeModal } from './modal.js?v=web-studio-2j';

export function showWipFeature({ title = t('common.unavailable'), message = t('wip.copy') } = {}) {
  return noticeModal({
    title,
    message,
    actionLabel: t('common.close'),
    tone: 'wip',
  });
}

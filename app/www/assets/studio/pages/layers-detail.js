import { t } from '../i18n.js';
import { button } from '../components/buttons.js';
import { el, mount } from '../components/dom.js';
import { layerGroupPreview } from '../components/layer-group.js';
import { pageHeader } from '../components/page.js';
import { navigate } from '../router.js';

export function render({ id }) {
  const [jobId, query] = id.split('?');
  const selected = Number(new URLSearchParams(query).get('layer'));
  mount(document.getElementById('content'),
    pageHeader({ title: t('layers.title'), actions: [button(t('assets.title'), { onClick: () => navigate('#/assets') })] }),
    el('div', { class: 'panel detail-page' },
      el('div', { class: 'panel-body' }, layerGroupPreview(jobId, selected)),
    ),
  );
}

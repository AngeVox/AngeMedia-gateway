import { t } from '../../i18n.js';
import { parseMediaUrls } from './media-inputs.js';

export class VideoReferenceInputError extends Error {}

export function publicReferenceSpec(model) {
  const spec = model?.ref_input_spec && typeof model.ref_input_spec === 'object' ? model.ref_input_spec : {};
  const formats = Array.isArray(spec.formats) ? spec.formats : [];
  const roles = Array.isArray(spec.roles) ? spec.roles : [];
  return formats.includes('url') ? { roles, maxTotal: Number(spec.max_total || 1) } : null;
}

export function supportsReferenceImage(model) {
  const refInputs = model?.ref_inputs && typeof model.ref_inputs === 'object' ? model.ref_inputs : {};
  return model?.capabilities?.image_to_video === true || Object.hasOwn(refInputs, 'image');
}

// 仅组合提交时捕获的值，不持有DOM，不读取后来修改的草稿。
export function applyVideoReferences(payload, model, values, uploaded) {
  if (!supportsReferenceImage(model)) return;
  const spec = publicReferenceSpec(model);
  if (!spec) {
    const selectedReference = uploaded || values.asset;
    if (selectedReference) payload.image = selectedReference;
    return;
  }
  const selectedReferences = [...uploaded, ...values.assets];
  const firstFrame = values.firstFrame.trim(), lastFrame = values.lastFrame.trim();
  const urls = parseMediaUrls(values.urls, spec.maxTotal, t('generateVideo.referenceTooMany').replace('{count}', String(spec.maxTotal)));
  const mode = String(payload.mode || '').trim().toLowerCase();
  if (mode === 'keyframe') {
    if (urls.length) throw new VideoReferenceInputError(t('generateVideo.keyframeMediaConflict'));
    if ((firstFrame && selectedReferences[0]) || (lastFrame && selectedReferences[1])) throw new VideoReferenceInputError(t('generateVideo.keyframeDuplicate'));
    if (selectedReferences.length > 2) throw new VideoReferenceInputError(t('generateVideo.keyframeLocalLimit'));
    if (firstFrame) payload.first_frame = firstFrame;
    else if (selectedReferences[0]) payload.first_frame = selectedReferences[0];
    if (lastFrame) payload.last_frame = lastFrame;
    else if (selectedReferences[1]) payload.last_frame = selectedReferences[1];
    if (!spec.roles.includes('images') && !payload.first_frame) throw new VideoReferenceInputError(t('generateVideo.firstFrameRequired'));
    return;
  }
  if (firstFrame || lastFrame) throw new VideoReferenceInputError(t('generateVideo.frameModeRequired'));
  const combined = [...selectedReferences, ...urls];
  if (combined.length > spec.maxTotal) throw new VideoReferenceInputError(t('generateVideo.referenceTooMany').replace('{count}', String(spec.maxTotal)));
  if (combined.length) {
    if (!spec.roles.includes('images')) throw new VideoReferenceInputError(t('generateVideo.frameModeRequired'));
    payload.images = combined;
    if (!mode || mode === 'text') payload.mode = 'reference';
  }
}

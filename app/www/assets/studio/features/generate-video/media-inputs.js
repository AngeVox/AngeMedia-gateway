import { t } from '../../i18n.js';
import { el } from '../../components/dom.js';
import { field, input, textarea, toggle } from '../../components/forms.js';

export function parseMediaUrls(value, maxTotal, message) {
  const urls = String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  if (urls.length > maxTotal) throw new Error(message);
  return urls;
}

function publicUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error(t('generateVideo.mediaUrlInvalid')); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error(t('generateVideo.mediaUrlInvalid'));
  }
  return value;
}

export function createVideoMediaInputs() {
  const audios = textarea({ name: 'audios', rows: 3, placeholder: 'https://example.com/audio.mp3' });
  const video = input({ name: 'video_url', type: 'url', placeholder: 'https://example.com/reference.mp4' });
  const start = input({ name: 'video_start_seconds', type: 'number', min: 0, step: 'any', value: '0' });
  const audioToggle = toggle(t('generateVideo.requireVideoAudio'), { name: 'video_require_audio' });
  const requireAudio = audioToggle.querySelector('input');

  function render(model) {
    const contract = model?.video_contract;
    const stale = audios.value.trim() || video.value.trim();
    if (!contract && !stale) return null;
    audios.disabled = !contract?.max_audios;
    video.disabled = !contract?.max_videos;
    start.disabled = video.disabled;
    requireAudio.disabled = video.disabled;
    return el('div', { class: 'form-stack compact-stack video-media-inputs' },
      field(t('generateVideo.audioUrls'), audios, {
        help: t('generateVideo.audioUrlsHelp').replace('{count}', String(contract?.max_audios || 0)),
      }),
      field(t('generateVideo.videoUrl'), video, {
        help: t(contract?.max_videos ? 'generateVideo.videoUrlHelp' : 'generateVideo.videoUnsupported'),
      }),
      contract?.max_videos ? field(t('generateVideo.videoStart'), start) : null,
      contract?.max_videos ? audioToggle : null,
      el('p', { class: 'field-help' }, t('generateVideo.mediaContentHelp')),
    );
  }

  function capture() {
    return { audios: audios.value, video: video.value, start: start.value, requireAudio: requireAudio.checked };
  }

  function apply(payload, model, values = capture()) {
    const contract = model?.video_contract;
    const audioUrls = parseMediaUrls(values.audios, contract?.max_audios || 0, t('generateVideo.audioTooMany'));
    const videoUrl = values.video.trim();
    if (videoUrl && !contract?.max_videos) throw new Error(t('generateVideo.videoUnsupported'));
    if ((audioUrls.length || videoUrl) && payload.mode === 'keyframe') {
      throw new Error(t('generateVideo.keyframeMediaConflict'));
    }
    if (audioUrls.length) payload.audios = audioUrls.map(publicUrl);
    if (videoUrl) {
      const startSeconds = Number(values.start);
      if (!Number.isFinite(startSeconds) || startSeconds < 0) throw new Error(t('generateVideo.videoStartInvalid'));
      payload.videos = [{ url: publicUrl(videoUrl), start_seconds: startSeconds, require_audio: values.requireAudio }];
    }
    if (audioUrls.length || videoUrl) payload.mode = 'reference';
    const total = (payload.images?.length || 0) + audioUrls.length + (videoUrl ? 1 : 0);
    if (contract && total > contract.max_total) throw new Error(t('generateVideo.mediaTooMany'));
  }

  function clear() {
    audios.value = '';
    video.value = '';
    start.value = '0';
    requireAudio.checked = false;
  }

  return { render, apply, capture, clear };
}

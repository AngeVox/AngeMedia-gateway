import { t } from '../i18n.js';
import { openAssistantChat } from './assistant-chat.js?v=web-studio-2h';
import { el } from './dom.js';

const STORAGE_KEY = 'studio_ange_pet_position';
let mounted = false;

function savedPosition() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (!parsed || typeof parsed !== 'object') return null;
    const x = Number(parsed.x);
    const y = Number(parsed.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  } catch (_) {
    return null;
  }
}

function clampPosition(x, y, node) {
  const width = node.offsetWidth || 58;
  const height = node.offsetHeight || 58;
  return {
    x: Math.max(8, Math.min(window.innerWidth - width - 8, x)),
    y: Math.max(8, Math.min(window.innerHeight - height - 8, y)),
  };
}

function setPosition(node, x, y, persist = false) {
  const next = clampPosition(x, y, node);
  node.style.left = `${next.x}px`;
  node.style.top = `${next.y}px`;
  node.style.right = 'auto';
  node.style.bottom = 'auto';
  if (persist) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }
}

function resetMobilePosition(node) {
  node.style.left = '';
  node.style.top = '';
  node.style.right = '';
  node.style.bottom = '';
}

export function mountAngePet() {
  if (mounted || document.querySelector('[data-ange-pet="true"]')) return;
  mounted = true;
  const pet = el('button', {
    type: 'button',
    class: 'ange-pet',
    title: t('angePet.title'),
    ariaLabel: t('angePet.title'),
    dataset: { angePet: 'true' },
  },
    el('span', { class: 'ange-pet-core', ariaHidden: 'true' },
      el('span', { class: 'ange-pet-face' }, 'AI'),
    ),
    el('span', { class: 'ange-pet-label' }, t('angePet.label')),
  );

  let dragging = false;
  let moved = false;
  let origin = null;

  pet.addEventListener('pointerdown', (event) => {
    if (window.matchMedia('(max-width: 640px)').matches) return;
    dragging = true;
    moved = false;
    origin = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      left: pet.offsetLeft,
      top: pet.offsetTop,
    };
    pet.setPointerCapture(event.pointerId);
  });

  pet.addEventListener('pointermove', (event) => {
    if (!dragging || !origin) return;
    const dx = event.clientX - origin.pointerX;
    const dy = event.clientY - origin.pointerY;
    if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
    setPosition(pet, origin.left + dx, origin.top + dy);
  });

  pet.addEventListener('pointerup', (event) => {
    if (dragging && origin) {
      const dx = event.clientX - origin.pointerX;
      const dy = event.clientY - origin.pointerY;
      setPosition(pet, origin.left + dx, origin.top + dy, true);
    }
    dragging = false;
    origin = null;
  });

  pet.addEventListener('click', () => {
    if (moved) {
      moved = false;
      return;
    }
    openAssistantChat();
  });

  document.body.appendChild(pet);
  const position = savedPosition();
  if (position && !window.matchMedia('(max-width: 640px)').matches) {
    setPosition(pet, position.x, position.y);
  }
  window.addEventListener('resize', () => {
    if (window.matchMedia('(max-width: 640px)').matches) {
      resetMobilePosition(pet);
      return;
    }
    const current = savedPosition();
    if (current) setPosition(pet, current.x, current.y);
  });
}

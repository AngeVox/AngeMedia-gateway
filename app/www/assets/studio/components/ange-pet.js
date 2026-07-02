import { t } from '../i18n.js';
import { openAssistantChat } from './assistant-chat.js?v=web-studio-2h';
import { el } from './dom.js';

const STORAGE_KEY = 'studio_ange_pet_position';
const IDLE_DOCK_MS = 9000;
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

function setRawPosition(node, x, y) {
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
  node.style.right = 'auto';
  node.style.bottom = 'auto';
}

function nearestEdge(node, x) {
  const width = node.offsetWidth || 58;
  return x + width / 2 < window.innerWidth / 2 ? 'left' : 'right';
}

function dockToEdge(node) {
  if (window.matchMedia('(max-width: 640px)').matches) return;
  const width = node.offsetWidth || 64;
  const currentX = node.offsetLeft;
  const currentY = node.offsetTop;
  const edge = nearestEdge(node, currentX);
  const dockX = edge === 'left' ? -38 : window.innerWidth - 26;
  setRawPosition(node, dockX, currentY);
  node.classList.add('ange-pet-peek');
  node.classList.toggle('ange-pet-stuck-left', edge === 'left');
  node.classList.toggle('ange-pet-stuck-right', edge === 'right');
  node.dataset.dockedEdge = edge;
  node.dataset.freeX = String(Math.max(8, Math.min(window.innerWidth - width - 8, currentX)));
  node.dataset.freeY = String(currentY);
}

function undock(node) {
  if (!node.classList.contains('ange-pet-peek')) return;
  const width = node.offsetWidth || 64;
  const edge = node.dataset.dockedEdge || nearestEdge(node, node.offsetLeft);
  const x = Number(node.dataset.freeX || (edge === 'left' ? 8 : window.innerWidth - width - 8));
  const y = Number(node.dataset.freeY || node.offsetTop);
  node.classList.remove('ange-pet-peek', 'ange-pet-stuck-left', 'ange-pet-stuck-right');
  setPosition(node, x, y);
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
      el('span', { class: 'ange-pet-bot-antenna' }),
      el('span', { class: 'ange-pet-bot-head' },
        el('span', { class: 'ange-pet-bot-eye' }),
        el('span', { class: 'ange-pet-bot-eye' }),
      ),
      el('span', { class: 'ange-pet-bot-body' },
        el('span', { class: 'ange-pet-bot-play' }),
      ),
    ),
    el('span', { class: 'ange-pet-label' }, t('angePet.label')),
  );

  let dragging = false;
  let moved = false;
  let origin = null;
  let idleTimer = null;

  function scheduleDock() {
    if (window.matchMedia('(max-width: 640px)').matches) return;
    if (idleTimer) window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(() => dockToEdge(pet), IDLE_DOCK_MS);
  }

  function wake() {
    if (idleTimer) window.clearTimeout(idleTimer);
    undock(pet);
  }

  pet.addEventListener('pointerdown', (event) => {
    if (window.matchMedia('(max-width: 640px)').matches) return;
    wake();
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
    scheduleDock();
  });

  pet.addEventListener('click', () => {
    wake();
    if (moved) {
      moved = false;
      scheduleDock();
      return;
    }
    openAssistantChat();
    scheduleDock();
  });
  pet.addEventListener('pointerenter', wake);
  pet.addEventListener('pointerleave', scheduleDock);
  pet.addEventListener('focus', wake);
  pet.addEventListener('blur', scheduleDock);

  document.body.appendChild(pet);
  const position = savedPosition();
  if (position && !window.matchMedia('(max-width: 640px)').matches) {
    setPosition(pet, position.x, position.y);
  } else if (!window.matchMedia('(max-width: 640px)').matches) {
    setPosition(pet, 14, Math.round(window.innerHeight * 0.52));
  }
  scheduleDock();
  window.addEventListener('resize', () => {
    if (window.matchMedia('(max-width: 640px)').matches) {
      if (idleTimer) window.clearTimeout(idleTimer);
      pet.classList.remove('ange-pet-peek', 'ange-pet-stuck-left', 'ange-pet-stuck-right');
      resetMobilePosition(pet);
      return;
    }
    const current = savedPosition();
    if (current) setPosition(pet, current.x, current.y);
    scheduleDock();
  });
}

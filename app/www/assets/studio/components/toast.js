let container;
function ensure() {
  if (!container) container = document.getElementById('toast-container');
  return container;
}
export function toast(message, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  ensure().appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

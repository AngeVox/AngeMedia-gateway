export async function render(params) {
  const id = params.id || '';
  const el = document.getElementById('content');
  el.textContent = '';
  const card = document.createElement('div');
  card.className = 'card';
  const h2 = document.createElement('h2');
  h2.textContent = 'Job Detail';
  const p = document.createElement('p');
  p.textContent = id ? `WIP skeleton — job ${id} detail will appear here.` : 'WIP skeleton — job detail will appear here.';
  card.append(h2, p);
  el.appendChild(card);
}

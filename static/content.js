'use strict';

// Handle failed portraits without inline handlers (also works with the server CSP).
function markMissingPortrait(img) {
  if (!(img instanceof HTMLImageElement) || !img.matches('[data-fighter-portrait], .fighter-img')) return;
  img.classList.add('portrait-error');
  const fallback = img.nextElementSibling;
  if (fallback?.classList.contains('fighter-initials')) fallback.style.display = 'flex';
}
document.addEventListener('error', event => markMissingPortrait(event.target), true);
document.querySelectorAll('[data-fighter-portrait], .fighter-img').forEach(img => {
  if (img.complete && !img.naturalWidth) markMissingPortrait(img);
});

// Directories retain useful rendered content while searching the full name index.
const directorySource = document.getElementById('directory-data');
if (directorySource) {
  const entries = JSON.parse(directorySource.textContent);
  const grid = document.getElementById('directory-grid');
  const search = document.getElementById('directory-search');
  const previous = document.getElementById('directory-prev');
  const next = document.getElementById('directory-next');
  let page = 1;
  const normalize = value => value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  function tile(entry) {
    const link = document.createElement('a');
    link.className = 'fighter-tile'; link.href = entry.path;
    const portrait = document.createElement('span');
    portrait.className = 'portrait'; portrait.setAttribute('aria-hidden', 'true');
    const initials = document.createElement('span');
    initials.className = 'portrait-initials';
    const names = entry.name.split(' ');
    initials.textContent = (names[0][0] || '') + (names[names.length - 1][0] || '');
    portrait.append(initials);
    if (entry.image) {
      const image = document.createElement('img');
      image.src = entry.image; image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
      image.setAttribute('data-fighter-portrait', ''); portrait.append(image);
    }
    const copy = document.createElement('span'); copy.className = 'fighter-tile-copy';
    for (const [tag, text] of [['strong',entry.name],['span',entry.record || 'View UFC record'],['small',entry.weight_class || 'Fighter profile']]) {
      const element = document.createElement(tag); element.textContent = text; copy.append(element);
    }
    const arrow = document.createElement('span'); arrow.className = 'tile-arrow'; arrow.setAttribute('aria-hidden', 'true'); arrow.textContent = '↗';
    link.append(portrait,copy,arrow); return link;
  }
  function renderDirectory() {
    const query = normalize(search.value.trim());
    const matches = entries.filter(entry => normalize(entry.name).includes(query));
    const pages = Math.max(1,Math.ceil(matches.length / 48)); page = Math.min(page,pages);
    grid.replaceChildren(...matches.slice((page - 1) * 48,page * 48).map(tile));
    if (!matches.length) {
      const message = document.createElement('p'); message.className = 'directory-empty'; message.textContent = 'No fighters match that search.'; grid.append(message);
    }
    document.getElementById('directory-summary').textContent = `${matches.length.toLocaleString()} fighter profiles${query ? ' match your search' : ' · Browse records, history, and upcoming fights'}`;
    document.getElementById('directory-page').textContent = `Page ${page} of ${pages}`;
    previous.disabled = page <= 1; next.disabled = page >= pages;
  }
  search.addEventListener('input', () => { page = 1; renderDirectory(); });
  previous.addEventListener('click', () => { page--; renderDirectory(); grid.scrollIntoView({block:'start'}); });
  next.addEventListener('click', () => { page++; renderDirectory(); grid.scrollIntoView({block:'start'}); });
  renderDirectory();
}

document.querySelectorAll('[data-filter-list]').forEach(input => {
  const list = document.getElementById(input.dataset.filterList);
  const items = [...list.querySelectorAll('[data-search-item]')];
  const empty = document.querySelector(`[data-empty-for="${list.id}"]`);
  const controls = document.createElement('div'); controls.className = 'directory-pagination';
  const previous = document.createElement('button'); previous.type = 'button'; previous.textContent = '← Previous';
  const label = document.createElement('span'); label.setAttribute('aria-live','polite');
  const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Next →';
  controls.append(previous,label,next); list.after(controls);
  let page = 1;
  function filter() {
    const query = input.value.trim().toLowerCase();
    const matches = items.filter(item => (item.textContent + ' ' + (item.querySelector('time')?.dateTime || '')).toLowerCase().includes(query));
    const pages = Math.max(1,Math.ceil(matches.length / 24)); page = Math.min(page,pages);
    items.forEach(item => { item.hidden = true; });
    matches.slice((page - 1) * 24,page * 24).forEach(item => { item.hidden = false; });
    if (empty) empty.hidden = matches.length > 0;
    controls.hidden = !matches.length; previous.disabled = page <= 1; next.disabled = page >= pages;
    label.textContent = `Page ${page} of ${pages}`;
  }
  input.addEventListener('input', () => { page = 1; filter(); });
  previous.addEventListener('click', () => { page--; filter(); list.scrollIntoView({block:'start'}); });
  next.addEventListener('click', () => { page++; filter(); list.scrollIntoView({block:'start'}); });
  filter();
});

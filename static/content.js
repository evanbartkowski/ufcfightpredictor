'use strict';

const normalizeRecordSearch = value => String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
const recordSearchTokens = value => normalizeRecordSearch(value).split(/\s+/).filter(Boolean);

// Fetch the cross-directory index only when somebody searches the archive.
if (document.querySelector('[data-archive-finder]')) {
  const input = document.getElementById('archive-query');
  const output = document.getElementById('archive-search-results');
  const status = document.getElementById('archive-search-status');
  const more = document.getElementById('archive-more');
  let indexRequest = null, kind = 'all', limit = 12, generation = 0;
  function resultCard(entry) {
    const link = document.createElement('a'); link.href = entry.path; link.className = 'archive-result';
    const labels = {fighters:'FIGHTER PROFILE',events:'EVENT CARD',forecasts:'ISSUED FORECAST',model:'MODEL METHODS'};
    const mark = document.createElement('span'); mark.className = 'archive-result-mark'; mark.setAttribute('aria-hidden','true');
    if (entry.image) {
      mark.classList.add('portrait');
      const fallback = document.createElement('span'); fallback.className = 'portrait-initials'; fallback.textContent = entry.title.split(' ').map(n=>n[0]).slice(0,2).join('');
      const image = document.createElement('img'); image.src = entry.image; image.alt = ''; image.loading = 'lazy'; image.setAttribute('data-fighter-portrait','');
      mark.append(fallback,image);
    } else mark.textContent = entry.kind === 'events' ? 'EV' : entry.kind === 'forecasts' ? 'VS' : entry.kind === 'model' ? 'AI' : entry.title.split(' ').map(n=>n[0]).slice(0,2).join('');
    const copy = document.createElement('span'); copy.className = 'archive-result-copy';
    for (const [tag,text] of [['small',labels[entry.kind]],['strong',entry.title],['span',entry.detail]]) {
      const element = document.createElement(tag); element.textContent = text; copy.append(element);
    }
    const arrow = document.createElement('span'); arrow.className = 'tile-arrow'; arrow.setAttribute('aria-hidden','true'); arrow.textContent = '↗';
    link.append(mark,copy,arrow); return link;
  }
  async function searchArchive() {
    const current = ++generation;
    const tokens = recordSearchTokens(input.value);
    output.replaceChildren(); more.hidden = true;
    if (normalizeRecordSearch(input.value).length < 2) {
      status.textContent = 'Try a fighter’s name, “UFC 334”, or an event date.'; return;
    }
    status.textContent = 'Searching the archive…';
    try {
      if (!indexRequest) indexRequest = fetch('/api/search-index.json',{cache:'no-cache',signal:AbortSignal.timeout(15000)}).then(response => {
        if (!response.ok) throw Error('Archive search unavailable'); return response.json();
      }).then(payload => payload.items.map(entry => ({...entry,searchText:normalizeRecordSearch([entry.title,entry.detail,entry.terms].join(' '))})));
      const entries = await indexRequest;
      if (current !== generation) return;
      const matches = entries.filter(entry => (kind === 'all' || entry.kind === kind) && tokens.every(token=>entry.searchText.includes(token)));
      matches.sort((a,b) => Number(normalizeRecordSearch(b.title).includes(normalizeRecordSearch(input.value))) - Number(normalizeRecordSearch(a.title).includes(normalizeRecordSearch(input.value))));
      output.replaceChildren(...matches.slice(0,limit).map(resultCard));
      status.textContent = matches.length ? `${matches.length.toLocaleString()} ${matches.length === 1 ? 'record' : 'records'} found · Showing ${Math.min(matches.length,limit)}` : 'No records found. Try another fighter, event name, or date.';
      more.hidden = matches.length <= limit;
    } catch (_) {
      indexRequest = null;
      if (current === generation) status.textContent = 'Archive search is temporarily unavailable. You can still browse the collections above.';
    }
  }
  input.addEventListener('input', () => { limit = 12; searchArchive(); });
  more.addEventListener('click', () => { limit += 12; searchArchive(); });
  document.querySelectorAll('[data-archive-kind]').forEach(button => button.addEventListener('click', () => {
    kind = button.dataset.archiveKind; limit = 12;
    document.querySelectorAll('[data-archive-kind]').forEach(item => { item.classList.toggle('active',item===button); item.setAttribute('aria-pressed',String(item===button)); });
    searchArchive();
  }));
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select,[contenteditable="true"]')) return;
    event.preventDefault();
    if (typeof showTab === 'function') showTab('fighters');
    input.focus();
  });
}

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
  const sort = document.getElementById('directory-sort');
  function tile(entry) {
    const link = document.createElement('a');
    link.className = 'fighter-tile'; link.href = entry.path;
    link.classList.toggle('fighter-upcoming',Boolean(entry.upcoming));
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
    if (entry.upcoming) { const badge = document.createElement('small'); badge.className = 'roster-upcoming'; badge.textContent = 'UPCOMING BOUT'; copy.append(badge); }
    for (const [tag, text] of [['strong',entry.name],['span',entry.record || 'View UFC record'],['small',`${entry.bouts} recorded UFC bouts`]]) {
      const element = document.createElement(tag); element.textContent = text; copy.append(element);
    }
    const arrow = document.createElement('span'); arrow.className = 'tile-arrow'; arrow.setAttribute('aria-hidden', 'true'); arrow.textContent = '↗';
    link.append(portrait,copy,arrow); return link;
  }
  function renderDirectory() {
    const query = normalizeRecordSearch(search.value);
    const tokens = recordSearchTokens(search.value);
    const matches = entries.filter(entry => tokens.every(token=>normalizeRecordSearch(entry.name).includes(token)));
    if (sort?.value === 'name') matches.sort((a,b)=>a.name.localeCompare(b.name));
    else if (sort?.value === 'bouts') matches.sort((a,b)=>b.bouts-a.bouts || a.name.localeCompare(b.name));
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
  sort?.addEventListener('change', () => { page = 1; renderDirectory(); });
  previous.addEventListener('click', () => { page--; renderDirectory(); grid.scrollIntoView({block:'start'}); });
  next.addEventListener('click', () => { page++; renderDirectory(); grid.scrollIntoView({block:'start'}); });
  renderDirectory();
}

document.querySelectorAll('[data-filter-list]').forEach(input => input.dataset.filterList.split(/\s+/).forEach(id => {
  const list = document.getElementById(id);
  const items = [...list.querySelectorAll('[data-search-item]')];
  const searchable = items.map(item=>({item,text:normalizeRecordSearch(item.textContent+' '+(item.dataset.searchText || '')+' '+[...item.querySelectorAll('[data-search-text],time')].map(e=>e.dataset.searchText || e.dateTime).join(' '))}));
  const empty = document.querySelector(`[data-empty-for="${list.id}"]`);
  const controls = document.createElement('div'); controls.className = 'directory-pagination';
  const previous = document.createElement('button'); previous.type = 'button'; previous.textContent = '← Previous';
  const label = document.createElement('span'); label.setAttribute('aria-live','polite');
  const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Next →';
  controls.append(previous,label,next); list.after(controls);
  let page = 1, state = 'all';
  function filter() {
    const tokens = recordSearchTokens(input.value);
    const matches = searchable.filter(({item,text}) => (state === 'all' || item.dataset.recordState === state) && tokens.every(token=>text.includes(token))).map(entry=>entry.item);
    const pages = Math.max(1,Math.ceil(matches.length / 24)); page = Math.min(page,pages);
    items.forEach(item => { item.hidden = true; });
    matches.slice((page - 1) * 24,page * 24).forEach(item => { item.hidden = false; });
    if (empty) empty.hidden = matches.length > 0;
    controls.hidden = !matches.length || pages === 1; previous.disabled = page <= 1; next.disabled = page >= pages;
    label.textContent = `Page ${page} of ${pages}`;
    const count = document.querySelector(`[data-count-for="${list.id}"]`);
    if (count) count.textContent = `${matches.length.toLocaleString()} ${list.id === 'forecast-directory' ? 'forecasts' : 'events'}`;
  }
  input.addEventListener('input', () => { page = 1; filter(); });
  previous.addEventListener('click', () => { page--; filter(); list.scrollIntoView({block:'start'}); });
  next.addEventListener('click', () => { page++; filter(); list.scrollIntoView({block:'start'}); });
  document.querySelectorAll(`[data-record-filter][data-target="${list.id}"]`).forEach(button=>button.addEventListener('click',()=>{
    state = button.dataset.recordFilter; page = 1;
    document.querySelectorAll(`[data-record-filter][data-target="${list.id}"]`).forEach(item=>{ item.classList.toggle('active',item===button); item.setAttribute('aria-pressed',String(item===button)); });
    filter();
  }));
  filter();
}));

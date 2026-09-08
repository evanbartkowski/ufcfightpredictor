'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = v => v == null ? '—' : `${(v*100).toFixed(1)}%`;
const day = v => v ? new Date(v.length === 10 ? `${v}T12:00:00` : v).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}) : 'Not yet';
let data = null;
let activeMatchup = null;
function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.hidden = p.id !== name);
  document.querySelectorAll('.tab').forEach(b => { b.classList.toggle('active',b.dataset.tab === name); b.setAttribute('aria-pressed',String(b.dataset.tab === name)); });
}
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click',() => showTab(b.dataset.tab)));
$('event-filter').addEventListener('change', () => render());
function empty(title, body, lab=false) {
  return `<div class="empty"><div class="empty-icon">↗</div><h3>${esc(title)}</h3><p>${esc(body)}</p>${lab?'<button class="text-button" data-open-lab>Explore the matchup lab →</button>':''}</div>`;
}
const stamp = v => v ? new Date(v).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not yet';
const line = v => v == null ? '\u2014' : v > 0 ? `+${v}` : String(v);
function oddsPanel(f) {
  const o = f.odds;
  if(!o) return '';
  const books = o.books || [];
  return `<section class="odds-panel" aria-label="Sportsbook odds"><div class="odds-heading"><span>SPORTSBOOK MONEYLINE</span><span class="odds-state ${o.stale?'is-stale':''}">${o.stale?'STALE / RETRYING':o.available?'LATEST FETCH':'AWAITING ODDS'}</span></div>${books.map(b=>`<div class="odds-book"><span>${esc(b.name)}</span></div><div class="odds-prices"><strong title="${esc(f.red)}">${line(b.red)}</strong><strong title="${esc(f.blue)}">${line(b.blue)}</strong></div><div class="odds-fighters"><span>${esc(f.red)}</span><span>${esc(f.blue)}</span></div>`).join('')}${!books.length?`<p class="muted">${esc(o.message || 'Odds are temporarily unavailable.')}</p>`:''}<div class="odds-meta">${o.fetched_at?'Checked '+esc(stamp(o.fetched_at))+' \u00b7 ':''}${o.source?'Via ESPN \u00b7 ':''}Auto-refresh ${Math.round((data?.refresh?.odds_seconds||900)/60)} min${o.error?' \u00b7 Refresh failed':''}</div>${o.available?'<div class="odds-meta">Source does not supply a line-change timestamp. Prices may move between checks.</div>':''}</section>`;
}
function countdownText(value, state, currentTime=Date.now()) {
  if(state === 'post') return 'Fight completed';
  if(state === 'in') return 'Event underway';
  const target = Date.parse(value);
  if(!Number.isFinite(target)) return 'Start time to be announced';
  const remaining = Math.ceil((target-currentTime)/1000);
  if(remaining <= 0) return 'Scheduled start reached';
  const days = Math.floor(remaining/86400);
  const hours = Math.floor(remaining%86400/3600);
  const minutes = Math.floor(remaining%3600/60);
  const seconds = remaining%60;
  const pad = n => String(n).padStart(2,'0');
  return `${days?days+'d ':''}${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
}
function countdown(f) {
  if(!f.date) return '';
  const start = f.odds?.start_at || f.start_at || '';
  const valid = Number.isFinite(Date.parse(start));
  const localTime = valid ? new Date(start).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}) : '';
  return `<div class="fight-countdown"><span class="countdown-label">CARD START COUNTDOWN</span><strong role="timer" aria-label="Time until scheduled event start" data-countdown="${esc(start)}" data-state="${esc(f.odds?.state || '')}">${esc(countdownText(start,f.odds?.state))}</strong>${valid?`<time datetime="${esc(start)}">${esc(localTime)}</time>`:''}<small>Countdown is to the event start; individual bout times vary.</small></div>`;
}
function updateCountdowns() {
  document.querySelectorAll('[data-countdown]').forEach(timer=>{
    timer.textContent=countdownText(timer.dataset.countdown,timer.dataset.state);
  });
}
function modelEdge(f, currentTime=Date.now()) {
  const o=f.odds;
  if(f.prediction_pending || f.low_history || f.locked || !o?.available || o.stale || o.error || o.state !== 'pre') return null;
  const start=Date.parse(o.start_at || f.start_at);
  const fetched=Date.parse(o.fetched_at);
  if(!Number.isFinite(start) || start<=currentTime || !Number.isFinite(fetched) || currentTime-fetched>Math.max((data?.refresh?.odds_seconds||900)*2,180)*1000) return null;
  const side=f.pick===f.red?'red':f.pick===f.blue?'blue':null;
  const probability=side?f[side+'_probability']:null;
  if(!Number.isFinite(probability) || probability<.70 || probability>1) return null;
  const options=(o.books||[]).flatMap(book=>{
    const price=book[side];
    if(!Number.isInteger(price) || Math.abs(price)<100) return [];
    const implied=price>0?100/(price+100):(-price)/(-price+100);
    return probability-implied>=.05-1e-9?[{name:book.name,price,probability,implied,edge:probability-implied}]:[];
  });
  return options.sort((a,b)=>b.edge-a.edge)[0] || null;
}
function isWomensFight(f) {
  return /\bwomen(?:['\u2019]s|s)?\b|\bfemale\b/i.test(f.weight_class || '');
}
function card(f, withProfiles=false) {
  const pending = f.prediction_pending;
  const edge = modelEdge(f);
  return `<article class="fight-card${isWomensFight(f)?' womens-fight':''}${edge?' has-model-edge':''}" data-fight-id="${esc(f.id || 'custom')}">
    <div class="fight-top"><span>${esc(f.weight_class || 'CUSTOM MATCHUP')}</span><span>${f.locked?'FORECAST LOCKED':'WIN PROBABILITY'}</span></div>
    ${edge?`<div class="model-edge"><strong>&#9733; MODEL EDGE</strong><span>${esc(f.pick)} &middot; ${pct(edge.probability)} model chance &middot; ${line(edge.price)} at ${esc(edge.name)}</span><small>+${(edge.edge*100).toFixed(1)} points vs. ${pct(edge.implied)} break-even probability. Model estimate, not a guaranteed return.</small></div>`:''}
    ${countdown(f)}
    <div class="fighters-row"><div><div class="corner">FIRST FIGHTER</div><div class="fighter-name">${esc(f.red)}</div></div><span class="versus">VS</span><div><div class="corner blue">SECOND FIGHTER</div><div class="fighter-name">${esc(f.blue)}</div></div></div>
    ${pending?'<p class="muted">No eligible pre-event forecast yet. Current matchup and available odds are shown below.</p>':`<div class="probabilities"><span>${pct(f.red_probability)}</span><span>${pct(f.blue_probability)}</span></div><div class="probability-bar" role="img" aria-label="${esc(f.red)} ${pct(f.red_probability)}, ${esc(f.blue)} ${pct(f.blue_probability)}"><span style="width:${Math.max(0,Math.min(100,f.red_probability*100))}%"></span></div><div class="pick"><div><span class="pick-label">PREDICTED WINNER</span><strong>${esc(f.pick)}</strong></div><span>\u2197 ${pct(f.confidence)}</span></div>`}
    ${oddsPanel(f)}
    ${f.low_history?'<div class="low-history">Limited history: at least one fighter has fewer than 3 recorded bouts.</div>':''}
    ${!pending?`<div class="forecast-meta">${f.issued_at?'Prediction updated '+esc(stamp(f.issued_at)):'Latest model prediction'}</div>`:''}
    ${withProfiles?`<details><summary>Compare current fighter stats</summary><div class="comparison">${profile(f.red)}${profile(f.blue)}</div></details>`:''}</article>`;
}
function profile(name) {
  const key = v => v.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const f = data.fighters.find(f => key(f.name) === key(name));
  if (!f) return '';
  const fields = [['Record',f.record || `${parseInt(f.wins)||0}–${parseInt(f.losses)||0}`],['Stance',f.stance],['Height',f.height],['Reach',f.reach],['Strikes landed / min',f.SLpM],['Strikes absorbed / min',f.SApM],['Striking accuracy',f.sig_str_acc],['Takedowns / 15 min',f.td_avg],['Takedown defense',f.td_def],['Submissions / 15 min',f.sub_avg]];
  return `<div class="profile"><h3>${esc(name)}</h3><span class="tag">${!f.fetched_at?'ARCHIVED PROFILE':Date.now()-Date.parse(f.fetched_at)>(data.refresh?.stats_seconds||3600)*2000?'CACHED \u00b7 REFRESH PENDING':'UFCSTATS PROFILE'}</span>${fields.map(([k,v])=>`<div class="stat-row"><span>${esc(k)}</span><strong>${esc(v || '—')}${!f.fetched_at && ['Height','Reach'].includes(k)?' cm':''}</strong></div>`).join('')}<span class="muted">${esc(f.source)}${f.fetched_at?' · Fetched '+esc(stamp(f.fetched_at)):''}</span></div>`;
}
function cardSections(fights) {
  const sections=[['main','Main card'],['prelims','Undercard / Prelims'],['early','Early prelims'],['unassigned','Card placement pending']];
  return sections.map(([key,label])=>{
    const group=fights.filter(f=>(f.odds?.card_section || 'unassigned')===key);
    if(!group.length) return '';
    return `<section class="card-section" aria-label="${label}"><div class="card-section-heading"><h4>${label}</h4><span>${group.length} ${group.length===1?'bout':'bouts'}</span></div>${key==='unassigned'?'<p class="section-note">The published schedule does not yet confirm these card placements.</p>':'<p class="section-note">Grouped by scheduled broadcast start times. Card placement may change.</p>'}<div class="fight-grid">${group.map(f=>card(f,true)).join('')}</div></section>`;
  }).join('');
}
function render() {
  const m = data.model;
  if(m) $('model-summary').textContent = `${m.name}, trained on ${m.metrics.training_fights.toLocaleString()} confirmed UFC win/loss results through ${day(m.through)}. It compares pre-fight records, opponent strength, recent form, and finish rates, then retrains when results change.`;
  $('accuracy').textContent = pct(m?.metrics.accuracy);
  $('test-count').textContent = m ? `${m.metrics.test_fights.toLocaleString()} fights · ${day(m.metrics.test_start)}–${day(m.metrics.test_end)}` : 'Model pending';
  $('fight-count').textContent = data.total_fights.toLocaleString();
  $('live-accuracy').textContent = pct(data.live_accuracy);
  $('live-count').textContent = data.scored_predictions ? `${data.scored_predictions} settled forecasts` : 'Awaiting settled forecasts';
  $('last-sync').textContent = data.status.last_sync ? new Date(data.status.last_sync).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not yet';
  $('sync-state').textContent = data.status.running ? data.status.phase : `Cards & stats every ${Math.round(data.refresh.stats_seconds/60)} min`;
  const stale = !data.status.last_sync || Date.now()-Date.parse(data.status.last_sync)>(data.refresh.stats_seconds*2000);
  const lastFailure=data.status.last_error;
  const error = data.status.error || (lastFailure && (!data.status.last_sync || Date.parse(lastFailure.at)>Date.parse(data.status.last_sync)) ? lastFailure.message : null);
  const profileWarning = data.profile_errors?.length ? ` ${data.profile_errors.length} fighter profiles could not be refreshed and are labeled as cached.` : '';
  $('odds-sync').textContent = data.odds_status.error ? 'Odds delayed / retrying automatically' : data.odds_status.running ? 'Refreshing odds...' : data.odds_status.checked_at ? `Odds checked ${stamp(data.odds_status.checked_at)}` : 'Waiting for sportsbook odds';
  $('odds-sync').classList.toggle('warning',Boolean(data.odds_status.error));
  $('notice').classList.toggle('warning',Boolean(!data.status.running && (error || stale)));
  $('notice').textContent = data.status.running ? 'Refreshing fight data in the background. Saved data remains available.' : error ? 'Fight data is delayed. Showing saved data; retrying automatically.' : stale ? 'Showing saved data. A fresh update is pending.' : `Fight data up to date / ${stamp(data.status.last_sync)}`;
  $('notice').textContent += profileWarning;
  if(data.refresh.automatic===false){$('notice').classList.add('warning');$('notice').textContent += ' Automatic server updates are disabled.';}
  $('card-count').textContent = data.upcoming.length;
  const expanded = new Set([...document.querySelectorAll('#cards .fight-card')].flatMap(c=>[...c.querySelectorAll('details')].map((d,i)=>d.open?c.dataset.fightId+'|'+i:null).filter(Boolean)));
  const groups = data.upcoming.reduce((groups,f)=>{const key=`${f.date}|${f.event}`;(groups[key] ||= []).push(f);return groups;},Object.create(null));
  let selection = $('event-filter').value;
  if(selection !== 'all' && !groups[selection]) selection=Object.keys(groups)[0] || 'all';
  $('event-filter').innerHTML = '<option value="all">All upcoming events</option>'+Object.entries(groups).map(([key,fs])=>`<option value="${esc(key)}">${esc(fs[0].event)}</option>`).join('');
  $('event-filter').value=selection;
  const visible = selection==='all' ? Object.values(groups) : [groups[selection]];
  $('cards').innerHTML = data.upcoming.length ? visible.map(fs=>`<div class="event-heading"><h3>${esc(fs[0].event)}</h3><span class="tag">${esc(day(fs[0].date))}</span></div>${cardSections(fs)}`).join('') : empty('The next card is on its way','Upcoming predictions appear after a successful UFCStats refresh. In the meantime, compare any two fighters using the historical model.',true);
  document.querySelectorAll('#cards .fight-card').forEach(c=>c.querySelectorAll('details').forEach((d,i)=>{d.open=expanded.has(c.dataset.fightId+'|'+i);}));
  $('fighter-list').innerHTML = data.fighters.map(f=>`<option value="${esc(f.name)}"></option>`).join('');
  $('results-list').innerHTML = data.results.length ? data.results.map(r=>`<article class="result"><div><h3>${esc(r.red)} vs. ${esc(r.blue)}</h3><p>${esc(day(r.date))} · Predicted ${esc(r.pick)} (${pct(r.confidence)}) · ${r.actual_winner?'Winner: '+esc(r.actual_winner):'Draw / no contest — unscored'}</p></div><span class="badge ${r.correct===false?'miss':''}">${r.correct===null?'UNSCORED':r.correct?'CORRECT':'MISSED'}</span></article>`).join('') : empty('A record earned in real fights','Once a saved forecast settles, the original pick and the actual result appear here. Historical test scores are tracked separately.');
  if(m) $('model-info').innerHTML = `<div class="model-grid"><div class="info-box"><h3>${esc(m.name)}</h3><div class="stat-row"><span>Results through</span><strong>${esc(day(m.through))}</strong></div><div class="stat-row"><span>Holdout accuracy</span><strong>${pct(m.metrics.accuracy)}</strong></div><div class="stat-row"><span>Elo baseline accuracy</span><strong>${pct(m.metrics.elo_accuracy)}</strong></div><div class="stat-row"><span>Majority baseline accuracy</span><strong>${pct(m.metrics.majority_accuracy)}</strong></div><div class="stat-row"><span>Brier score · lower is better</span><strong>${m.metrics.brier.toFixed(3)}</strong></div><div class="stat-row"><span>Log loss · lower is better</span><strong>${m.metrics.log_loss.toFixed(3)}</strong></div><p>Model choice uses the middle 15% of event dates. The final 20% is reserved for evaluation. The serving model is then fitted on all confirmed results.</p></div><div class="info-box"><h3>A continuous learning loop</h3><ol><li>Fetch announced cards and refresh fighter profiles.</li><li>Refresh the forecast until event start; keep its earlier versions.</li><li>Wait for an official win, loss, draw, or no contest.</li><li>Score the original forecast and rebuild pre-fight features.</li><li>Retrain on confirmed outcomes for future matchups.</li></ol><p>Features for a fight only include earlier dates. Draws and no contests are excluded from binary training. Swapping fighter order preserves the forecast.</p><p>The bundled record begins in 2010 and ends in 2024. Missing early careers and new UFC entrants reduce confidence. Current striking and grappling profiles are context, not historical model inputs.</p></div></div>`;
  document.querySelectorAll('[data-open-lab]').forEach(b=>b.addEventListener('click',()=>showTab('matchup')));
}
let loading=false;
async function load() {
  if(loading) return;
  loading=true;
  try { const r=await fetch('/api/dashboard',{cache:'no-store',signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error('Dashboard is unavailable');data=await r.json();render(); if(activeMatchup && !$('matchup').hidden) await refreshMatchup(); }
  catch(e){$('notice').classList.add('warning');$('notice').textContent='Connection interrupted. Showing any saved data; retrying automatically.';}
  finally{loading=false;}
}
async function refreshMatchup() {
  const pair=activeMatchup;
  if(!pair) return;
  const r=await fetch('/api/matchup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pair),signal:AbortSignal.timeout(20000)});
  const f=await r.json();
  if(!r.ok)throw Error(f.error||'Prediction unavailable');
  if(activeMatchup!==pair) return;
  $('matchup-output').innerHTML=card(f)+`<div class="comparison">${profile(f.red)}${profile(f.blue)}</div>`;
}
$('matchup-form').addEventListener('submit',async e=>{
  e.preventDefault();const b=e.submitter;b.disabled=true;
  $('matchup-output').textContent='Calculating prediction\u2026';
  activeMatchup={red:$('red').value,blue:$('blue').value};
  try {await refreshMatchup();}
  catch(e){activeMatchup=null;$('matchup-output').textContent=e.message;}
  finally{b.disabled=false;}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
window.addEventListener('online',load);
load();setInterval(load,60000);setInterval(updateCountdowns,1000);

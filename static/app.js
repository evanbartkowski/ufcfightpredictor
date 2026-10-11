'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = v => v == null ? '—' : `${(v*100).toFixed(1)}%`;
const day = v => v ? new Date(v.length === 10 ? `${v}T12:00:00` : v).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}) : 'Not yet';
const stamp = v => v ? new Date(v).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not yet';
const line = v => v == null ? '\u2014' : v > 0 ? `+${v}` : String(v);

let data = null;
let activeMatchup = null;
let currentCardFilter = 'all'; // 'all' | 'upset' | 'favorites'
let analyticsScope = 'all'; // 'all' | '50' | '25' | '10'
let fighterSearchQuery = '';
let fighterDivisionFilter = 'all';
let shareTargetFight = null;

// ==========================================
// Navigation & Tab Switching
// ==========================================
function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.hidden = p.id !== name);
  document.querySelectorAll('.tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
    b.setAttribute('aria-pressed', String(b.dataset.tab === name));
  });
  if (name === 'analytics') {
    renderAnalytics();
  } else if (name === 'fighters') {
    renderFighterDatabase();
  }
}
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
$('event-filter')?.addEventListener('change', () => render());

// Card Filter Pills (All Bouts, Upset Radar, Favorites)
document.querySelectorAll('.filter-pill').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCardFilter = btn.dataset.filter || 'all';
    render();
  });
});

// Analytics Scope Pills
document.querySelectorAll('.pill-scope').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.pill-scope').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    analyticsScope = btn.dataset.scope || 'all';
    renderAccuracyChart();
  });
});

function empty(title, body, lab=false) {
  return `<div class="empty"><div class="empty-icon">↗</div><h3>${esc(title)}</h3><p>${esc(body)}</p>${lab?'<button class="text-button" data-open-lab>Explore the matchup lab →</button>':''}</div>`;
}

// ==========================================
// Favorites Management
// ==========================================
function getFavorites() {
  try {
    const raw = localStorage.getItem('mma_cipher_favorites');
    return new Set(raw ? JSON.parse(raw) : []);
  } catch (_) {
    return new Set();
  }
}

function toggleFavorite(name) {
  const favs = getFavorites();
  if (favs.has(name)) {
    favs.delete(name);
  } else {
    favs.add(name);
  }
  try {
    localStorage.setItem('mma_cipher_favorites', JSON.stringify([...favs]));
  } catch (_) {}
  updateFavCounts();
  if (currentCardFilter === 'favorites') {
    render();
  } else {
    document.querySelectorAll(`[data-fav-name="${esc(name)}"]`).forEach(btn => {
      btn.classList.toggle('is-fav', favs.has(name));
    });
  }
  if (!$('fighters').hidden) {
    renderFighterDatabase();
  }
}

function updateFavCounts() {
  const favs = getFavorites();
  let countInUpcoming = 0;
  if (data?.upcoming) {
    countInUpcoming = data.upcoming.filter(f => favs.has(f.red) || favs.has(f.blue)).length;
  }
  const favPill = $('fav-pill-count');
  if (favPill) favPill.textContent = countInUpcoming;
}

// ==========================================
// Community Predictions & Consensus
// ==========================================
const COMMUNITY_VOTES_KEY = 'mma_cipher_community_votes';
const USER_PICKS_KEY = 'mma_cipher_user_picks';

function getUserPicks() {
  try {
    const raw = localStorage.getItem(USER_PICKS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

function getCommunityVotes() {
  try {
    const raw = localStorage.getItem(COMMUNITY_VOTES_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) {
    return {};
  }
}

function saveCommunityVotes(votes) {
  try {
    localStorage.setItem(COMMUNITY_VOTES_KEY, JSON.stringify(votes));
  } catch (_) {}
}

function getCommunityConsensus(f) {
  const fightId = f.id || `${f.red}_${f.blue}`;
  const votes = getCommunityVotes();
  // Starts strictly at 50/50% baseline
  const current = votes[fightId] || { red: 50, blue: 50 };
  const rVotes = Math.max(0, current.red ?? 50);
  const bVotes = Math.max(0, current.blue ?? 50);
  const total = rVotes + bVotes;
  const redPct = total > 0 ? (rVotes / total) : 0.50;
  const bluePct = 1.0 - redPct;

  return {
    red: redPct,
    blue: bluePct,
    redVotes: rVotes,
    blueVotes: bVotes,
    totalVotes: total
  };
}

function setUserPick(fightId, side) {
  const picks = getUserPicks();
  const votes = getCommunityVotes();
  if (!votes[fightId]) {
    votes[fightId] = { red: 50, blue: 50 };
  }

  const prevPick = picks[fightId];
  if (prevPick === side) {
    // Deselect pick
    delete picks[fightId];
    if (side === 'red') votes[fightId].red = Math.max(50, votes[fightId].red - 1);
    if (side === 'blue') votes[fightId].blue = Math.max(50, votes[fightId].blue - 1);
  } else {
    // Switch or apply new pick
    if (prevPick === 'red') votes[fightId].red = Math.max(50, votes[fightId].red - 1);
    if (prevPick === 'blue') votes[fightId].blue = Math.max(50, votes[fightId].blue - 1);
    picks[fightId] = side;
    if (side === 'red') votes[fightId].red += 1;
    if (side === 'blue') votes[fightId].blue += 1;
  }

  try {
    localStorage.setItem(USER_PICKS_KEY, JSON.stringify(picks));
    saveCommunityVotes(votes);
  } catch (_) {}

  renderCardPickButtons(fightId);
}

// ==========================================
// Odds & Card Countdown
// ==========================================
function oddsPanel(f) {
  const o = f.odds;
  if (!o) return '';
  const books = o.books || [];
  return `<section class="odds-panel" aria-label="Sportsbook odds"><div class="odds-heading"><span>SPORTSBOOK MONEYLINE</span><span class="odds-state ${o.stale?'is-stale':''}">${o.stale?'STALE / RETRYING':o.available?'LATEST FETCH':'AWAITING ODDS'}</span></div>${books.map(b=>`<div class="odds-book"><span>${esc(b.name)}</span></div><div class="odds-prices"><strong title="${esc(f.red)}">${line(b.red)}</strong><strong title="${esc(f.blue)}">${line(b.blue)}</strong></div><div class="odds-fighters"><span>${esc(f.red)}</span><span>${esc(f.blue)}</span></div>`).join('')}${!books.length?`<p class="muted">${esc(o.message || 'Odds are temporarily unavailable.')}</p>`:''}<div class="odds-meta">${o.fetched_at?'Checked '+esc(stamp(o.fetched_at))+' \u00b7 ':''}${o.source?'Via ESPN \u00b7 ':''}Auto-refresh ${Math.round((data?.refresh?.odds_seconds||900)/60)} min${o.error?' \u00b7 Refresh failed':''}</div>${o.available?'<div class="odds-meta">Source does not supply a line-change timestamp. Prices may move between checks.</div>':''}</section>`;
}

function countdownText(value, state, currentTime=Date.now()) {
  if (state === 'post') return 'Fight completed';
  if (state === 'in') return 'Event underway';
  const target = Date.parse(value);
  if (!Number.isFinite(target)) return 'Start time to be announced';
  const remaining = Math.ceil((target - currentTime) / 1000);
  if (remaining <= 0) return 'Scheduled start reached';
  const days = Math.floor(remaining / 86400);
  const hours = Math.floor(remaining % 86400 / 3600);
  const minutes = Math.floor(remaining % 3600 / 60);
  const seconds = remaining % 60;
  const pad = n => String(n).padStart(2, '0');
  return `${days?days+'d ':''}${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
}

function countdown(f) {
  if (!f.date) return '';
  const start = f.odds?.start_at || f.start_at || '';
  const valid = Number.isFinite(Date.parse(start));
  const localTime = valid ? new Date(start).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}) : '';
  return `<div class="fight-countdown"><span class="countdown-label">CARD START COUNTDOWN</span><strong role="timer" aria-label="Time until scheduled event start" data-countdown="${esc(start)}" data-state="${esc(f.odds?.state || '')}">${esc(countdownText(start,f.odds?.state))}</strong>${valid?`<time datetime="${esc(start)}">${esc(localTime)}</time>`:''}<small>Countdown is to the event start; individual bout times vary.</small></div>`;
}

function updateCountdowns() {
  document.querySelectorAll('[data-countdown]').forEach(timer => {
    timer.textContent = countdownText(timer.dataset.countdown, timer.dataset.state);
  });
}

function modelEdge(f, currentTime=Date.now()) {
  const o = f.odds;
  if (f.prediction_pending || f.low_history || f.locked || !o?.available || o.stale || o.error || o.state !== 'pre') return null;
  const start = Date.parse(o.start_at || f.start_at);
  const fetched = Date.parse(o.fetched_at);
  if (!Number.isFinite(start) || start <= currentTime || !Number.isFinite(fetched) || currentTime - fetched > Math.max((data?.refresh?.odds_seconds||900)*2, 180)*1000) return null;
  const side = f.pick === f.red ? 'red' : f.pick === f.blue ? 'blue' : null;
  const probability = side ? f[side+'_probability'] : null;
  if (!Number.isFinite(probability) || probability < .70 || probability > 1) return null;
  const options = (o.books || []).flatMap(book => {
    const price = book[side];
    if (!Number.isInteger(price) || Math.abs(price) < 100) return [];
    const implied = price > 0 ? 100 / (price + 100) : (-price) / (-price + 100);
    return probability - implied >= .05 - 1e-9 ? [{name:book.name, price, probability, implied, edge:probability-implied}] : [];
  });
  return options.sort((a,b) => b.edge - a.edge)[0] || null;
}

function isWomensFight(f) {
  return /\bwomen(?:['\u2019]s|s)?\b|\bfemale\b/i.test(f.weight_class || '');
}

// ==========================================
// Prediction Card Components
// ==========================================
function renderMethod(f) {
  if (!f.likely_method || !f.method_probabilities) return '';
  const mp = f.method_probabilities;
  return `
    <div class="method-strip">
      <div class="method-heading">
        <span>PROJECTED METHOD OF VICTORY</span>
        <strong class="method-tag">${esc(f.likely_method)}</strong>
      </div>
      <div class="method-bars">
        <div class="method-item">
          <span class="method-name">DECISION</span>
          <div class="method-bar-bg"><div class="method-bar-fill fill-dec" style="width:${Math.round((mp.decision||0.5)*100)}%"></div></div>
          <strong>${pct(mp.decision)}</strong>
        </div>
        <div class="method-item">
          <span class="method-name">KO / TKO</span>
          <div class="method-bar-bg"><div class="method-bar-fill fill-ko" style="width:${Math.round((mp.ko||0.32)*100)}%"></div></div>
          <strong>${pct(mp.ko)}</strong>
        </div>
        <div class="method-item">
          <span class="method-name">SUBMISSION</span>
          <div class="method-bar-bg"><div class="method-bar-fill fill-sub" style="width:${Math.round((mp.submission||0.18)*100)}%"></div></div>
          <strong>${pct(mp.submission)}</strong>
        </div>
      </div>
    </div>
  `;
}

function renderTape(f) {
  const tape = f.tale_of_the_tape;
  if (!tape) return '';
  const diffSign = (v) => v > 0 ? `+${v.toFixed(1)}` : v < 0 ? `${v.toFixed(1)}` : 'Even';
  return `
    <div class="tape-box">
      <div class="tape-heading"><span>PRE-FIGHT TALE OF THE TAPE</span><span class="cipher-pill">PHYSICAL DATA</span></div>
      <div class="tape-grid">
        <div class="tape-row">
          <span class="tape-val">${tape.age.red ? Math.round(tape.age.red) : '—'}</span>
          <span class="tape-label">AGE (${diffSign(tape.age.diff || 0)})</span>
          <span class="tape-val right">${tape.age.blue ? Math.round(tape.age.blue) : '—'}</span>
        </div>
        <div class="tape-row">
          <span class="tape-val">${tape.height.red ? Math.round(tape.height.red) + ' cm' : '—'}</span>
          <span class="tape-label">HEIGHT (${diffSign(tape.height.diff || 0)} cm)</span>
          <span class="tape-val right">${tape.height.blue ? Math.round(tape.height.blue) + ' cm' : '—'}</span>
        </div>
        <div class="tape-row">
          <span class="tape-val">${tape.reach.red ? Math.round(tape.reach.red) + ' cm' : '—'}</span>
          <span class="tape-label">REACH (${diffSign(tape.reach.diff || 0)} cm)</span>
          <span class="tape-val right">${tape.reach.blue ? Math.round(tape.reach.blue) + ' cm' : '—'}</span>
        </div>
        <div class="tape-row">
          <span class="tape-val">${esc(tape.stance.red || 'Orthodox')}</span>
          <span class="tape-label">STANCE</span>
          <span class="tape-val right">${esc(tape.stance.blue || 'Orthodox')}</span>
        </div>
      </div>
    </div>
  `;
}

function renderEnsemble(f) {
  if (!f.models || !Object.keys(f.models).length) return '';
  const redLast = esc(f.red.split(' ').pop());
  const blueLast = esc(f.blue.split(' ').pop());
  const consensus = f.consensus || {};

  return `
    <details class="ensemble-details">
      <summary>
        <span><span class="cipher-badge">MMA CIPHER ENSEMBLE</span> Algorithmic Consensus (${Object.keys(f.models).length} Models)</span>
        <span class="uncertainty-pill ${(consensus.uncertainty||'').toLowerCase()}">${esc(consensus.tier || 'Consensus')}</span>
      </summary>
      <div class="ensemble-breakdown">
        ${Object.entries(f.models).map(([name, m]) => `
          <div class="ensemble-row">
            <div class="ensemble-col-name">
              <strong>${esc(name.replace('MMA Cipher ', ''))}</strong>
              <small>Weight: ${(m.weight * 100).toFixed(0)}%</small>
            </div>
            <div class="ensemble-col-probs">
              <span class="red-bar-prob">${redLast}: ${pct(m.red_probability)}</span>
              <span class="blue-bar-prob">${blueLast}: ${pct(m.blue_probability)}</span>
            </div>
          </div>
        `).join('')}
      </div>
    </details>
  `;
}

function renderExplanation(f) {
  const exp = f.explanation;
  if (!exp) return '';
  const advs = exp.advantages || [];
  const risks = exp.risks || [];
  const consensus = f.consensus || {};

  return `
    <details class="prediction-explanation">
      <summary>
        <span>⚡ WHY THE AI CHOSE THIS FIGHTER</span>
        <span class="cipher-pill">${esc(exp.pick)} ADVANTAGE</span>
      </summary>
      <div class="explanation-body">
        <p class="explanation-summary-text">${esc(exp.summary || '')}</p>
        ${advs.length ? `
          <div class="factors-group">
            <div class="factors-heading">PRIMARY STATISTICAL ADVANTAGES</div>
            ${advs.map(a => `
              <div class="factor-item">
                <span>${esc(a.title)}</span>
                <span class="badge-impact ${a.impact==='high'?'high':''}">${esc(a.impact.toUpperCase())}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
        ${risks.length ? `
          <div class="factors-group">
            <div class="factors-heading risk">KEY MATCHUP RISKS / VULNERABILITIES</div>
            ${risks.map(r => `
              <div class="factor-item">
                <span>${esc(r.title)}</span>
                <span class="badge-impact risk">${esc(r.impact.toUpperCase())}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
        <div class="consensus-hud">
          <div class="consensus-status">
            <span>MODEL CONSENSUS:</span>
            <strong>${esc(consensus.tier || 'Ensemble Consensus')}</strong>
          </div>
          <span class="uncertainty-pill ${(consensus.uncertainty||'').toLowerCase()}">
            UNCERTAINTY: ${esc(consensus.uncertainty || 'LOW')}
          </span>
        </div>
      </div>
    </details>
  `;
}

function renderUpsetBanner(f) {
  if (!f.upset_radar?.is_candidate && !modelEdge(f)) return '';
  const signals = f.upset_radar?.signals || [];
  const edge = modelEdge(f);
  return `
    <div class="upset-radar-banner">
      <div class="upset-radar-tag">
        <span class="radar-pulse-dot"></span>
        <span>UPSET RADAR</span>
      </div>
      <small>${esc(signals[0] || (edge ? `+${(edge.edge*100).toFixed(0)}% edge over market odds` : 'Underdog projected to contend'))}</small>
    </div>
  `;
}

function renderUserPickButtons(f) {
  if (f.prediction_pending) return '';
  const userPick = getUserPicks()[f.id];
  const comm = getCommunityConsensus(f);
  const isLocked = f.locked || false;
  const redLast = esc(f.red.split(' ').pop());
  const blueLast = esc(f.blue.split(' ').pop());
  const redPct = Math.round(comm.red * 100);
  const bluePct = Math.round(comm.blue * 100);

  return `
    <div class="user-pick-container" id="pick-box-${esc(f.id)}">
      <div class="user-pick-header">
        <span class="user-pick-tag"><span class="pulse-dot"></span> COMMUNITY CONSENSUS</span>
        <span class="user-pick-action">${userPick ? (userPick === 'red' ? 'PICKED ' + redLast.toUpperCase() : 'PICKED ' + blueLast.toUpperCase()) : 'SELECT WINNER'}</span>
      </div>
      <div class="user-pick-btns">
        <button class="pick-btn ${userPick==='red'?'selected-red':''}" data-pick-fight="${esc(f.id)}" data-pick-side="red" ${isLocked?'disabled':''} aria-label="Vote for ${esc(f.red)}">
          <span class="pick-btn-name">${esc(f.red)}</span>
          <span class="pick-btn-pct">${redPct}%</span>
          <span class="pick-status-icon">${userPick==='red'?'✓':'+'}</span>
        </button>
        <button class="pick-btn ${userPick==='blue'?'selected-blue':''}" data-pick-fight="${esc(f.id)}" data-pick-side="blue" ${isLocked?'disabled':''} aria-label="Vote for ${esc(f.blue)}">
          <span class="pick-btn-name">${esc(f.blue)}</span>
          <span class="pick-btn-pct">${bluePct}%</span>
          <span class="pick-status-icon">${userPick==='blue'?'✓':'+'}</span>
        </button>
      </div>
      <div class="community-vs-ai">
        <div class="cvsa-labels">
          <span>AI Forecast: <strong>${esc(f.pick)}</strong> (${pct(f.confidence)})</span>
          <span>Community: <strong>${redPct}%</strong> ${redLast} &middot; <strong>${bluePct}%</strong> ${blueLast}</span>
        </div>
        <div class="cvsa-bar" role="progressbar" aria-label="Community split: ${redPct}% ${redLast} vs ${bluePct}% ${blueLast}">
          <div class="cvsa-fill" style="width: ${(comm.red * 100).toFixed(1)}%"></div>
        </div>
      </div>
    </div>
  `;
}

function renderCardPickButtons(fightId) {
  const box = $(`pick-box-${fightId}`);
  if (!box || !data?.upcoming) return;
  const f = data.upcoming.find(x => x.id === fightId);
  if (f) box.outerHTML = renderUserPickButtons(f);
  attachCardListeners();
}

let fighterImagesMap = {};

async function initFighterImages() {
  if (data?.images && Object.keys(data.images).length > 0) {
    fighterImagesMap = data.images;
    return;
  }
  try {
    const res = await fetch('/static/fighter_images.json');
    if (res.ok) {
      fighterImagesMap = await res.json();
    }
  } catch (e) {
    // Graceful fallback
  }
}

function getFighterImage(name) {
  if (!name) return null;
  const k = name.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  if (data?.images && data.images[k]) return data.images[k];
  if (fighterImagesMap && fighterImagesMap[k]) return fighterImagesMap[k];
  if (window.fighterImagesMap && window.fighterImagesMap[k]) return window.fighterImagesMap[k];
  return null;
}

function renderFighterAvatar(name, corner='red') {
  const imgUrl = getFighterImage(name);
  const initials = (name || '').split(' ').map(n => n[0]).filter(Boolean).slice(0, 2).join('').toUpperCase() || 'MMA';
  if (imgUrl) {
    return `<div class="fighter-avatar-frame ${corner}-frame" title="${esc(name)}">
      <img src="${esc(imgUrl)}" alt="${esc(name)}" class="fighter-img" loading="lazy" onerror="this.onerror=null; this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';" />
      <div class="fighter-initials" style="display:none">${esc(initials)}</div>
    </div>`;
  }
  return `<div class="fighter-avatar-frame ${corner}-frame" title="${esc(name)}">
    <div class="fighter-initials">${esc(initials)}</div>
  </div>`;
}

function getFighterData(name) {
  if (!name || !data?.fighters) return null;
  const key = v => v.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const k = key(name);
  return data.fighters.find(f => key(f.name) === k) || null;
}

function getFighterCoreMetrics(f) {
  if (!f) {
    return {
      volume: 0.4, accuracy: 0.4, avoidance: 0.4, td_acc: 0.4, td_def: 0.4, sub_threat: 0.4,
      raw: { volume: '—', accuracy: '—', avoidance: '—', td_acc: '—', td_def: '—', sub_threat: '—' }
    };
  }

  // 1. Striking Volume (SLpM)
  const slpm = parseFloat(f.SLpM) || 0;
  const normVol = Math.max(0.12, Math.min(0.98, slpm / 7.5));

  // 2. Striking Accuracy (sig_str_acc)
  let strAcc = parseFloat(f.sig_str_acc) || 0;
  if (strAcc > 1) strAcc = strAcc / 100;
  const normAcc = Math.max(0.12, Math.min(0.98, strAcc / 0.65));

  // 3. Damage Avoidance (str_def or 1 - SApM / 7.0)
  let strDef = parseFloat(f.str_def) || 0;
  if (strDef > 1) strDef = strDef / 100;
  if (!strDef && f.SApM) {
    const sapm = parseFloat(f.SApM) || 3.5;
    strDef = Math.max(0, 1 - (sapm / 7.0));
  }
  const normAvoid = Math.max(0.12, Math.min(0.98, strDef / 0.78));

  // 4. Takedown Accuracy (td_acc)
  let tdAcc = parseFloat(f.td_acc) || 0;
  if (tdAcc > 1) tdAcc = tdAcc / 100;
  const normTdAcc = Math.max(0.12, Math.min(0.98, tdAcc / 0.65));

  // 5. Takedown Defense (td_def)
  let tdDef = parseFloat(f.td_def) || 0;
  if (tdDef > 1) tdDef = tdDef / 100;
  const normTdDef = Math.max(0.12, Math.min(0.98, tdDef / 0.90));

  // 6. Submission Threat (sub_avg)
  const subAvg = parseFloat(f.sub_avg) || 0;
  const normSub = Math.max(0.12, Math.min(0.98, subAvg / 2.0));

  return {
    volume: normVol,
    accuracy: normAcc,
    avoidance: normAvoid,
    td_acc: normTdAcc,
    td_def: normTdDef,
    sub_threat: normSub,
    raw: {
      volume: slpm ? slpm.toFixed(1) : '—',
      accuracy: strAcc ? `${Math.round(strAcc * 100)}%` : '—',
      avoidance: strDef ? `${Math.round(strDef * 100)}%` : '—',
      td_acc: tdAcc ? `${Math.round(tdAcc * 100)}%` : '—',
      td_def: tdDef ? `${Math.round(tdDef * 100)}%` : '—',
      sub_threat: subAvg ? subAvg.toFixed(1) : '—'
    }
  };
}

function renderPerformanceSnapshot(redName, blueName) {
  const redF = getFighterData(redName);
  const blueF = blueName ? getFighterData(blueName) : null;
  const redM = getFighterCoreMetrics(redF);
  const blueM = blueName ? getFighterCoreMetrics(blueF) : null;

  const CX = 175;
  const CY = 148;
  const R = 90;
  const angles = [
    -Math.PI / 2,        // 0: Striking Volume (Top)
    -Math.PI / 6,        // 1: Striking Accuracy (Top-Right)
    Math.PI / 6,         // 2: Damage Avoidance (Bottom-Right)
    Math.PI / 2,         // 3: Takedown Accuracy (Bottom)
    (5 * Math.PI) / 6,   // 4: Takedown Defense (Bottom-Left)
    -(5 * Math.PI) / 6   // 5: Submission Threat (Top-Left)
  ];

  // Concentric hexagonal grid rings
  const levels = [0.25, 0.5, 0.75, 1.0];
  const gridPolygons = levels.map(lvl => {
    const pts = angles.map(a => `${(CX + R * lvl * Math.cos(a)).toFixed(1)},${(CY + R * lvl * Math.sin(a)).toFixed(1)}`).join(' ');
    return `<polygon points="${pts}" class="radar-grid-ring" />`;
  }).join('');

  // 6 spokes
  const spokes = angles.map(a => {
    const x2 = (CX + R * Math.cos(a)).toFixed(1);
    const y2 = (CY + R * Math.sin(a)).toFixed(1);
    return `<line x1="${CX}" y1="${CY}" x2="${x2}" y2="${y2}" class="radar-spoke" />`;
  }).join('');

  // Red polygon and vertices
  const redVals = [redM.volume, redM.accuracy, redM.avoidance, redM.td_acc, redM.td_def, redM.sub_threat];
  const redPts = angles.map((a, i) => `${(CX + R * redVals[i] * Math.cos(a)).toFixed(1)},${(CY + R * redVals[i] * Math.sin(a)).toFixed(1)}`).join(' ');
  const redDots = angles.map((a, i) => {
    const x = (CX + R * redVals[i] * Math.cos(a)).toFixed(1);
    const y = (CY + R * redVals[i] * Math.sin(a)).toFixed(1);
    return `<circle cx="${x}" cy="${y}" r="3.5" class="radar-dot red-dot" />`;
  }).join('');

  // Blue polygon and vertices
  let blueMarkup = '';
  if (blueM) {
    const blueVals = [blueM.volume, blueM.accuracy, blueM.avoidance, blueM.td_acc, blueM.td_def, blueM.sub_threat];
    const bluePts = angles.map((a, i) => `${(CX + R * blueVals[i] * Math.cos(a)).toFixed(1)},${(CY + R * blueVals[i] * Math.sin(a)).toFixed(1)}`).join(' ');
    const blueDots = angles.map((a, i) => {
      const x = (CX + R * blueVals[i] * Math.cos(a)).toFixed(1);
      const y = (CY + R * blueVals[i] * Math.sin(a)).toFixed(1);
      return `<circle cx="${x}" cy="${y}" r="3.5" class="radar-dot blue-dot" />`;
    }).join('');
    blueMarkup = `<polygon points="${bluePts}" class="radar-poly blue-poly" filter="url(#radarBlueDrop)" />${blueDots}`;
  }

  const gradId = 'radarGlow_' + Math.random().toString(36).slice(2, 7);

  return `
    <div class="performance-snapshot-card">
      <div class="snapshot-header">
        <span class="snapshot-subtitle">PERFORMANCE SNAPSHOT</span>
        <h4 class="snapshot-title">${blueName ? "Fighters' Core Metrics" : esc(redName) + " \u00b7 Core Metrics"}</h4>
      </div>
      <div class="radar-chart-container">
        <svg viewBox="0 0 350 295" class="radar-svg" role="img" aria-label="Fighters Core Metrics Radar Chart">
          <defs>
            <radialGradient id="${gradId}" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stop-color="#4a3e72" stop-opacity="0.45" />
              <stop offset="60%" stop-color="#212338" stop-opacity="0.2" />
              <stop offset="100%" stop-color="#0e111a" stop-opacity="0" />
            </radialGradient>
            <filter id="radarRedDrop" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#ff4d6d" flood-opacity="0.65"/>
            </filter>
            <filter id="radarBlueDrop" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="0" stdDeviation="2.5" flood-color="#00d4ff" flood-opacity="0.65"/>
            </filter>
          </defs>
          <circle cx="${CX}" cy="${CY}" r="${R}" fill="url(#${gradId})" />
          ${gridPolygons}
          ${spokes}
          <polygon points="${redPts}" class="radar-poly red-poly" filter="url(#radarRedDrop)" />
          ${blueMarkup}
          ${redDots}
          <!-- Axes Labels exactly matching user reference -->
          <text x="175" y="22" text-anchor="middle" class="radar-text"><tspan x="175" dy="0">Striking</tspan><tspan x="175" dy="13">Volume</tspan></text>
          <text x="265" y="98" text-anchor="start" class="radar-text"><tspan x="265" dy="0">Striking</tspan><tspan x="265" dy="13">Accuracy</tspan></text>
          <text x="265" y="194" text-anchor="start" class="radar-text"><tspan x="265" dy="0">Damage</tspan><tspan x="265" dy="13">Avoidance</tspan></text>
          <text x="175" y="262" text-anchor="middle" class="radar-text"><tspan x="175" dy="0">Takedown</tspan><tspan x="175" dy="13">Accuracy</tspan></text>
          <text x="85" y="194" text-anchor="end" class="radar-text"><tspan x="85" dy="0">Takedown</tspan><tspan x="85" dy="13">Defense</tspan></text>
          <text x="85" y="98" text-anchor="end" class="radar-text"><tspan x="85" dy="0">Submission</tspan><tspan x="85" dy="13">Threat</tspan></text>
        </svg>
      </div>
      <div class="snapshot-legend">
        <div class="legend-fighter legend-red">
          <span class="legend-badge-dot"></span>
          <strong>${esc(redName)}</strong>
        </div>
        ${blueName ? `
          <div class="legend-fighter legend-blue">
            <span class="legend-badge-dot"></span>
            <strong>${esc(blueName)}</strong>
          </div>
        ` : ''}
      </div>
      <div class="metrics-breakdown-row">
        <div class="metric-pill" title="Significant Strikes Landed per Minute">
          <span class="m-val red">${esc(redM.raw.volume)}</span>
          <span class="m-name">SLpM</span>
          ${blueM ? `<span class="m-val blue">${esc(blueM.raw.volume)}</span>` : ''}
        </div>
        <div class="metric-pill" title="Significant Strike Accuracy">
          <span class="m-val red">${esc(redM.raw.accuracy)}</span>
          <span class="m-name">Accuracy</span>
          ${blueM ? `<span class="m-val blue">${esc(blueM.raw.accuracy)}</span>` : ''}
        </div>
        <div class="metric-pill" title="Significant Strike Defense / Avoidance">
          <span class="m-val red">${esc(redM.raw.avoidance)}</span>
          <span class="m-name">Avoidance</span>
          ${blueM ? `<span class="m-val blue">${esc(blueM.raw.avoidance)}</span>` : ''}
        </div>
        <div class="metric-pill" title="Takedown Accuracy">
          <span class="m-val red">${esc(redM.raw.td_acc)}</span>
          <span class="m-name">TD Acc</span>
          ${blueM ? `<span class="m-val blue">${esc(blueM.raw.td_acc)}</span>` : ''}
        </div>
        <div class="metric-pill" title="Takedown Defense">
          <span class="m-val red">${esc(redM.raw.td_def)}</span>
          <span class="m-name">TD Def</span>
          ${blueM ? `<span class="m-val blue">${esc(blueM.raw.td_def)}</span>` : ''}
        </div>
        <div class="metric-pill" title="Submission Attempts per 15 min">
          <span class="m-val red">${esc(redM.raw.sub_threat)}</span>
          <span class="m-name">Sub / 15m</span>
          ${blueM ? `<span class="m-val blue">${esc(blueM.raw.sub_threat)}</span>` : ''}
        </div>
      </div>
    </div>
  `;
}

const expandedFightIds = new Set();
const openComparisonFightIds = new Set();

function quickOddsBar(f) {
  const o = f.odds;
  if (!o || !o.available || !o.books || !o.books.length) return '';
  const bestBook = o.books[0];
  const redPrice = line(bestBook.red);
  const bluePrice = line(bestBook.blue);
  return `
    <div class="quick-odds-bar">
      <div class="quick-odds-label">
        <span class="odds-live-dot"></span>
        <span>${esc(bestBook.name || 'Sportsbook')} Moneyline</span>
      </div>
      <div class="odds-prices">
        <strong title="${esc(f.red)}">${redPrice}</strong>
        <strong title="${esc(f.blue)}">${bluePrice}</strong>
      </div>
    </div>
  `;
}

function toggleCardDrawer(fightId) {
  const drawer = $(`drawer-${fightId}`);
  const btn = document.querySelector(`[data-toggle-drawer="${fightId}"]`);
  if (!drawer || !btn) return;
  const isOpening = drawer.hidden;
  if (isOpening) {
    drawer.hidden = false;
    expandedFightIds.add(fightId);
    btn.setAttribute('aria-expanded', 'true');
    btn.classList.add('is-expanded');
    const label = btn.querySelector('.expand-label');
    const icon = btn.querySelector('.expand-icon');
    if (label) label.textContent = 'Hide Deep Analysis';
    if (icon) icon.textContent = '▴';
  } else {
    drawer.hidden = true;
    expandedFightIds.delete(fightId);
    btn.setAttribute('aria-expanded', 'false');
    btn.classList.remove('is-expanded');
    const label = btn.querySelector('.expand-label');
    const icon = btn.querySelector('.expand-icon');
    if (label) label.textContent = 'Deep Fight Analysis & Stats';
    if (icon) icon.textContent = '▾';
  }
}

function card(f, withProfiles=false, isMainEvent=false) {
  const pending = f.prediction_pending;
  const edge = modelEdge(f);
  const isUpset = Boolean(f.upset_radar?.is_candidate || edge);
  const favs = getFavorites();
  const isExpanded = expandedFightIds.has(f.id);

  return `<article class="fight-card${isMainEvent ? ' is-main-event' : ''}${isWomensFight(f)?' womens-fight':''}${edge?' has-model-edge':''}${isUpset?' is-upset-card':''}" data-fight-id="${esc(f.id || 'custom')}">
    <div class="fight-top">
      <div class="fight-top-tag-wrap">
        ${isMainEvent ? '<span class="main-event-badge">👑 MAIN EVENT</span>' : ''}
        <span>${esc(f.weight_class || 'CUSTOM MATCHUP')}</span>
      </div>
      <span>${f.locked?'FORECAST LOCKED':'MMA CIPHER PROBABILITY'}</span>
    </div>
    ${renderUpsetBanner(f)}
    ${edge?`<div class="model-edge"><strong>★ MODEL EDGE</strong><span>${esc(f.pick)} &middot; ${pct(edge.probability)} model chance &middot; ${line(edge.price)} at ${esc(edge.name)}</span><small>+${(edge.edge*100).toFixed(1)} points vs. ${pct(edge.implied)} break-even probability. Model estimate, not a guaranteed return.</small></div>`:''}
    ${countdown(f)}
    <div class="fighters-row">
      <div class="fighter-cell red-cell">
        <div class="fighter-avatar-wrap red-avatar-wrap">
          ${renderFighterAvatar(f.red, 'red')}
        </div>
        <div class="fighter-details">
          <div class="corner">FIRST FIGHTER</div>
          <div class="fighter-name">
            ${esc(f.red)}
            <button class="fav-btn ${favs.has(f.red)?'is-fav':''}" data-fav-name="${esc(f.red)}" aria-label="Favorite ${esc(f.red)}" title="Favorite ${esc(f.red)}">★</button>
          </div>
        </div>
      </div>
      <span class="versus">VS</span>
      <div class="fighter-cell blue-cell">
        <div class="fighter-avatar-wrap blue-avatar-wrap">
          ${renderFighterAvatar(f.blue, 'blue')}
        </div>
        <div class="fighter-details">
          <div class="corner blue">SECOND FIGHTER</div>
          <div class="fighter-name">
            ${esc(f.blue)}
            <button class="fav-btn ${favs.has(f.blue)?'is-fav':''}" data-fav-name="${esc(f.blue)}" aria-label="Favorite ${esc(f.blue)}" title="Favorite ${esc(f.blue)}">★</button>
          </div>
        </div>
      </div>
    </div>
    ${pending?'<p class="muted">No eligible pre-event forecast yet. Current matchup and available odds are shown below.</p>':`
      <div class="probabilities"><span>${pct(f.red_probability)}</span><span>${pct(f.blue_probability)}</span></div>
      <div class="probability-bar" role="img" aria-label="${esc(f.red)} ${pct(f.red_probability)}, ${esc(f.blue)} ${pct(f.blue_probability)}">
        <span style="width:${Math.max(0,Math.min(100,f.red_probability*100))}%"></span>
      </div>
      <div class="pick">
        <div><span class="pick-label">PROJECTED WINNER</span><strong>${esc(f.pick)}</strong></div>
        <span class="pick-conf">\u2197 ${pct(f.confidence)}</span>
      </div>
    `}
    ${quickOddsBar(f)}
    <div class="card-expand-bar">
      <button class="expand-card-btn ${isExpanded?'is-expanded':''}" data-toggle-drawer="${esc(f.id)}" aria-expanded="${isExpanded}">
        <span class="expand-label">${isExpanded?'Hide Deep Analysis':'Deep Fight Analysis & Stats'}</span>
        <span class="expand-icon">${isExpanded?'▴':'▾'}</span>
      </button>
    </div>
    <div class="card-details-drawer ${isExpanded?'is-open':''}" id="drawer-${esc(f.id)}" ${isExpanded?'':'hidden'}>
      ${!pending ? `
        ${renderUserPickButtons(f)}
        ${renderMethod(f)}
        ${renderExplanation(f)}
        ${renderEnsemble(f)}
      ` : ''}
      ${renderTape(f)}
      ${oddsPanel(f)}
      ${f.low_history?'<div class="low-history">Limited history: at least one fighter has fewer than 3 recorded bouts.</div>':''}
      ${!pending?`<div class="forecast-meta">${f.issued_at?'Prediction updated '+esc(stamp(f.issued_at)):'MMA Cipher Ensemble prediction'}</div>`:''}
      <div class="card-footer-actions">
        <button class="share-card-btn" data-share-id="${esc(f.id)}"><span>⎘</span> Share Prediction</button>
      </div>
      ${withProfiles?`<details class="stats-comparison-details" ${openComparisonFightIds.has(f.id)?'open':''}><summary>Compare current fighter stats</summary>${renderPerformanceSnapshot(f.red, f.blue)}<div class="comparison">${profile(f.red)}${profile(f.blue)}</div></details>`:''}
    </div>
  </article>`;
}

function profile(name) {
  const key = v => v.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const f = data.fighters.find(f => key(f.name) === key(name));
  if (!f) return '';
  const fields = [
    ['Record', f.record || `${parseInt(f.wins)||0}–${parseInt(f.losses)||0}`],
    ['Stance', f.stance],
    ['Height', f.height],
    ['Reach', f.reach],
    ['Strikes landed / min', f.SLpM],
    ['Strikes absorbed / min', f.SApM],
    ['Striking accuracy', f.sig_str_acc],
    ['Takedowns / 15 min', f.td_avg],
    ['Takedown defense', f.td_def],
    ['Submissions / 15 min', f.sub_avg]
  ];
  return `<div class="profile">
    <div class="profile-header-wrap">
      ${renderFighterAvatar(name, 'neutral')}
      <div>
        <h3>${esc(name)}</h3>
        <span class="tag">${!f.fetched_at?'ARCHIVED PROFILE':Date.now()-Date.parse(f.fetched_at)>(data.refresh?.stats_seconds||3600)*2000?'CACHED \u00b7 REFRESH PENDING':'UFCSTATS PROFILE'}</span>
      </div>
    </div>
    ${fields.map(([k,v])=>`<div class="stat-row"><span>${esc(k)}</span><strong>${esc(v || '—')}${!f.fetched_at && ['Height','Reach'].includes(k)?' cm':''}</strong></div>`).join('')}
    <span class="muted">${esc(f.source)}${f.fetched_at?' · Fetched '+esc(stamp(f.fetched_at)):''}</span>
  </div>`;
}

const openUndercardSections = new Set();

function cardSections(fights) {
  const favs = getFavorites();
  let filtered = fights;
  if (currentCardFilter === 'upset') {
    filtered = fights.filter(f => f.upset_radar?.is_candidate || modelEdge(f));
  } else if (currentCardFilter === 'favorites') {
    filtered = fights.filter(f => favs.has(f.red) || favs.has(f.blue));
  }

  if (!filtered.length) {
    if (currentCardFilter === 'upset') {
      return empty('No Upset Radar alerts on this card', 'MMA Cipher has not detected any heavy underdog style clashes or market pricing edges for this selection.');
    } else if (currentCardFilter === 'favorites') {
      return empty('No starred fighters on this card', 'Star fighters with the ★ button next to their name or in the Fighter Database to track them here.');
    }
  }

  const mainBoutId = fights.length ? fights[0].id : null;
  const sections = [['main','Main card'],['prelims','Undercard / Prelims'],['early','Early prelims'],['unassigned','Card placement pending']];
  return sections.map(([key, label]) => {
    const group = filtered.filter(f => (f.odds?.card_section || 'unassigned') === key);
    if (!group.length) return '';
    if (key === 'main') {
      return `<section class="card-section" aria-label="${label}">
        <div class="card-section-heading">
          <h4>${label}</h4>
          <span>${group.length} ${group.length===1?'bout':'bouts'}</span>
        </div>
        <p class="section-note">Grouped by scheduled broadcast start times. Card placement may change.</p>
        <div class="fight-grid">${group.map((f, i) => card(f, true, f.id === mainBoutId || Boolean(f.is_main_event) || i === 0)).join('')}</div>
      </section>`;
    }

    const isFilterActive = (currentCardFilter !== 'all');
    const wasOpen = openUndercardSections.has(label);
    const isOpen = isFilterActive || wasOpen;

    return `<details class="undercard-dropdown card-section" aria-label="${label}" ${isOpen ? 'open' : ''}>
      <summary class="undercard-summary">
        <div class="undercard-summary-info">
          <span class="undercard-pill-tag">⚡ UNDERCARD</span>
          <div class="undercard-heading-group">
            <h4 class="undercard-title">${label}</h4>
            <span class="undercard-bouts-badge">${group.length} ${group.length===1?'bout':'bouts'}</span>
          </div>
        </div>
        <div class="undercard-summary-action">
          <span class="undercard-action-hint show-text">Click to show undercard</span>
          <span class="undercard-action-hint hide-text">Click to hide undercard</span>
          <span class="undercard-chevron" aria-hidden="true">▾</span>
        </div>
      </summary>
      <div class="undercard-content">
        ${key==='unassigned'?'<p class="section-note">The published schedule does not yet confirm these card placements.</p>':'<p class="section-note">Grouped by scheduled broadcast start times. Card placement may change.</p>'}
        <div class="fight-grid">${group.map(f => card(f, true, false)).join('')}</div>
      </div>
    </details>`;
  }).join('');
}

function updateNextEvent() {
  const link = $('next-event-link');
  if (!link) return;
  const now = Date.now();
  const today = new Date().toISOString().slice(0,10);
  const next = data.upcoming.find(f => {
    const state = f.odds?.state;
    const start = Date.parse(f.odds?.start_at || f.start_at);
    return state !== 'post' && state !== 'in' && (Number.isFinite(start) ? start > now : f.date >= today);
  });
  link.dataset.event = next ? `${next.date}|${next.event}` : '';
  $('next-event-label').textContent = next ? 'NEXT EVENT' : 'EXPLORE';
  $('next-event-date').textContent = next ? new Date(`${next.date}T12:00:00`).toLocaleDateString(undefined,{month:'short',day:'numeric'}) : 'Fight cards';
  link.title = next ? `${next.event} - View fight card` : 'Browse upcoming fight cards';
  link.setAttribute('aria-label', next ? `View ${next.event} on ${day(next.date)}` : 'Browse upcoming fight cards');
}

$('next-event-link')?.addEventListener('click', () => {
  showTab('upcoming');
  const event = $('next-event-link').dataset.event;
  if (data) {
    if (event) $('event-filter').value = event;
    render();
  }
});

function attachCardListeners() {
  // Favorite star clicks
  document.querySelectorAll('.fav-btn').forEach(b => {
    b.onclick = (e) => {
      e.stopPropagation();
      toggleFavorite(b.dataset.favName);
    };
  });
  // Community pick buttons
  document.querySelectorAll('.pick-btn').forEach(b => {
    b.onclick = (e) => {
      e.stopPropagation();
      setUserPick(b.dataset.pickFight, b.dataset.pickSide);
    };
  });
  // Expand / collapse drawer buttons
  document.querySelectorAll('.expand-card-btn').forEach(b => {
    b.onclick = (e) => {
      e.stopPropagation();
      toggleCardDrawer(b.dataset.toggleDrawer);
    };
  });
  // Share prediction buttons
  document.querySelectorAll('.share-card-btn').forEach(b => {
    b.onclick = (e) => {
      e.stopPropagation();
      openShareModal(b.dataset.shareId);
    };
  });
  // Track open comparison details
  document.querySelectorAll('.stats-comparison-details').forEach(d => {
    d.ontoggle = () => {
      const cardEl = d.closest('[data-fight-id]');
      const fid = cardEl?.dataset?.fightId;
      if (fid) {
        if (d.open) openComparisonFightIds.add(fid);
        else openComparisonFightIds.delete(fid);
      }
    };
  });
  // Track open undercard dropdown menus
  document.querySelectorAll('details.undercard-dropdown').forEach(d => {
    d.ontoggle = () => {
      const label = d.getAttribute('aria-label');
      if (label) {
        if (d.open) openUndercardSections.add(label);
        else openUndercardSections.delete(label);
      }
    };
  });
}

// ==========================================
// Main Dashboard Render
// ==========================================
function render() {
  const m = data.model;
  if (m) {
    $('model-summary').innerHTML = `
      <span class="model-overview">MMA Cipher runs an advanced prediction algorithm combining gradient-boosted decision trees, LightGBM, regularized logistic, and random forests.</span>
      <span class="model-selection">
        <span class="model-kicker">PREDICTION ALGORITHM</span>
        <strong class="active-model">${esc(m.name)}</strong>
        <span class="model-estimator">Out-of-Fold Nelder-Mead Optimization · Schema v${esc(m.schema)}</span>
      </span>
      <span class="model-training">Trained on <strong class="glow-blue">${m.metrics.training_fights.toLocaleString()} confirmed UFC bouts</strong> with strict zero-leakage guarantee.</span>
      <span class="model-inputs compact-inputs" role="list" aria-label="Feature engineering">
        <span role="listitem">Opponent-adjusted Elo</span>
        <span role="listitem">Age differential</span>
        <span role="listitem">Height &amp; reach differential</span>
        <span role="listitem">Striking pace &amp; accuracy</span>
        <span role="listitem">Takedown success &amp; volume</span>
        <span role="listitem">Finish resistance &amp; durability</span>
      </span>
    `;
  }
  $('accuracy').textContent = pct(m?.metrics.accuracy);
  $('test-count').textContent = m ? `${m.metrics.test_fights.toLocaleString()} fights · ${day(m.metrics.test_start)}–${day(m.metrics.test_end)}` : 'Model pending';
  $('fight-count').textContent = data.total_fights.toLocaleString();
  $('live-accuracy').textContent = pct(data.live_accuracy);
  $('live-count').textContent = data.scored_predictions ? `${data.scored_predictions} settled forecasts` : 'Awaiting settled forecasts';
  $('last-sync').textContent = data.status.last_sync ? new Date(data.status.last_sync).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not yet';
  const pullInterval = getDynamicPullInterval(data?.upcoming);
  const isFightCadence = pullInterval <= 6 * 3600 * 1000;
  $('sync-state').textContent = data.status.running ? data.status.phase : (isFightCadence ? 'Fight day: updating every 6h' : 'Updating every 24h (6h on fight day)');
  const stale = !data.status.last_sync || Date.now() - Date.parse(data.status.last_sync) > (data.refresh.stats_seconds * 2000);
  const lastFailure = data.status.last_error;
  const error = data.status.error || (lastFailure && (!data.status.last_sync || Date.parse(lastFailure.at) > Date.parse(data.status.last_sync)) ? lastFailure.message : null);
  const profileWarning = data.profile_errors?.length ? ` ${data.profile_errors.length} fighter profiles could not be refreshed and are labeled as cached.` : '';
  $('odds-sync').textContent = data.odds_status.error ? 'Odds delayed / retrying automatically' : data.odds_status.running ? 'Refreshing odds...' : data.odds_status.checked_at ? `Odds checked ${stamp(data.odds_status.checked_at)}` : 'Waiting for sportsbook odds';
  $('odds-sync').classList.toggle('warning', Boolean(data.odds_status.error));
  $('notice').classList.toggle('warning', Boolean(!data.status.running && (error || stale)));
  $('notice').textContent = data.status.running ? 'Refreshing fight data in the background. Saved data remains available.' : error ? 'Fight data is delayed. Showing saved data; retrying automatically.' : stale ? 'Showing saved data. A fresh update is pending.' : `Fight data up to date / ${stamp(data.status.last_sync)}`;
  $('notice').textContent += profileWarning;
  updateNextEvent();
  if (data.refresh.automatic === false) {
    $('notice').classList.add('warning');
    $('notice').textContent += ' Automatic server updates are disabled.';
  }
  $('card-count').textContent = data.upcoming.length;
  if ($('fighters-count')) $('fighters-count').textContent = `${(data.fighters.length / 1000).toFixed(1)}k`;

  // Update pill counts
  const upsetCount = data.upcoming.filter(f => f.upset_radar?.is_candidate || modelEdge(f)).length;
  if ($('upset-pill-count')) $('upset-pill-count').textContent = upsetCount;
  updateFavCounts();

  const expanded = new Set([...document.querySelectorAll('#cards .fight-card')].flatMap(c => [...c.querySelectorAll('details')].map((d, i) => d.open ? c.dataset.fightId + '|' + i : null).filter(Boolean)));
  const groups = data.upcoming.reduce((groups, f) => {
    const key = `${f.date}|${f.event}`;
    (groups[key] ||= []).push(f);
    return groups;
  }, Object.create(null));
  let selection = $('event-filter').value;
  if (selection !== 'all' && !groups[selection]) selection = Object.keys(groups)[0] || 'all';
  $('event-filter').innerHTML = '<option value="all">All upcoming events</option>' + Object.entries(groups).map(([key, fs]) => `<option value="${esc(key)}">${esc(fs[0].event)}</option>`).join('');
  $('event-filter').value = selection;
  const visible = selection === 'all' ? Object.values(groups) : [groups[selection]];
  $('cards').innerHTML = data.upcoming.length ? visible.map(fs => `<div class="event-heading"><h3>${esc(fs[0].event)}</h3><span class="tag">${esc(day(fs[0].date))}</span></div>${cardSections(fs)}`).join('') : empty('The next card is on its way', 'Upcoming predictions appear after a successful UFCStats refresh. In the meantime, compare any two fighters in the MMA Cipher Matchup Lab.', true);
  
  document.querySelectorAll('#cards .fight-card').forEach(c => c.querySelectorAll('details').forEach((d, i) => {
    d.open = expanded.has(c.dataset.fightId + '|' + i);
  }));
  attachCardListeners();

  $('fighter-list').innerHTML = data.fighters.map(f => `<option value="${esc(f.name)}"></option>`).join('');
  $('results-list').innerHTML = data.results.length ? data.results.map(r => `<article class="result"><div><h3>${esc(r.red)} vs. ${esc(r.blue)}</h3><p>${esc(day(r.date))} · Predicted ${esc(r.pick)} (${pct(r.confidence)}) · ${r.actual_winner ? 'Winner: ' + esc(r.actual_winner) : 'Draw / no contest — unscored'}</p></div><span class="badge ${r.correct===false?'miss':''}">${r.correct===null?'UNSCORED':r.correct?'CORRECT':'MISSED'}</span></article>`).join('') : empty('A record earned in real fights', 'Once a saved forecast settles, the original pick and the actual result appear here. Historical test scores are tracked separately.');
  
  if (m) {
    const weights = m.weights || {};
    const candAccs = m.metrics.candidate_holdout_accuracies || {};
    $('model-info').innerHTML = `
      <div class="model-grid">
        <div class="info-box">
          <h3>${esc(m.name)}</h3>
          <div class="stat-row"><span>Results through</span><strong>${esc(day(m.through))}</strong></div>
          <div class="stat-row"><span>Ensemble holdout accuracy</span><strong class="glow-rose">${pct(m.metrics.accuracy)}</strong></div>
          <div class="stat-row"><span>Elo baseline accuracy</span><strong>${pct(m.metrics.elo_accuracy)}</strong></div>
          <div class="stat-row"><span>Majority class baseline</span><strong>${pct(m.metrics.majority_accuracy)}</strong></div>
          <div class="stat-row"><span>Brier score (lower is better)</span><strong>${m.metrics.brier.toFixed(4)}</strong></div>
          <div class="stat-row"><span>Log loss (lower is better)</span><strong>${m.metrics.log_loss.toFixed(4)}</strong></div>
          
          <h4 style="margin:20px 0 10px;font-size:13px;letter-spacing:1px;color:#00f0ff;">ENSEMBLE CANDIDATE MODELS &amp; WEIGHTS</h4>
          ${Object.entries(weights).map(([name, w]) => `
            <div class="stat-row">
              <span>${esc(name.replace('MMA Cipher ', ''))}</span>
              <strong>Holdout: ${pct(candAccs[name])} · Weight: ${(w*100).toFixed(1)}%</strong>
            </div>
          `).join('')}
          <p style="margin-top:14px;">Ensemble blend weights are determined using out-of-fold Nelder-Mead optimization across expanding chronological windows, strictly penalizing overconfidence and minimizing Log Loss.</p>
        </div>
        <div class="info-box">
          <h3>Strict Zero-Leakage Guarantee</h3>
          <ol>
            <li>Every feature for a bout is calculated purely from data available prior to the calendar day.</li>
            <li>No post-fight statistics, career averages from the future, or end-of-career ratings are ever leaked.</li>
            <li>Symmetric corner invariance ensures p(A, B) = 1 - p(B, A).</li>
            <li>Walk-forward chronological backtesting reserves the final 20% of event dates as a pristine unseen holdout.</li>
            <li>Method of victory (KO/TKO, Submission, Decision) is modeled via calibrated multi-class regularized estimation.</li>
          </ol>
          <p>The historical database contains over 6,500 UFC bouts spanning 2010 through 2024. Dynamic Elo ratings update chronologically after each verified bout outcome.</p>
        </div>
      </div>
    `;
  }
  document.querySelectorAll('[data-open-lab]').forEach(b => b.addEventListener('click', () => showTab('matchup')));
}

// ==========================================
// AI Analytics Rendering
// ==========================================
function renderAnalytics() {
  if (!data || !data.model) return;
  renderAccuracyChart();
  renderModelsLeaderboard();
  renderCalibrationChart();
  renderConfidenceDistribution();
  renderDivisionAccuracy();
  renderFeatureImportances();
}

function renderAccuracyChart() {
  const container = $('accuracy-chart-container');
  if (!container || !data?.model?.metrics?.events_history) return;
  let events = [...data.model.metrics.events_history];
  if (analyticsScope === '10') events = events.slice(-10);
  else if (analyticsScope === '25') events = events.slice(-25);
  else if (analyticsScope === '50') events = events.slice(-50);

  if (events.length < 2) return;

  const width = container.clientWidth || 800;
  const height = 280;
  const pad = { top: 25, right: 30, bottom: 40, left: 45 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const minY = 0.40;
  const maxY = 0.90;
  const getX = (i) => pad.left + (i / (events.length - 1)) * w;
  const getY = (val) => pad.top + h - ((val - minY) / (maxY - minY)) * h;

  // Compute rolling 5-event accuracy for smooth curve
  const rolling = events.map((ev, i) => {
    const slice = events.slice(Math.max(0, i - 4), i + 1);
    const corr = slice.reduce((a, b) => a + b.correct, 0);
    const tot = slice.reduce((a, b) => a + b.fights, 0);
    return tot ? corr / tot : ev.accuracy;
  });

  const cumPoints = events.map((ev, i) => `${getX(i).toFixed(1)},${getY(ev.cumulative_accuracy).toFixed(1)}`).join(' ');
  const rollPoints = events.map((ev, i) => `${getX(i).toFixed(1)},${getY(rolling[i]).toFixed(1)}`).join(' ');
  const areaPoints = `${getX(0)},${pad.top + h} ` + cumPoints + ` ${getX(events.length - 1)},${pad.top + h}`;

  // Gridlines
  const yTicks = [0.45, 0.55, 0.65, 0.75, 0.85];
  const gridSvg = yTicks.map(t => `
    <line x1="${pad.left}" y1="${getY(t)}" x2="${pad.left + w}" y2="${getY(t)}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="3 3"/>
    <text x="${pad.left - 8}" y="${getY(t) + 3}" fill="#64748b" font-size="10" text-anchor="end">${(t*100).toFixed(0)}%</text>
  `).join('');

  // Interactive hover targets
  const hoverDots = events.map((ev, i) => `
    <circle cx="${getX(i)}" cy="${getY(ev.cumulative_accuracy)}" r="4" fill="#00f0ff" stroke="#06070a" stroke-width="2" class="chart-point" data-idx="${i}" />
  `).join('');

  container.innerHTML = `
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <defs>
        <linearGradient id="chartAreaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#00f0ff" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="#00f0ff" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      ${gridSvg}
      <polygon points="${areaPoints}" fill="url(#chartAreaGrad)" />
      <polyline points="${rollPoints}" fill="none" stroke="#ff3366" stroke-width="2" stroke-dasharray="4 3" opacity="0.85" />
      <polyline points="${cumPoints}" fill="none" stroke="#00f0ff" stroke-width="2.5" />
      ${hoverDots}
    </svg>
    <div id="chart-tooltip" class="chart-tooltip"></div>
  `;

  const tooltip = $('chart-tooltip');
  container.querySelectorAll('.chart-point').forEach(pt => {
    pt.addEventListener('mouseenter', (e) => {
      const idx = parseInt(pt.dataset.idx, 10);
      const ev = events[idx];
      tooltip.style.display = 'block';
      tooltip.style.left = `${pt.getAttribute('cx')}px`;
      tooltip.style.top = `${pt.getAttribute('cy')}px`;
      tooltip.innerHTML = `
        <strong>${esc(ev.event)}</strong><br>
        <span style="color:#9499ab">${day(ev.date)}</span><br>
        Bouts: ${ev.fights} (${ev.correct} correct · ${(ev.accuracy*100).toFixed(0)}%)<br>
        <span style="color:#00f0ff">Cumulative: ${(ev.cumulative_accuracy*100).toFixed(1)}%</span>
      `;
    });
    pt.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
    });
  });
}

function renderModelsLeaderboard() {
  const container = $('models-leaderboard');
  if (!container || !data?.model?.metrics?.candidate_metrics) return;
  const models = data.model.metrics.candidate_metrics;
  const weights = data.model.weights || {};

  container.innerHTML = Object.entries(models).map(([name, m], i) => `
    <div class="leaderboard-item">
      <div class="leaderboard-name">
        <span class="leaderboard-rank">#${i + 1}</span>
        <span>${esc(name.replace('MMA Cipher ', ''))}</span>
      </div>
      <div class="leaderboard-stats">
        <div class="leaderboard-stat-col">
          <span>HOLDOUT ACC</span>
          <strong>${pct(m.accuracy)}</strong>
        </div>
        <div class="leaderboard-stat-col">
          <span>LOG LOSS</span>
          <strong>${m.log_loss.toFixed(4)}</strong>
        </div>
        <div class="leaderboard-stat-col">
          <span>BRIER</span>
          <strong>${m.brier.toFixed(4)}</strong>
        </div>
        <div class="leaderboard-stat-col">
          <span>WEIGHT</span>
          <strong style="color:var(--accent)">${((weights[name]||0)*100).toFixed(1)}%</strong>
        </div>
      </div>
    </div>
  `).join('');
}

function renderCalibrationChart() {
  const container = $('calibration-chart-container');
  if (!container || !data?.model?.metrics?.calibration) return;
  const calib = data.model.metrics.calibration;

  const w = 340;
  const h = 200;
  const pad = { top: 20, right: 20, bottom: 35, left: 40 };
  const pw = w - pad.left - pad.right;
  const ph = h - pad.top - pad.bottom;

  const getX = (val) => pad.left + ((val - 0.5) / 0.5) * pw;
  const getY = (val) => pad.top + ph - ((val - 0.5) / 0.5) * ph;

  const diagLine = `<line x1="${getX(0.5)}" y1="${getY(0.5)}" x2="${getX(1.0)}" y2="${getY(1.0)}" stroke="rgba(255,255,255,0.2)" stroke-dasharray="3 3"/>`;
  const points = calib.filter(c => c.count > 0).map(c => `
    <circle cx="${getX(c.expected)}" cy="${getY(c.observed)}" r="${Math.max(4, Math.min(10, Math.log(c.count) * 2))}" fill="#00f0ff" opacity="0.9" stroke="#fff" stroke-width="1.5">
      <title>${c.range}: Expected ${(c.expected*100).toFixed(1)}%, Observed ${(c.observed*100).toFixed(1)}% (N=${c.count})</title>
    </circle>
    <text x="${getX(c.expected)}" y="${getY(c.observed) - 10}" fill="#9499ab" font-size="9" text-anchor="middle">N=${c.count}</text>
  `).join('');

  container.innerHTML = `
    <svg class="chart-svg" viewBox="0 0 ${w} ${h}">
      ${diagLine}
      <line x1="${pad.left}" y1="${pad.top+ph}" x2="${pad.left+pw}" y2="${pad.top+ph}" stroke="rgba(255,255,255,0.1)"/>
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top+ph}" stroke="rgba(255,255,255,0.1)"/>
      <text x="${pad.left}" y="${h - 8}" fill="#64748b" font-size="9">50%</text>
      <text x="${pad.left + pw}" y="${h - 8}" fill="#64748b" font-size="9" text-anchor="end">100%</text>
      <text x="${pad.left - 6}" y="${pad.top + 10}" fill="#64748b" font-size="9" text-anchor="end">100%</text>
      <text x="${pad.left - 6}" y="${pad.top + ph}" fill="#64748b" font-size="9" text-anchor="end">50%</text>
      ${points}
    </svg>
    <div style="font-size:10px;color:#9499ab;text-align:center;margin-top:4px;">
      Diagonal line = Perfect Calibration · Cyan bubbles = Empirical Bins (size by volume)
    </div>
  `;
}

function renderConfidenceDistribution() {
  const container = $('confidence-distribution');
  if (!container || !data?.model?.metrics?.confidence_distribution) return;
  const dist = data.model.metrics.confidence_distribution;

  container.innerHTML = dist.map(d => `
    <div class="dist-tier-row">
      <span class="dist-tier-label">${esc(d.range)}</span>
      <div class="dist-track">
        <div class="dist-fill" style="width: ${d.percentage}%"></div>
      </div>
      <span class="dist-percent">${d.percentage}%</span>
    </div>
  `).join('');
}

function renderDivisionAccuracy() {
  const container = $('division-accuracy');
  if (!container || !data?.model?.metrics?.weight_class_stats) return;
  const wc = data.model.metrics.weight_class_stats;

  container.innerHTML = Object.entries(wc).map(([name, s]) => `
    <div class="div-stat-item">
      <div class="div-stat-top">
        <span class="div-stat-name">${esc(name)}</span>
        <span class="div-stat-val">${(s.accuracy * 100).toFixed(1)}% <small style="color:var(--muted)">(${s.fights})</small></span>
      </div>
      <div class="div-track">
        <div class="div-fill" style="width: ${Math.max(0, Math.min(100, s.accuracy * 100))}%"></div>
      </div>
    </div>
  `).join('');
}

function renderFeatureImportances() {
  const container = $('feature-importance-grid');
  if (!container || !data?.model?.metrics?.feature_importances) return;
  const feats = data.model.metrics.feature_importances.slice(0, 12);
  const maxImp = Math.max(...feats.map(f => f.importance), 0.01);

  container.innerHTML = feats.map(f => `
    <div class="feat-item">
      <div class="feat-top">
        <span>${esc(f.feature)}</span>
        <strong style="color:var(--accent)">${f.importance.toFixed(3)}</strong>
      </div>
      <div class="feat-track">
        <div class="feat-fill" style="width: ${(f.importance / maxImp) * 100}%"></div>
      </div>
    </div>
  `).join('');
}

// ==========================================
// Fighter Database Rendering
// ==========================================
function renderFighterDatabase() {
  const container = $('fighter-grid');
  if (!container || !data?.fighters) return;

  const query = fighterSearchQuery.trim().toLowerCase();
  const divFilter = fighterDivisionFilter;
  const favs = getFavorites();

  const filtered = data.fighters.filter(f => {
    if (query && !f.name.toLowerCase().includes(query)) return false;
    if (divFilter !== 'all' && f.weight_class && !f.weight_class.toLowerCase().includes(divFilter.toLowerCase())) return false;
    return true;
  });

  const summary = $('fighter-results-summary');
  if (summary) summary.textContent = `Showing ${filtered.length.toLocaleString()} verified UFC fighters`;

  // Virtual slice: top 48 fighters matching to keep 60fps responsiveness
  const slice = filtered.slice(0, 48);
  container.innerHTML = slice.map(f => `
    <div class="fighter-db-card">
      <div class="db-card-header">
        <div class="db-header-left">
          ${renderFighterAvatar(f.name, 'neutral')}
          <div>
            <div class="db-card-name">${esc(f.name)}</div>
            <div class="db-card-record">${esc(f.record || 'UFC Veteran')} · ${esc(f.weight_class || 'UFC Division')}</div>
          </div>
        </div>
        <button class="fav-btn ${favs.has(f.name)?'is-fav':''}" data-fav-name="${esc(f.name)}" aria-label="Favorite ${esc(f.name)}">★</button>
      </div>

      <div class="db-physical-row">
        <div class="db-physical-item"><span>HEIGHT</span><strong>${esc(f.height || '—')}</strong></div>
        <div class="db-physical-item"><span>REACH</span><strong>${esc(f.reach || '—')}</strong></div>
        <div class="db-physical-item"><span>STANCE</span><strong>${esc(f.stance || 'Orthodox')}</strong></div>
        <div class="db-physical-item"><span>DOB</span><strong>${esc(f.dob || '—')}</strong></div>
      </div>

      <div class="db-stat-gauges">
        <div class="db-gauge-row">
          <span class="db-gauge-label">Strikes / min (SLpM)</span>
          <div class="db-gauge-track"><div class="db-gauge-fill" style="width:${Math.min(100, (parseFloat(f.SLpM)||3.5)/8*100)}%"></div></div>
          <span class="db-gauge-val">${esc(f.SLpM || '—')}</span>
        </div>
        <div class="db-gauge-row">
          <span class="db-gauge-label">Striking accuracy</span>
          <div class="db-gauge-track"><div class="db-gauge-fill" style="width:${Math.min(100, (parseFloat(f.sig_str_acc)||0.45)*100)}%"></div></div>
          <span class="db-gauge-val">${esc(f.sig_str_acc || '—')}</span>
        </div>
        <div class="db-gauge-row">
          <span class="db-gauge-label">Takedowns / 15m</span>
          <div class="db-gauge-track"><div class="db-gauge-fill" style="width:${Math.min(100, (parseFloat(f.td_avg)||1.5)/5*100)}%"></div></div>
          <span class="db-gauge-val">${esc(f.td_avg || '—')}</span>
        </div>
        <div class="db-gauge-row">
          <span class="db-gauge-label">Takedown defense</span>
          <div class="db-gauge-track"><div class="db-gauge-fill" style="width:${Math.min(100, (parseFloat(f.td_def)||0.65)*100)}%"></div></div>
          <span class="db-gauge-val">${esc(f.td_def || '—')}</span>
        </div>
      </div>

      <details class="db-core-details">
        <summary>View Core Metrics Radar</summary>
        ${renderPerformanceSnapshot(f.name, null)}
      </details>

      ${f.recent_bouts && f.recent_bouts.length ? `
        <div class="db-bouts-container">
          <div class="db-bouts-heading">RECENT BOUTS</div>
          ${f.recent_bouts.slice(0, 3).map(b => `
            <div class="db-bout-mini">
              <span>vs. ${esc(b.opponent)} (${day(b.date)})</span>
              <span class="db-bout-result ${b.result.toLowerCase()}">${esc(b.result)}</span>
            </div>
          `).join('')}
        </div>
      ` : ''}

      <div class="db-card-actions">
        <button class="db-matchup-btn" data-lab-fighter="${esc(f.name)}">Compare in Matchup Lab ↗</button>
      </div>
    </div>
  `).join('');

  // Attach favorite & lab listeners
  container.querySelectorAll('.fav-btn').forEach(b => {
    b.onclick = () => toggleFavorite(b.dataset.favName);
  });
  container.querySelectorAll('.db-matchup-btn').forEach(b => {
    b.onclick = () => {
      $('red').value = b.dataset.labFighter;
      showTab('matchup');
      $('blue').focus();
    };
  });
}

$('fighter-search')?.addEventListener('input', (e) => {
  fighterSearchQuery = e.target.value;
  renderFighterDatabase();
});
$('fighter-division')?.addEventListener('change', (e) => {
  fighterDivisionFilter = e.target.value;
  renderFighterDatabase();
});

// ==========================================
// Matchup Lab Dual Radar
// ==========================================
function renderMatchupRadar(f) {
  const radar = f.radar;
  if (!radar || !radar.red || !radar.blue) return '';

  const r = radar.red;
  const b = radar.blue;
  const labels = ['Striking Volume', 'Accuracy', 'Takedowns', 'TD Defense', 'Submission', 'Experience'];
  const rVals = [r.striking_volume, r.striking_accuracy, r.takedown_volume, r.takedown_defense, r.submission_threat, r.octagon_experience];
  const bVals = [b.striking_volume, b.striking_accuracy, b.takedown_volume, b.takedown_defense, b.submission_threat, b.octagon_experience];

  const cx = 140, cy = 130, radius = 80;
  const angles = labels.map((_, i) => (i * 2 * Math.PI) / 6 - Math.PI / 2);

  const getPt = (ang, val) => {
    const dist = (val / 100) * radius;
    return `${(cx + dist * Math.cos(ang)).toFixed(1)},${(cy + dist * Math.sin(ang)).toFixed(1)}`;
  };

  const redPoly = angles.map((ang, i) => getPt(ang, rVals[i])).join(' ');
  const bluePoly = angles.map((ang, i) => getPt(ang, bVals[i])).join(' ');

  const gridCircles = [0.25, 0.5, 0.75, 1.0].map(s => `
    <polygon points="${angles.map(ang => `${(cx + radius*s*Math.cos(ang)).toFixed(1)},${(cy + radius*s*Math.sin(ang)).toFixed(1)}`).join(' ')}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>
  `).join('');

  const axisLines = angles.map((ang, i) => {
    const x2 = cx + radius * Math.cos(ang);
    const y2 = cy + radius * Math.sin(ang);
    const lx = cx + (radius + 20) * Math.cos(ang);
    const ly = cy + (radius + 14) * Math.sin(ang);
    return `
      <line x1="${cx}" y1="${cy}" x2="${x2}" y2="${y2}" stroke="rgba(255,255,255,0.1)"/>
      <text x="${lx}" y="${ly}" fill="#9499ab" font-size="8.5" text-anchor="middle" dominant-baseline="middle">${labels[i]}</text>
    `;
  }).join('');

  return `
    <div class="radar-comparison-card">
      <div class="radar-heading">AI TACTICAL RADAR COMPARISON</div>
      <div class="radar-svg-container">
        <svg viewBox="0 0 280 260" style="width:100%;height:100%;">
          ${gridCircles}
          ${axisLines}
          <polygon points="${redPoly}" fill="rgba(255, 51, 102, 0.25)" stroke="#ff3366" stroke-width="2"/>
          <polygon points="${bluePoly}" fill="rgba(0, 240, 255, 0.25)" stroke="#00f0ff" stroke-width="2"/>
        </svg>
      </div>
      <div style="display:flex;justify-content:center;gap:20px;font-size:11px;font-weight:700;margin-top:6px;">
        <span style="color:#ff6680">■ ${esc(f.red)}</span>
        <span style="color:#00f0ff">■ ${esc(f.blue)}</span>
      </div>
    </div>
  `;
}

// ==========================================
// Shareable Prediction Card Generator (Canvas)
// ==========================================
function openShareModal(fightId) {
  if (!data?.upcoming) return;
  const f = data.upcoming.find(x => x.id === fightId);
  if (!f) return;
  shareTargetFight = f;
  const modal = $('share-modal');
  if (modal) {
    if (typeof modal.showModal === 'function') modal.showModal();
    else modal.style.display = 'block';
    drawShareCard(f);
  }
}

$('modal-close')?.addEventListener('click', () => {
  const modal = $('share-modal');
  if (modal) {
    if (typeof modal.close === 'function') modal.close();
    else modal.style.display = 'none';
  }
});

function drawShareCard(f) {
  const canvas = $('share-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = 1200;
  const h = 675;

  // Background
  const grad = ctx.createRadialGradient(w/2, h/2, 50, w/2, h/2, 700);
  grad.addColorStop(0, '#151926');
  grad.addColorStop(0.5, '#0a0d14');
  grad.addColorStop(1, '#050608');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  // Decorative border
  ctx.strokeStyle = 'rgba(0, 240, 255, 0.3)';
  ctx.lineWidth = 3;
  ctx.strokeRect(24, 24, w - 48, h - 48);

  // Header Brand
  ctx.fillStyle = '#00f0ff';
  ctx.font = 'bold 24px system-ui, sans-serif';
  ctx.fillText('MMA CIPHER · ADVANCED MULTI-MODEL AI', 50, 70);

  // Event & Date
  ctx.fillStyle = '#9499ab';
  ctx.font = '18px system-ui, sans-serif';
  ctx.fillText(`${f.event || 'UFC EVENT'} · ${f.weight_class || 'MATCHUP'} · ${day(f.date)}`, 50, 105);

  // Red Fighter
  ctx.fillStyle = '#ff6680';
  ctx.font = 'bold 38px system-ui, sans-serif';
  ctx.fillText(f.red, 50, 200);
  ctx.fillStyle = '#e2e8f0';
  ctx.font = 'bold 28px system-ui, sans-serif';
  ctx.fillText(pct(f.red_probability), 50, 240);

  // VS
  ctx.fillStyle = 'rgba(255,255,255,0.2)';
  ctx.font = 'bold 44px system-ui, sans-serif';
  ctx.fillText('VS', 560, 220);

  // Blue Fighter
  ctx.fillStyle = '#00f0ff';
  ctx.font = 'bold 38px system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.fillText(f.blue, w - 50, 200);
  ctx.fillStyle = '#e2e8f0';
  ctx.font = 'bold 28px system-ui, sans-serif';
  ctx.fillText(pct(f.blue_probability), w - 50, 240);
  ctx.textAlign = 'left';

  // Predicted Winner Box
  ctx.fillStyle = 'rgba(0, 240, 255, 0.08)';
  ctx.strokeStyle = '#00f0ff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.roundRect(50, 310, w - 100, 170, 14);
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = '#9499ab';
  ctx.font = 'bold 16px system-ui, sans-serif';
  ctx.fillText('PROJECTED WINNER', 80, 350);

  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 48px system-ui, sans-serif';
  ctx.fillText(f.pick, 80, 410);

  ctx.fillStyle = '#00f0ff';
  ctx.font = 'bold 32px system-ui, sans-serif';
  ctx.fillText(`↗ ${pct(f.confidence)} CONFIDENCE`, 80, 455);

  // Method of Victory tag
  if (f.likely_method) {
    ctx.fillStyle = 'rgba(255, 51, 102, 0.2)';
    ctx.strokeStyle = '#ff3366';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(w - 380, 350, 300, 90, 10);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#ff6680';
    ctx.font = 'bold 14px system-ui, sans-serif';
    ctx.fillText('LIKELY METHOD', w - 360, 380);
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 26px system-ui, sans-serif';
    ctx.fillText(f.likely_method, w - 360, 415);
  }

  // Footer Disclaimers
  ctx.fillStyle = '#64748b';
  ctx.font = '14px system-ui, sans-serif';
  ctx.fillText('Strict zero-leakage chronological training · Algorithmic estimates, not financial guarantees.', 50, 625);
  ctx.textAlign = 'right';
  ctx.fillText('mmacipher.web.app', w - 50, 625);
  ctx.textAlign = 'left';
}

$('copy-card-btn')?.addEventListener('click', async () => {
  const canvas = $('share-canvas');
  const status = $('share-status');
  if (!canvas) return;
  try {
    canvas.toBlob(async (blob) => {
      if (blob && navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        if (status) status.textContent = '✓ Prediction image copied to clipboard!';
        setTimeout(() => { if (status) status.textContent = ''; }, 3000);
      } else {
        if (status) status.textContent = 'Clipboard write not supported; use Download Image instead.';
      }
    });
  } catch (err) {
    if (status) status.textContent = 'Could not copy to clipboard; use Download Image.';
  }
});

$('download-card-btn')?.addEventListener('click', () => {
  const canvas = $('share-canvas');
  if (!canvas || !shareTargetFight) return;
  const link = document.createElement('a');
  link.download = `mma-cipher-${(shareTargetFight.pick||'fight').toLowerCase().replace(/[^a-z0-9]/g,'-')}.png`;
  link.href = canvas.toDataURL('image/png');
  link.click();
});

// ==========================================
// Cache & Fetch Synchronization
// ==========================================
const PULL_INTERVAL_DEFAULT_MS = 24 * 60 * 60 * 1000;
const PULL_INTERVAL_FIGHT_DAY_MS = 6 * 60 * 60 * 1000;
const CACHE_KEY = 'mma_cipher_dashboard';
const CACHE_TIME_KEY = 'mma_cipher_last_pull';
const LEGACY_CACHE_KEY = 'mma_oracle_dashboard';
const LEGACY_CACHE_TIME_KEY = 'mma_oracle_last_pull';

function getDynamicPullInterval(upcomingList) {
  if (!upcomingList || !upcomingList.length) return PULL_INTERVAL_DEFAULT_MS;
  const now = Date.now();
  const today = new Date().toISOString().slice(0, 10);
  const next = upcomingList.find(f => {
    const state = f.odds?.state;
    const start = Date.parse(f.odds?.start_at || f.start_at);
    return state !== 'post' && (Number.isFinite(start) ? start > now - 4 * 3600 * 1000 : f.date >= today);
  });
  if (!next) return PULL_INTERVAL_DEFAULT_MS;
  const isFightDay = next.date === today;
  const startMs = Date.parse(next.odds?.start_at || next.start_at);
  const hoursToStart = Number.isFinite(startMs) ? (startMs - now) / 3600000 : 999;
  if (isFightDay || (hoursToStart > 0 && hoursToStart <= 6)) {
    return PULL_INTERVAL_FIGHT_DAY_MS;
  }
  return PULL_INTERVAL_DEFAULT_MS;
}

function getCachedPredictions() {
  try {
    let raw = localStorage.getItem(CACHE_KEY);
    let time = parseInt(localStorage.getItem(CACHE_TIME_KEY), 10);
    if (!raw) {
      raw = localStorage.getItem(LEGACY_CACHE_KEY);
      time = parseInt(localStorage.getItem(LEGACY_CACHE_TIME_KEY), 10);
    }
    if (raw && Number.isFinite(time)) {
      return { data: JSON.parse(raw), timestamp: time };
    }
  } catch (_) {}
  return null;
}

function saveCachedPredictions(d) {
  try {
    const payload = JSON.stringify(d);
    const nowStr = String(Date.now());
    localStorage.setItem(CACHE_KEY, payload);
    localStorage.setItem(CACHE_TIME_KEY, nowStr);
    localStorage.setItem(LEGACY_CACHE_KEY, payload);
    localStorage.setItem(LEGACY_CACHE_TIME_KEY, nowStr);
  } catch (_) {}
}

function shouldPull() {
  const cached = getCachedPredictions();
  if (!cached) return true;
  const interval = getDynamicPullInterval(cached.data?.upcoming);
  return (Date.now() - cached.timestamp >= interval);
}

let loading = false;
let hasInitialNetworkSynced = false;

async function load(force = false) {
  if (loading) return;
  const now = Date.now();
  const cached = getCachedPredictions();

  // 1. Instant paint from cache so user experiences zero wait time
  if (!data && cached) {
    data = cached.data;
    if (data?.images) fighterImagesMap = data.images;
    initFighterImages().then(() => {});
    render();
  }

  // 2. Determine if network fetch is needed:
  // Forced, or initial page load/reload, or cache has reached dynamic expiration
  const pullInterval = getDynamicPullInterval(data?.upcoming || cached?.data?.upcoming);
  const isCacheExpired = !cached || (now - cached.timestamp >= pullInterval);
  const shouldFetchNetwork = force || !hasInitialNetworkSynced || isCacheExpired;

  if (!shouldFetchNetwork) {
    if (activeMatchup && !$('matchup').hidden) await refreshMatchup();
    return;
  }

  loading = true;
  try {
    const bust = Date.now();
    const r = await fetch(`/api/dashboard?_t=${bust}`, { cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (!r.ok) throw Error('Dashboard is unavailable');
    const freshData = await r.json();
    data = freshData;
    hasInitialNetworkSynced = true;
    if (data?.images) fighterImagesMap = data.images;
    else await initFighterImages();
    saveCachedPredictions(data);
    render();
    if (activeMatchup && !$('matchup').hidden) await refreshMatchup();
  } catch (e) {
    hasInitialNetworkSynced = true;
    if (!data && cached) {
      data = cached.data;
      if (data?.images) fighterImagesMap = data.images;
      await initFighterImages();
      render();
    }
    $('notice').classList.add('warning');
    $('notice').textContent = 'Connection interrupted. Showing saved MMA Cipher data; retrying automatically.';
  } finally {
    loading = false;
  }
}

async function refreshMatchup() {
  const pair = activeMatchup;
  if (!pair) return;
  const r = await fetch('/api/matchup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pair),
    signal: AbortSignal.timeout(20000)
  });
  const f = await r.json();
  if (!r.ok) throw Error(f.error || 'Prediction unavailable');
  if (activeMatchup !== pair) return;

  const redP = data?.fighters?.find(x => x.name.toLowerCase() === f.red.toLowerCase());
  const blueP = data?.fighters?.find(x => x.name.toLowerCase() === f.blue.toLowerCase());
  const divisionMismatch = (redP?.weight_class && blueP?.weight_class && redP.weight_class !== blueP.weight_class);

  $('matchup-output').innerHTML = `
    ${divisionMismatch ? `<div class="division-mismatch-banner">⚠ Cross-division matchup: ${esc(redP.weight_class)} vs. ${esc(blueP.weight_class)}</div>` : ''}
    ${card(f)}
    ${renderPerformanceSnapshot(f.red, f.blue)}
    <div class="comparison">${profile(f.red)}${profile(f.blue)}</div>
  `;
  attachCardListeners();
}

$('matchup-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const b = e.submitter;
  b.disabled = true;
  $('matchup-output').textContent = 'Calculating MMA Cipher ensemble prediction\u2026';
  activeMatchup = { red: $('red').value, blue: $('blue').value };
  try {
    await refreshMatchup();
  } catch (e) {
    activeMatchup = null;
    $('matchup-output').textContent = e.message;
  } finally {
    b.disabled = false;
  }
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && shouldPull()) load();
});
window.addEventListener('online', () => {
  if (shouldPull()) load();
});

load();
setInterval(() => {
  if (shouldPull()) load();
}, 60000);
setInterval(updateCountdowns, 1000);

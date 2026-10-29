'use strict';

/* 拾音 · 前端控制器
   数据来自 GET /api/state、GET /api/tracks 与 SSE GET /api/events。
   没有令牌时只显示提示，不发任何请求。
   勾选 / 搜索 / 排序都在本地这一份列表上算，SSE 只增量更新行。 */

const TOKEN_KEY = 'sonora.token';
const UI_KEY = 'sonora.ui';
const ACTIVE_STATES = ['decrypting', 'transcoding', 'tagging'];
const GROUP = {
  waiting: 'waiting', decrypting: 'active', transcoding: 'active', tagging: 'active',
  done: 'done', failed: 'failed', unsupported: 'unsupported'
};
const LABEL = {
  waiting: '等待中', decrypting: '解密中', transcoding: '转换中', tagging: '写入标签',
  done: '已转换', failed: '失败', unsupported: '不支持'
};
const FILTER_LABEL = {
  all: '全部', waiting: '等待', active: '转换中', done: '已转换', failed: '失败', unsupported: '不支持'
};
const STATE_RANK = { decrypting: 0, transcoding: 0, tagging: 0, failed: 1, waiting: 2, done: 3, unsupported: 4 };
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
const MAX_ROWS = 500;

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const el = {
  gate: $('#gate'), app: $('#app'),
  sidebar: $('#sidebar'), scrim: $('#scrim'), menu: $('#menu'), drawerclose: $('#drawerclose'),
  sources: $('#sources'), addfolder: $('#addfolder'), sourceCount: $('#sourceCount'),
  opensettings: $('#opensettings'), version: $('#version'),
  banner: $('#ffmpegBanner'),
  scanline: $('#scanline'), scanState: $('#scanState'), lastScan: $('#lastScan'),
  rescan: $('#rescan'),
  canvas: $('#canvas'), dock: $('.dock'),
  totalCount: $('#totalCount'), outPath: $('#outPath'), pct: $('#pct'), eta: $('#eta'),
  crumbs: $('#crumbs'), search: $('#search'), searchclear: $('#searchclear'),
  searchcount: $('#searchcount'), searchwrap: $('#searchwrap'),
  counts: $$('.count'), rows: $('#rows'), qhead: $('#qhead'), qfoot: $('#qfoot'),
  qfootinfo: $('#qfootinfo'), viewall: $('#viewall'), unsupportedLink: $('#unsupportedLink'),
  selectall: $('#selectall'), selectallBtn: $('#selectallBtn'),
  scanbar: $('#scanbar'), batchbar: $('#batchbar'),
  selinfo: $('#selinfo'), outDir: $('#outDir'), changedir: $('#changedir'),
  fmtMp3: $('#fmtMp3'), fmtFlac: $('#fmtFlac'), quality: $('#quality'), qualityWrap: $('#qualityWrap'),
  openfolder: $('#openfolder'), primary: $('#primary'),
  toast: $('#toast'), dropmask: $('#dropmask'),
  sheet: $('#sheet'), sheetclose: $('#sheetclose'), sheetnav: $('.sheetnav'), sheetmain: $('#sheetmain'),
  setOutDir: $('#setOutDir'), setChangedir: $('#setChangedir'),
  setFmtMp3: $('#setFmtMp3'), setFmtFlac: $('#setFmtFlac'),
  setQuality: $('#setQuality'), setQualityRow: $('#setQualityRow'),
  setTemplate: $('#setTemplate'), setTemplatePreview: $('#setTemplatePreview'),
  setConflict: $('#setConflict'),
  concSeg: $('#concSeg'), concNote: $('#concNote'),
  swRetry: $('#swRetry'), swOpen: $('#swOpen'), swAuto: $('#swAuto'), swMotion: $('#swMotion'),
  densitySeg: $('#densitySeg'),
  animSpeedSeg: $('#animSpeedSeg'), animAmpSeg: $('#animAmpSeg'), swRandom: $('#swRandom'),
  themeSeg: $('#themeSeg'), accentPick: $('#accentPick'), accentReset: $('#accentReset'),
  viewSeg: $('#viewSeg'), albumhead: $('#albumhead'), countbox: $('#counts'), countnote: $('#countnote'),
  swShowWaiting: $('#swShowWaiting'), swShowActive: $('#swShowActive'),
  selectallTop: $('#selectallTop'), refreshDir: $('#refreshDir'),
  apps: $('#apps'), phoneTag: $('#phoneTag'), phoneHint: $('#phoneHint'), devices: $('#devices')
};

const S = {
  token: '', state: null, tracks: [], total: 0, limit: MAX_ROWS,
  filter: 'all', sourceId: null, q: '',
  sort: { key: null, dir: 1 },
  selected: new Set(),
  scanning: false, busy: false, loaded: false, firstPaint: true,
  apps: null, appsError: null, appsLoading: false,
  ui: { density: 'standard', motion: true, conc: null, speed: 'normal', amp: 'md', random: false, theme: 'sand', accent: '', view: 'list', showWaiting: false, showActive: false }
};

const mq = matchMedia('(max-width: 899px)');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let drawerOpen = false;
let sheetOpen = false;
let dragDepth = 0;
let toastTimer = 0;
let sse = null;
let reconnectTimer = 0;
let scanTimer = 0;
let sheetReturnFocus = null;

/* ---------------------------------------------------------------- 基础工具 */

const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ESC_MAP[c]);
const clamp01 = (v) => Math.min(1, Math.max(0, Number(v) || 0));
const pad2 = (n) => String(n).padStart(2, '0');

function baseName(p) {
  const parts = String(p || '').split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(p || '');
}

function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (!n) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + ' GB';
  return (n / 1e6).toFixed(1) + ' MB';
}

function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const mm = Math.floor(s / 60);
  if (mm >= 60) return `${Math.floor(mm / 60)} 小时 ${mm % 60} 分`;
  if (!mm) return `${s} 秒`;
  return `${mm} 分 ${pad2(s % 60)} 秒`;
}

function fmtTrackDuration(ms) {
  const n = Number(ms) || 0;
  if (!n) return '';
  const s = Math.round(n / 1000);
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
}

function fmtClock(ts) {
  const n = Number(ts);
  if (!n) return '—';
  const d = new Date(n * 1000);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function toast(msg) {
  if (!msg) return;
  el.toast.textContent = msg;
  el.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('show'), 2800);
}

function focusables(root) {
  return $$(FOCUSABLE, root).filter((n) => n.getClientRects().length > 0);
}

/* ---------------------------------------------------------------- 本地偏好 */

function loadUI() {
  try {
    const raw = JSON.parse(localStorage.getItem(UI_KEY) || '{}');
    if (raw && typeof raw === 'object') {
      if (['compact', 'standard', 'comfy'].includes(raw.density)) S.ui.density = raw.density;
      if (typeof raw.motion === 'boolean') S.ui.motion = raw.motion;
      if (['ink', 'navy', 'forest', 'porcelain', 'sand'].includes(raw.theme)) S.ui.theme = raw.theme;
      if (typeof raw.accent === 'string' && /^#[0-9a-f]{6}/i.test(raw.accent)) S.ui.accent = raw.accent;
      if (['list', 'albums'].includes(raw.view)) S.ui.view = raw.view;
      if (typeof raw.showWaiting === 'boolean') S.ui.showWaiting = raw.showWaiting;
      if (typeof raw.showActive === 'boolean') S.ui.showActive = raw.showActive;
      if (['slow', 'normal', 'fast'].includes(raw.speed)) S.ui.speed = raw.speed;
      if (['sm', 'md', 'lg'].includes(raw.amp)) S.ui.amp = raw.amp;
      if (typeof raw.random === 'boolean') S.ui.random = raw.random;
      if (Number.isFinite(raw.conc)) S.ui.conc = Math.min(6, Math.max(1, Math.round(raw.conc)));
    }
  } catch (e) { /* 隐私模式忽略 */ }
}

let uiPatchTimer = 0;

function saveUI() {
  S.uiDirty = Date.now();
  try {
    localStorage.setItem(UI_KEY, JSON.stringify({
      density: S.ui.density, motion: S.ui.motion, conc: S.ui.conc,
      speed: S.ui.speed, amp: S.ui.amp, random: S.ui.random, theme: S.ui.theme, accent: S.ui.accent, view: S.ui.view, showWaiting: S.ui.showWaiting, showActive: S.ui.showActive
    }));
  } catch (e) { /* 忽略 */ }
  clearTimeout(uiPatchTimer);
  uiPatchTimer = setTimeout(() => {
    api('/api/settings', { method: 'PATCH', body: { ui: { ...S.ui } } }).catch(() => {});
  }, 250);
}

function hasLocalUI() {
  try {
    const raw = JSON.parse(localStorage.getItem(UI_KEY) || 'null');
    return !!(raw && typeof raw === 'object');
  } catch (e) { return false; }
}

function applyServerUI() {
  const u = S.state && S.state.ui;
  if (!u || typeof u !== 'object') return;
  if (S.uiDirty && Date.now() - S.uiDirty < 1500) return;
  if (!S.uiSynced) {
    S.uiSynced = true;
    if (hasLocalUI()) { saveUI(); return; }
  }
  if (['ink', 'navy', 'forest', 'porcelain', 'sand'].includes(u.theme)) S.ui.theme = u.theme;
  if (['compact', 'standard', 'comfy'].includes(u.density)) S.ui.density = u.density;
  if (typeof u.motion === 'boolean') S.ui.motion = u.motion;
  if (['slow', 'normal', 'fast'].includes(u.speed)) S.ui.speed = u.speed;
  if (['list', 'albums'].includes(u.view)) S.ui.view = u.view;
  if (typeof u.showWaiting === 'boolean') S.ui.showWaiting = u.showWaiting;
  if (typeof u.showActive === 'boolean') S.ui.showActive = u.showActive;
  if (['sm', 'md', 'lg'].includes(u.amp)) S.ui.amp = u.amp;
  if (typeof u.random === 'boolean') S.ui.random = u.random;
  if (typeof u.accent === 'string' && (!u.accent || /^#[0-9a-f]{6}$/i.test(u.accent))) S.ui.accent = u.accent;
  applyUI();
}

function motionOn() { return S.ui.motion && !reduced.matches; }

function applyUI() {
  document.documentElement.dataset.density = S.ui.density;
  document.documentElement.dataset.motion = motionOn() ? 'on' : 'off';
  document.documentElement.dataset.animSpeed = S.ui.speed;
  document.documentElement.dataset.animAmp = S.ui.amp;
  document.documentElement.dataset.animRandom = S.ui.random ? '1' : '0';
  document.documentElement.dataset.theme = S.ui.theme;
  document.documentElement.dataset.view = S.ui.view || 'list';
  const root = document.documentElement;
  if (S.ui.accent) {
    root.style.setProperty('--accent', S.ui.accent);
    root.style.setProperty('--accent-soft', `color-mix(in srgb, ${S.ui.accent} 14%, transparent)`);
  } else {
    root.style.removeProperty('--accent');
    root.style.removeProperty('--accent-soft');
  }
}

/* ---------------------------------------------------------------- 令牌与请求 */

function takeToken() {
  let token = '';
  try { token = sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { token = ''; }
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get('t');
  if (fromUrl) {
    token = fromUrl;
    try { sessionStorage.setItem(TOKEN_KEY, fromUrl); } catch (e) { /* 隐私模式忽略 */ }
    params.delete('t');
    const q = params.toString();
    try { history.replaceState(null, '', location.pathname + (q ? '?' + q : '') + location.hash); } catch (e) { /* 忽略 */ }
  }
  return token;
}

function showGate() {
  el.app.hidden = true;
  el.gate.hidden = false;
  try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* 忽略 */ }
}

function apiError(code, message) {
  const err = new Error(message || '请求失败');
  err.code = code || 'BAD_REQUEST';
  return err;
}

async function api(path, opts) {
  const o = opts || {};
  const headers = { 'X-Sonora-Token': S.token };
  let payload;
  if (o.file) {
    payload = o.file;
    headers['Content-Type'] = o.file.type || 'application/octet-stream';
  } else if (o.body !== undefined) {
    payload = JSON.stringify(o.body);
    headers['Content-Type'] = 'application/json';
  }
  let res;
  try {
    res = await fetch(path, { method: o.method || 'GET', headers, body: payload, cache: 'no-store' });
  } catch (e) {
    throw apiError('NETWORK', '连不上本地服务，请确认 start.bat 还在运行');
  }
  let json = null;
  try { json = await res.json(); } catch (e) { json = null; }
  if (!res.ok || !json || json.ok === false) {
    const code = (json && json.error && json.error.code) || (res.status === 403 ? 'FORBIDDEN' : 'HTTP_' + res.status);
    const message = (json && json.error && json.error.message) || `请求失败（${res.status}）`;
    throw apiError(code, message);
  }
  return json.data;
}

function nudgePrimary() {
  if (!el.primary) return;
  el.primary.classList.remove('nudge');
  void el.primary.offsetWidth;
  el.primary.classList.add('nudge');
  setTimeout(() => el.primary.classList.remove('nudge'), 650);
}

function fail(err) {
  if (err && (err.code === 'FORBIDDEN' || err.code === 'NO_TOKEN')) { showGate(); return; }
  toast((err && err.message) || '请求失败');
  if (err) console.warn('[sonora]', err.code || '', err.message);
}

/* ---------------------------------------------------------------- 数据读取 */

function tracksURL() {
  const p = new URLSearchParams();
  p.set('state', 'all');
  if (S.sourceId) p.set('source', S.sourceId);
  p.set('limit', String(S.limit));
  p.set('offset', '0');
  return '/api/tracks?' + p.toString();
}

function applyPage(page) {
  S.tracks = (page && page.items) || [];
  S.total = page && typeof page.total === 'number' ? page.total : S.tracks.length;
  S.loaded = true;
}

async function refresh() {
  try {
    const [state, page] = await Promise.all([api('/api/state'), api(tracksURL())]);
    S.state = state;
    applyServerUI();
    applyPage(page);
    render();
  } catch (err) { fail(err); }
}

async function loadTracks() {
  try {
    applyPage(await api(tracksURL()));
    renderRows();
    renderCounts();
    renderFooter();
    renderSel();
  } catch (err) { fail(err); }
}

function applyState(state) {
  if (!state || !Array.isArray(state.sources)) return false;
  S.state = state;
  applyServerUI();
  S.loaded = true;
  render();
  return true;
}

async function patchSettings(patch, pane) {
  const data = await api('/api/settings', { method: 'PATCH', body: patch });
  if (applyState(data)) { if (pane) stamp(pane, '已保存'); }
  else await refresh();
  return data;
}

/* ---------------------------------------------------------------- 派生数据 */

function totalsOf() {
  const t = (S.state && S.state.totals) || {};
  return {
    all: t.all || 0,
    waiting: t.waiting || 0,
    active: (t.decrypting || 0) + (t.transcoding || 0) + (t.tagging || 0),
    done: t.done || 0,
    failed: t.failed || 0,
    unsupported: t.unsupported || 0
  };
}

function viewTotals() {
  const n = { all: S.total, waiting: 0, active: 0, done: 0, failed: 0, unsupported: 0 };
  S.tracks.forEach((t) => {
    const g = GROUP[t.state] || 'waiting';
    if (n[g] != null) n[g] += 1;
  });
  return n;
}

function sourceName(id) {
  const list = (S.state && S.state.sources) || [];
  const hit = list.find((s) => s.id === id);
  return hit ? baseName(hit.path) : '';
}

function isScanning() {
  if (S.scanning) return true;
  return ((S.state && S.state.sources) || []).some((s) => s.scanning);
}

function matchesFilter(t) {
  if (S.filter === 'all') return true;
  if (S.filter === 'active') return ACTIVE_STATES.indexOf(t.state) !== -1;
  return t.state === S.filter;
}

function trackTitle(t) { return t.title || baseName(t.name) || '未命名'; }

function matchesQuery(t) {
  if (!S.q) return true;
  const q = S.q.toLowerCase();
  return [trackTitle(t), t.artist, t.album, t.name].some((v) => String(v || '').toLowerCase().includes(q));
}

function cmpDefault(a, b) {
  const ra = STATE_RANK[a.state] ?? 9;
  const rb = STATE_RANK[b.state] ?? 9;
  if (ra !== rb) return ra - rb;
  return String(trackTitle(a)).localeCompare(String(trackTitle(b)), 'zh-Hans-CN');
}

function sortValue(t, key) {
  if (key === 'title') return trackTitle(t);
  if (key === 'artist') return t.artist || '';
  return t.album || '';
}

function albumKey(t) {
  return (t.album || '未知专辑').trim() || '未知专辑';
}

function visibleTracks() {
  const list = S.tracks.filter((t) => matchesFilter(t) && matchesQuery(t)
    && (!S.album || albumKey(t) === S.album));
  if (S.album && !S.sort.key) {
    return list.slice().sort((a, b) => (a.trackNo || 9999) - (b.trackNo || 9999) || cmpDefault(a, b));
  }
  const key = S.sort.key;
  if (!key) return list.slice().sort(cmpDefault);
  const dir = S.sort.dir;
  return list.slice().sort((a, b) => {
    const va = String(sortValue(a, key) || '');
    const vb = String(sortValue(b, key) || '');
    if (!va && vb) return 1;
    if (va && !vb) return -1;
    const c = va.localeCompare(vb, 'zh-Hans-CN');
    if (c !== 0) return c * dir;
    return cmpDefault(a, b);
  });
}

/* ---------------------------------------------------------------- 渲染 */

function render() {
  if (!S.state) return;
  renderBanner();
  renderTopbar();
  renderSources();
  renderCounts();
  renderReadout();
  renderDock();
  renderCrumbs();
  renderSearchCount();
  renderRows();
  renderFooter();
  renderSel();
  renderSheet();
  renderPrimary();
}

function renderBanner() {
  const ff = S.state && S.state.ffmpeg;
  el.banner.hidden = !(ff && ff.ok === false);
}

function renderTopbar() {
  const sources = (S.state && S.state.sources) || [];
  const scanning = isScanning();
  const paused = !!(S.state && S.state.batch && S.state.batch.paused);
  el.scanState.textContent = scanning ? '扫描中…' : (paused ? '已暂停' : '监控中');
  el.sourceCount.textContent = `${sources.length} 个来源文件夹`;
  el.lastScan.textContent = fmtClock(S.state && S.state.lastScanAt);
  if (el.version) el.version.textContent = `v${(S.state && S.state.version) || '0.4.0'} · 全部本地处理`;
  el.scanline.dataset.scanning = String(scanning);
  el.scanbar.hidden = !scanning;
  if (el.refreshDir) {
    el.refreshDir.classList.toggle('spin', scanning);
    el.refreshDir.disabled = scanning;
  }
}

function renderSources() {
  const sources = (S.state && S.state.sources) || [];
  if (!sources.length) { el.sources.innerHTML = ''; return; }
  const sum = sources.reduce((a, s) => a + (Number(s.count) || 0), 0);
  const all = `<div class="srcrow">
      <button class="folder" type="button" data-src="" aria-pressed="${S.sourceId ? 'false' : 'true'}">
        <span class="fname">全部来源</span><em class="num">${sum}</em>
      </button>
    </div>`;
  el.sources.innerHTML = all + sources.map((s) => {
    const name = baseName(s.path);
    const pressed = S.sourceId === s.id;
    const count = s.scanning ? '扫描中' : String(s.count || 0);
    return `<div class="srcrow" data-id="${esc(s.id)}">
      <button class="folder" type="button" data-src="${esc(s.id)}" aria-pressed="${pressed}" title="${esc(s.path)}">
        <span class="fname">${esc(name)}</span><em class="num">${esc(count)}</em>
      </button>
      <button class="srcrefresh" type="button" data-refresh="${esc(s.id)}" aria-label="刷新目录 ${esc(name)}" title="刷新这个目录"><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M13.4 6.6A5.5 5.5 0 1 0 13.5 9"/><path d="M13.6 2.8v3.9H9.7"/></svg></button>
      <button class="srcrm" type="button" data-remove="${esc(s.id)}" aria-label="移除来源 ${esc(name)}">移除</button>
    </div>`;
  }).join('');
}

function setNum(node, value) {
  if (!node) return;
  const target = Math.max(0, Math.round(Number(value) || 0));
  const prev = Number(node.dataset.v);
  node.dataset.v = String(target);
  if (!Number.isFinite(prev) || prev === target || !motionOn()) { node.textContent = String(target); return; }
  if (node._raf) cancelAnimationFrame(node._raf);
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / 320);
    const e = 1 - Math.pow(1 - k, 3);
    node.textContent = String(Math.round(prev + (target - prev) * e));
    if (k < 1) node._raf = requestAnimationFrame(step);
    else { node._raf = 0; node.textContent = String(target); }
  };
  node._raf = requestAnimationFrame(step);
}

function albumStats() {
  const map = new Map();
  for (const t of S.tracks) {
    const k = albumKey(t);
    let g = map.get(k);
    if (!g) { g = new Set(); map.set(k, g); }
    g.add(GROUP[t.state] || 'waiting');
  }
  let done = 0, failed = 0, waiting = 0, active = 0;
  for (const states of map.values()) {
    if (states.size === 1 && states.has('done')) done++;
    if (states.has('failed')) failed++;
    if (states.has('waiting')) waiting++;
    if (states.has('active')) active++;
  }
  return { all: map.size, done, failed, waiting, active };
}

function applyCountsVisibility() {
  if (!el.countbox) return;
  const showWaiting = !!S.ui.showWaiting;
  const showActive = !!S.ui.showActive;
  const w = $('[data-opt="waiting"]', el.countbox);
  const a = $('[data-opt="active"]', el.countbox);
  if (w) w.hidden = !showWaiting;
  if (a) a.hidden = !showActive;
  if ((S.filter === 'waiting' && !showWaiting) || (S.filter === 'active' && !showActive)) S.filter = 'all';
  if (el.swShowWaiting) el.swShowWaiting.setAttribute('aria-checked', String(showWaiting));
  if (el.swShowActive) el.swShowActive.setAttribute('aria-checked', String(showActive));
}

function renderCounts() {
  const n = S.ui.view === 'albums' ? albumStats() : viewTotals();
  const map = { all: n.all, waiting: n.waiting, active: n.active, done: n.done, failed: n.failed };
  el.counts.forEach((btn) => {
    const key = btn.dataset.filter;
    const v = $('.v', btn);
    if (v) setNum(v, map[key] || 0);
    btn.setAttribute('aria-pressed', String(S.filter === key));
  });
  applyCountsVisibility();
  if (el.countnote) {
    if (S.ui.view === 'albums') {
      el.countnote.textContent = `共 ${n.all} 张专辑 · ${n.done} 张已完成`;
    } else {
      const total = (S.state && S.state.totals && S.state.totals.all) || n.all;
      el.countnote.textContent = `共 ${total} 首 · ${n.done} 首已转换`;
    }
  }
}

function setPct(node, value) {
  if (!node) return;
  const target = Math.min(100, Math.max(0, Math.round(Number(value) || 0)));
  const prev = Number(node.dataset.v);
  node.dataset.v = String(target);
  if (!Number.isFinite(prev) || prev === target || !motionOn()) { node.textContent = target + '%'; return; }
  node.classList.remove('tick');
  void node.offsetWidth;
  node.classList.add('tick');
  setTimeout(() => node.classList.remove('tick'), 520);
  if (node._raf) cancelAnimationFrame(node._raf);
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / 340);
    const e = 1 - Math.pow(1 - k, 3);
    node.textContent = Math.round(prev + (target - prev) * e) + '%';
    if (k < 1) node._raf = requestAnimationFrame(step);
    else { node._raf = 0; node.textContent = target + '%'; }
  };
  node._raf = requestAnimationFrame(step);
}

function renderReadout() {
  const n = totalsOf();
  const b = (S.state && S.state.batch) || {};
  const pct = Math.min(100, Math.max(0, Math.round(Number(b.percent) || 0)));
  setNum(el.totalCount, n.all);
  setPct(el.pct, pct);
  el.batchbar.setAttribute('aria-valuenow', String(pct));
  const fill = $('i', el.batchbar);
  if (fill) fill.style.width = pct + '%';
  let eta = '';
  if (!n.all) eta = '';
  else if (b.running && b.paused) eta = '已暂停';
  else if (b.running) eta = b.etaSec > 0 ? `预计 ${fmtDuration(b.etaSec)}后完成` : '正在估算剩余时间';
  else if (S.state.ffmpeg && S.state.ffmpeg.ok === false) eta = '缺少 ffmpeg，无法转换';
  else if (n.waiting + n.active === 0) eta = (n.failed + n.unsupported) ? `${n.failed + n.unsupported} 首未能转换` : '全部完成';
  else eta = '准备就绪，点开始转换';
  el.eta.textContent = eta;
}

function selectionCount() { return S.selected.size; }

function primaryPlan() {
  const n = totalsOf();
  const b = (S.state && S.state.batch) || {};
  const sel = selectionCount();
  let label = '开始转换', act = 'convert', disabled = false;
  if (b.running && b.paused) { label = '继续转换'; act = 'resume'; }
  else if (b.running) { label = '暂停转换'; act = 'pause'; }
  else if (sel > 0) { label = `转换所选 ${sel} 首`; act = 'convert-selected'; }
  else if (n.waiting > 0) { label = '开始转换'; act = 'convert'; }
  else if (n.all > 0 && n.waiting + n.active === 0) { label = '打开输出文件夹'; act = 'open'; }
  else { label = '开始转换'; act = 'none'; disabled = true; }
  if ((act === 'convert' || act === 'convert-selected') && S.state && S.state.ffmpeg && S.state.ffmpeg.ok === false) disabled = true;
  return { label, act, disabled };
}

function renderPrimary() {
  const plan = primaryPlan();
  if (el.primary.textContent !== plan.label) el.primary.textContent = plan.label;
  el.primary.dataset.act = plan.act;
  el.primary.setAttribute('aria-disabled', String(plan.disabled));
  el.openfolder.hidden = plan.act === 'open';
}

function renderSel() {
  const sel = selectionCount();
  const n = totalsOf();
  if (sel > 0) {
    el.selinfo.innerHTML = `已选 <b>${sel}</b> 首<button class="clear" type="button" data-act="clearsel">清除</button>`;
  } else if (n.waiting > 0) {
    el.selinfo.innerHTML = `未选择 · 将转换全部等待项 <b>${n.waiting}</b> 首`;
  } else if (n.all > 0) {
    el.selinfo.textContent = '未选择 · 没有等待转换的文件';
  } else {
    el.selinfo.textContent = '未选择';
  }
  const list = visibleTracks();
  const visible = new Set(list.map((t) => t.id));
  const chosen = list.filter((t) => S.selected.has(t.id)).length;
  if (el.selectall) {
    el.selectall.checked = list.length > 0 && chosen === list.length;
    el.selectall.indeterminate = chosen > 0 && chosen < list.length;
    el.selectall.disabled = list.length === 0;
  }
  el.selectallBtn.textContent = el.selectall?.checked ? '取消全选' : '全选当前';
  el.selectallBtn.disabled = list.length === 0;
  if (el.selectall) el.selectall.disabled = list.length === 0;
  if (el.selectallTop) {
    el.selectallTop.textContent = el.selectall?.checked ? '取消全选' : '全选';
    el.selectallTop.disabled = list.length === 0;
  }
  renderPrimary();
  return visible;
}

function renderDock() {
  const out = (S.state && S.state.output) || {};
  const dir = out.dir || '未设置';
  el.outDir.textContent = dir;
  el.outPath.textContent = dir;
  const fmt = out.format === 'flac' ? 'flac' : 'mp3';
  el.fmtMp3.setAttribute('aria-pressed', String(fmt === 'mp3'));
  el.fmtFlac.setAttribute('aria-pressed', String(fmt === 'flac'));
  if (document.activeElement !== el.quality && out.quality) el.quality.value = out.quality;
  el.qualityWrap.hidden = fmt !== 'mp3';
  el.version.textContent = `v${(S.state && S.state.version) || '0.1'} · 全部本地处理`;
}

function renderCrumbs() {
  const bits = [];
  if (S.sourceId && sourceName(S.sourceId)) {
    bits.push(`<span class="crumb"><span>来源</span><b>${esc(sourceName(S.sourceId))}</b>` +
      `<button class="crumbclick" type="button" data-clear="source" aria-label="取消来源筛选">✕</button></span>`);
  }
  if (S.filter !== 'all') {
    bits.push(`<span class="crumb"><span>状态</span><b>${esc(FILTER_LABEL[S.filter])}</b>` +
      `<button class="crumbclick" type="button" data-clear="filter" aria-label="取消状态筛选">✕</button></span>`);
  }
  if (bits.length) {
    el.crumbs.hidden = false;
    el.crumbs.innerHTML = `<button class="crumbclick" type="button" data-clear="all">全部来源</button>` +
      '<span class="sep" aria-hidden="true">›</span>' + bits.join('<span class="sep" aria-hidden="true">·</span>');
  } else {
    el.crumbs.hidden = true;
    el.crumbs.innerHTML = '';
  }
}

function renderSearchCount() {
  const n = visibleTracks().length;
  if (!S.q) { el.searchcount.hidden = true; el.searchcount.textContent = ''; return; }
  el.searchcount.hidden = false;
  el.searchcount.textContent = `找到 ${n} 首`;
}

function renderFooter() {
  if (!S.loaded) { el.qfootinfo.textContent = '正在读取…'; return; }
  const list = visibleTracks();
  const bits = [];
  if (S.filter !== 'all') bits.push('状态 ' + FILTER_LABEL[S.filter]);
  if (S.sourceId) bits.push('来源 ' + sourceName(S.sourceId));
  if (S.q) bits.push(`搜索「${S.q}」`);
  bits.push(`显示 ${list.length} 首`);
  if (S.total > S.tracks.length) bits.push(`共 ${S.total} 首，已载入 ${S.tracks.length} 首`);
  el.qfootinfo.textContent = bits.join(' · ');
  el.viewall.hidden = !(S.total > S.tracks.length);
  el.viewall.textContent = `载入全部 ${S.total} 首`;
  const unsupported = viewTotals().unsupported;
  el.unsupportedLink.hidden = !(unsupported > 0 && S.filter !== 'unsupported');
  el.unsupportedLink.textContent = `${unsupported} 首不支持`;
  el.unsupportedLink.setAttribute('aria-pressed', String(S.filter === 'unsupported'));
}

function dropzoneHTML() {
  return `<div class="dropzone">
      <div class="dzi" aria-hidden="true"><div class="mark"><i></i><i></i><i></i></div></div>
      <p>把音乐放进来</p>
      <span class="dzsub">拖入文件，或直接选一个文件夹；解密与转码都在这台电脑上完成</span>
      <div class="dzchips"><span class="dzchip">FLAC</span><span class="dzchip">MP3</span><span class="dzchip">M4A</span><span class="dzchip">OGG</span><span class="dzchip">WAV</span></div>
      <button class="btn" type="button" data-action="add-folder">添加文件夹</button>
    </div>`;
}

function captureRects() {
  const map = new Map();
  $$('.row', el.rows).forEach((r) => map.set(r.dataset.id, r.getBoundingClientRect().top));
  return map;
}

function animateFlip(prev) {
  if (!motionOn() || !prev.size) return;
  $$('.row', el.rows).forEach((r) => {
    const before = prev.get(r.dataset.id);
    if (before == null) return;
    const dy = before - r.getBoundingClientRect().top;
    if (Math.abs(dy) < 2) return;
    if (r.animate) r.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }],
      { duration: 230, easing: 'cubic-bezier(.2,.7,.25,1)' });
  });
}

function emptyText() {
  if (S.q) return `没有匹配「${S.q}」的歌。`;
  if (S.filter !== 'all') return `这个筛选下没有文件。`;
  return isScanning() ? '正在扫描来源文件夹…' : '还没有文件，添加文件夹或拖进来。';
}

function renderAlbumHead() {
  if (!el.albumhead) return;
  if (!S.album) { el.albumhead.hidden = true; el.albumhead.innerHTML = ''; return; }
  const tracks = S.tracks.filter((t) => albumKey(t) === S.album);
  if (!tracks.length) { el.albumhead.hidden = true; return; }
  const first = tracks.find((t) => t.hasCover);
  const cover = first
    ? `<img src="/api/cover/${encodeURIComponent(first.id)}?t=${encodeURIComponent(S.token)}&v=2" alt="" loading="lazy" decoding="async">`
    : `<span class="aletter">${esc((tracks[0].album || '?').slice(0, 1))}</span>`;
  const allSel = tracks.every((t) => S.selected.has(t.id));
  el.albumhead.innerHTML = `<button class="albumback" type="button" data-album-back>‹ 全部专辑</button>
    <div class="albumcover">${cover}</div>
    <div class="albuminfo"><b>${esc(tracks[0].album || '未知专辑')}</b><span>${esc([...new Set(tracks.flatMap((t) => String(t.artist || '').split('/').map((x) => x.trim()).filter(Boolean)))].slice(0, 3).join(' / ') || '未知歌手')} · ${tracks.length} 首</span></div>
    <button class="btn albumselect" type="button" data-album-select="${esc(S.album)}">${allSel ? '取消选中整张' : '选中整张'}</button>`;
  el.albumhead.hidden = false;
}

function albumLayout(n) {
  const pad = 36;
  const gap = 16;
  const metaH = 46;
  const W = Math.max(200, el.rows.clientWidth - pad);
  const H = Math.max(200, el.rows.clientHeight - pad);
  let best = { cols: 1, size: 140 };
  for (let cols = 1; cols <= Math.max(1, n); cols++) {
    const rows = Math.ceil(n / cols);
    const size = Math.min((W - gap * (cols - 1)) / cols,
                          (H - gap * (rows - 1) - rows * metaH) / rows);
    if (size > best.size) best = { cols, size };
  }
  const size = Math.max(132, Math.min(Math.round(best.size), 320));
  return { cols: Math.min(best.cols, Math.max(1, n)), size };
}

function renderAlbums() {
  const list = visibleTracks();
  const groups = new Map();
  for (const t of list) {
    const key = albumKey(t);
    let g = groups.get(key);
    if (!g) {
      g = { key, album: t.album || '未知专辑', artist: t.artist || '', tracks: [], cover: null };
      groups.set(key, g);
    }
    g.tracks.push(t);
    if (!g.cover && t.hasCover) g.cover = t.id;
  }
  const albums = [...groups.values()].sort((a, b) => a.album.localeCompare(b.album, 'zh-Hans-CN'));
  el.qhead.hidden = true;
  el.qfoot.hidden = false;
  if (el.albumhead) { el.albumhead.hidden = true; el.albumhead.innerHTML = ''; }
  el.rows.dataset.view = 'albums';
  renderFooter();
  const lay = albumLayout(albums.length);
  el.rows.style.setProperty('--acols', String(lay.cols));
  el.rows.style.setProperty('--atile', `${lay.size}px`);
  el.rows.innerHTML = albums.map((g) => {
    const picked = g.tracks.filter((t) => S.selected.has(t.id)).length;
    const selected = picked === g.tracks.length && picked > 0;
    const cover = g.cover
      ? `<img src="/api/cover/${encodeURIComponent(g.cover)}?t=${encodeURIComponent(S.token)}&v=2" alt="" loading="lazy" decoding="async">`
      : `<span class="aletter">${esc((g.album || '?').slice(0, 1))}</span>`;
    const artists = [...new Set(g.tracks.flatMap((t) => String(t.artist || '').split('/').map((x) => x.trim()).filter(Boolean)))];
    const artistLabel = artists.length > 1 ? artists.slice(0, 2).join(' / ') : (artists[0] || '未知歌手');
    return `<div class="album" data-album="${esc(encodeURIComponent(g.key))}" data-selected="${selected}" data-partial="${picked > 0 && !selected}">
      <button class="albumart" type="button" data-album-open="${esc(encodeURIComponent(g.key))}" aria-label="打开专辑 ${esc(g.album)}">${cover}</button>
      <button class="apick" type="button" data-album-pick="${esc(encodeURIComponent(g.key))}" aria-label="${selected ? '取消选中' : '选中'}整张专辑">✓</button>
      <div class="ameta"><b class="atitle" title="${esc(g.album)}">${esc(g.album)}</b><span>${esc(artistLabel)} · ${g.tracks.length} 首</span></div>
    </div>`;
  }).join('');
}

function setView(view) {
  S.ui.view = view === 'albums' ? 'albums' : 'list';
  S.album = null;
  saveUI();
  applyUI();
  renderRows();
  renderCounts();
  renderFooter();
  renderSel();
}

function albumFromEvent(el2) {
  const raw = el2 && el2.dataset ? (el2.dataset.albumOpen || el2.dataset.albumPick) : null;
  if (!raw) return null;
  return decodeURIComponent(raw);
}

function toggleAlbum(key) {
  const tracks = S.tracks.filter((t) => albumKey(t) === key);
  if (!tracks.length) return;
  const all = tracks.every((t) => S.selected.has(t.id));
  for (const t of tracks) {
    if (all) S.selected.delete(t.id);
    else S.selected.add(t.id);
  }
  renderRows();
  renderSel();
}

function renderRows() {
  if (!S.loaded) return;
  const sources = (S.state && S.state.sources) || [];
  if (!sources.length) {
    el.qhead.hidden = true;
    el.qfoot.hidden = true;
    el.rows.innerHTML = dropzoneHTML();
    S.selected.clear();
    return;
  }
  if (S.ui.view === 'albums' && !S.album) { renderAlbums(); return; }
  el.qhead.hidden = !!S.album;
  el.qfoot.hidden = false;
  el.rows.dataset.view = 'list';
  renderAlbumHead();
  const list = visibleTracks();
  const ids = new Set(list.map((t) => t.id));
  let pruned = false;
  for (const id of Array.from(S.selected)) {
    if (!ids.has(id)) { S.selected.delete(id); pruned = true; }
  }
  const prev = S.flipNext ? captureRects() : null;
  S.flipNext = false;
  const fresh = S.firstPaint;
  if (!list.length) {
    el.rows.innerHTML = `<div class="emptyrow">${esc(emptyText())}</div>`;
  } else {
    el.rows.innerHTML = list.map((t, i) => rowHTML(t, i)).join('');
    if (fresh) {
      el.rows.classList.add('rows-fresh');
      setTimeout(() => el.rows.classList.remove('rows-fresh'), 760);
    }
  }
  if (fresh) S.firstPaint = false;
  if (prev) animateFlip(prev);
  if (pruned) renderSel();
  renderFooter();
}

/* ---------------------------------------------------------------- 行 */

function waveSVG(peaks, group, progress) {
  const n = Math.min(100, peaks.length);
  const step = 100 / n;
  let bars = '';
  for (let i = 0; i < n; i++) {
    const v = Math.max(0, Math.min(100, Number(peaks[i]) || 0));
    const h = 2 + (v / 100) * 18;
    const y = (22 - h) / 2;
    let fill = '#2b2b2b';
    if (group === 'done') fill = '#5f5f5f';
    else if (group === 'active' && (i + 0.5) * step / 100 <= progress) fill = '#e8a33d';
    bars += `<rect x="${(i * step + step * 0.17).toFixed(3)}" y="${y.toFixed(2)}" width="${(step * 0.66).toFixed(3)}" height="${h.toFixed(2)}" rx="0.3" fill="${fill}"/>`;
  }
  return `<svg class="wave" viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>`;
}

function progressInfo(t) {
  const group = GROUP[t.state] || 'waiting';
  const progress = clamp01(t.progress);
  const pctNum = t.state === 'done' ? 100 : Math.round(progress * 100);
  let ariaText = pctNum + '%';
  if (group === 'waiting') ariaText = '等待中';
  else if (group === 'failed') ariaText = '转换失败';
  else if (group === 'unsupported') ariaText = '不支持';
  else if (t.state === 'done') ariaText = '已完成';
  return { group, progress, pctNum, ariaText };
}

function progHTML(t, title) {
  const info = progressInfo(t);
  const peaks = Array.isArray(t.peaks) && t.peaks.length ? t.peaks : null;
  const pctText = (info.group === 'waiting' || info.group === 'unsupported') ? '—'
    : (info.group === 'failed' && !info.pctNum) ? '—' : info.pctNum + '%';
  const inner = peaks
    ? waveSVG(peaks, info.group, info.progress)
    : `<span class="track" aria-hidden="true"><i style="width:${info.pctNum}%"></i></span>`;
  return `<div class="prog" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${info.pctNum}" aria-valuetext="${esc(info.ariaText)}" aria-label="${esc(title)} 的转换进度">${inner}<span class="pct num">${pctText}</span></div>`;
}

function statusHTML(t) {
  const group = GROUP[t.state] || 'waiting';
  if (group === 'failed') {
    return `<span class="st st-fail">失败</span><button class="retry" type="button" data-retry="${esc(t.id)}">重试</button>`;
  }
  if (group === 'active') {
    return `<span class="st st-run">${LABEL[t.state] || '转换中'}</span><button class="mini" type="button" data-cancel="${esc(t.id)}">取消</button>`;
  }
  return `<span class="st">${LABEL[t.state] || '等待中'}</span>`;
}

function subText(t) {
  return [String(t.fmt || '').toUpperCase(), fmtTrackDuration(t.durationMs), t.error].filter(Boolean).join(' · ');
}

function meta2HTML(t) {
  const main = [t.artist, t.album].filter(Boolean).join(' · ');
  const size = fmtSize(t.size);
  const parts = [];
  if (main) parts.push(`<span class="m-main">${esc(main)}</span>`);
  if (!main) parts.push(`<span class="m-size">${esc(size)}</span>`);
  else if (size && size !== '—') parts.push(`<span class="m-size"> · ${esc(size)}</span>`);
  return parts.join('') || '—';
}

function titleHTML(t, title) {
  if (t.state !== 'done' || !t.outputPath) return esc(title);
  const name = String(t.outputPath).split(/[\\/]/).pop() || 'audio';
  const mime = name.toLowerCase().endsWith('.flac') ? 'audio/flac' : 'audio/mpeg';
  const href = `/api/file/${encodeURIComponent(t.id)}?t=${encodeURIComponent(S.token)}`;
  return `<a class="filelink" href="${esc(href)}" download="${esc(name)}" draggable="true" data-mime="${mime}" data-filename="${esc(name)}" title="拖到剪映等剪辑软件，或点击下载">${esc(title)}</a>`;
}

function rowHTML(t, i) {
  const title = trackTitle(t);
  const fmtCode = String(t.fmt || '').toUpperCase().slice(0, 4) || '—';
  const coverUrl = t.hasCover
    ? `/api/cover/${encodeURIComponent(t.id)}?t=${encodeURIComponent(S.token)}&v=2`
    : '';
  const tile = `<span class="tile${coverUrl ? ' has-cover' : ''}" aria-hidden="true"><span class="fmt">${esc(fmtCode)}</span>${coverUrl ? `<img src="${esc(coverUrl)}" alt="" loading="lazy" onerror="this.remove();this.parentElement.classList.remove('has-cover')">` : ''}</span>`;
  const platform = t.platform || sourceName(t.sourceId) || '—';
  const checked = S.selected.has(t.id);
  const sub = subText(t);
  return `<div class="row" data-id="${esc(t.id)}" data-state="${GROUP[t.state] || 'waiting'}" data-selected="${checked}" style="--i:${S.ui.random ? Math.floor(Math.random() * 10) : Math.min(i, 14)}">
    <div class="cell-name idcell">${tile}
      <div class="idtext"><div class="ttl">${S.album ? `<span class="idx num">${String(i + 1).padStart(2, '0')}</span>` : ''}${titleHTML(t, title)}</div><div class="sub" title="${esc(t.error || '')}">${esc(sub)}</div>
      <div class="sub meta2">${meta2HTML(t)}</div></div>
    </div>
    <div class="cell-artist" title="${esc(t.artist || '')}">${esc(t.artist || '—')}</div>
    <div class="cell-album" title="${esc(t.album || '')}">${esc(t.album || '—')}</div>
    <div class="cell-dur num">${esc(fmtTrackDuration(t.durationMs) || '—')}</div>
    <div class="cell-src" title="${esc(t.path || '')}">${esc(platform)}</div>
    <div class="cell-size size">${esc(fmtSize(t.size))}</div>
    <div class="cell-prog">${progHTML(t, title)}</div>
    <div class="cell-status statuscell">${statusHTML(t)}</div>
  </div>`;
}

function patchRow(t) {
  const row = el.rows.querySelector(`.row[data-id="${esc(t.id)}"]`);
  if (!row) return;
  const title = trackTitle(t);
  const tileEl = $('.tile', row);
  if (tileEl && t.hasCover && !tileEl.querySelector('img')) {
    tileEl.classList.add('has-cover');
    const url = `/api/cover/${encodeURIComponent(t.id)}?t=${encodeURIComponent(S.token)}`;
    tileEl.insertAdjacentHTML('beforeend', `<img src="${esc(url)}" alt="" loading="lazy" onerror="this.remove();this.parentElement.classList.remove('has-cover')">`);
  }
  if (t.title && $('.ttl', row)) $('.ttl', row).textContent = t.title;
  const sub = $('.sub', row);
  if (sub) { sub.textContent = subText(t); sub.title = t.error || ''; }
  const meta2 = $('.meta2', row);
  if (meta2) meta2.innerHTML = meta2HTML(t);
  const oldProg = $('.prog', row);
  if (oldProg) oldProg.outerHTML = progHTML(t, title);
  const st = $('.statuscell', row);
  const nextStatus = statusHTML(t);
  if (st && st.innerHTML !== nextStatus) st.innerHTML = nextStatus;
  row.dataset.state = GROUP[t.state] || 'waiting';
}

/* ---------------------------------------------------------------- 操作 */

async function addSources(paths) {
  const data = await api('/api/sources', { method: 'POST', body: { paths } });
  const added = (data && data.added) || 0;
  toast(added ? `已添加 ${added} 个来源，开始扫描` : '这个文件夹已经在来源里了');
  await refresh();
}

async function addFolder() {
  if (S.busy) return;
  S.busy = true;
  el.addfolder.disabled = true;
  try {
    const data = await api('/api/pick-folder', { method: 'POST', body: { kind: 'source' } });
    if (!data || !data.path) { toast('没有选择文件夹'); return; }
    await addSources([data.path]);
  } catch (err) { fail(err); }
  finally {
    S.busy = false;
    el.addfolder.disabled = false;
  }
}

async function removeSource(id) {
  const name = sourceName(id) || '来源';
  try {
    await api('/api/sources/' + encodeURIComponent(id), { method: 'DELETE' });
    if (S.sourceId === id) S.sourceId = null;
    toast(`已移除来源「${name}」`);
    await refresh();
  } catch (err) { fail(err); }
}

async function rescan(ids) {
  S.scanUser = true;
  try {
    await api('/api/scan', { method: 'POST', body: { ids: ids || [] } });
    S.scanning = true;
    S.scanUser = true;
    renderTopbar();
    toast(ids && ids.length ? '正在刷新这个目录' : '已开始扫描目录');
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => {
      if (!S.scanning) return;
      S.scanning = false;
      renderTopbar();
      refresh();
    }, 60000);
  } catch (err) { fail(err); }
}

function setFilter(next) {
  S.filter = next;
  renderCounts();
  renderCrumbs();
  renderRows();
  renderFooter();
  renderSearchCount();
  renderSel();
}

function cycleSort(key) {
  if (S.sort.key !== key) S.sort = { key, dir: 1 };
  else if (S.sort.dir === 1) S.sort.dir = -1;
  else S.sort = { key: null, dir: 1 };
  const move = S.sort.key !== null;
  S.flipNext = move;
  renderSortState();
  renderRows();
  renderSel();
  renderFooter();
}

function renderSortState() {
  $$('[role="columnheader"][aria-sort]', el.qhead).forEach((h) => {
    const b = $('[data-sort]', h);
    if (!b) return;
    h.setAttribute('aria-sort', b.dataset.sort === S.sort.key ? (S.sort.dir === 1 ? 'ascending' : 'descending') : 'none');
  });
}

async function changeOutputDir() {
  if (S.busy) return;
  S.busy = true;
  el.changedir.disabled = true;
  if (el.setChangedir) el.setChangedir.disabled = true;
  try {
    const data = await api('/api/pick-folder', { method: 'POST', body: { kind: 'output' } });
    if (!data || !data.path) { toast('没有选择文件夹'); return; }
    await patchSettings({ output: { dir: data.path } }, 'output');
    toast(`输出目录改为 ${data.path}`);
  } catch (err) { fail(err); }
  finally {
    S.busy = false;
    el.changedir.disabled = false;
    if (el.setChangedir) el.setChangedir.disabled = false;
  }
}

async function setFormat(format) {
  const cur = (S.state && S.state.output) || {};
  if (cur.format === format) return;
  try {
    const patch = { output: { format } };
    if (format === 'mp3') patch.output.quality = el.quality.value || 'v2';
    await patchSettings(patch, 'output');
    toast(format === 'flac' ? '输出改为 FLAC 无损' : '输出改为 MP3');
  } catch (err) { fail(err); }
}

async function setQuality(quality) {
  try {
    await patchSettings({ output: { quality } }, 'output');
    const names = { v2: 'V2 透明档', v0: 'V0 高音质', '320': '320 kbps' };
    toast(`MP3 质量：${names[quality] || quality}`);
  } catch (err) { fail(err); }
}

async function toggleSetting(btn, key, pane) {
  const next = btn.getAttribute('aria-checked') !== 'true';
  btn.setAttribute('aria-checked', String(next));
  try {
    await patchSettings({ settings: { [key]: next } }, pane);
  } catch (err) {
    btn.setAttribute('aria-checked', String(!next));
    fail(err);
  }
}

async function handlePrimary() {
  const act = el.primary.dataset.act || 'convert';
  const ff = S.state && S.state.ffmpeg;
  if (act === 'none' || el.primary.getAttribute('aria-disabled') === 'true') {
    if ((act === 'convert' || act === 'convert-selected') && ff && ff.ok === false) toast('未找到 ffmpeg，先把 ffmpeg 放好再开始');
    else if (act === 'none') toast('没有等待转换的文件');
    return;
  }
  try {
    if (act === 'pause') {
      await api('/api/pause', { method: 'POST' });
      toast('已暂停，正在转换的文件会先完成');
      await refresh();
    } else if (act === 'resume') {
      await api('/api/resume', { method: 'POST' });
      toast('继续转换');
      await refresh();
    } else if (act === 'convert') {
      const res = await api('/api/convert', { method: 'POST', body: { ids: [] } });
      if (res && res.queued === 0) { nudgePrimary(); toast('这些歌都已经转好了，勾选想重转的再试'); return; }
      toast('开始转换');
      await refresh();
    } else if (act === 'convert-selected') {
      const ids = visibleTracks().filter((t) => S.selected.has(t.id)).map((t) => t.id);
      if (!ids.length) { toast('先勾选要转换的歌'); return; }
      const res = await api('/api/convert', { method: 'POST', body: { ids } });
      if (res && res.queued === 0) { nudgePrimary(); toast('所选的歌暂不支持转换'); return; }
      toast(`开始转换所选 ${ids.length} 首`);
      await refresh();
    } else if (act === 'open') {
      await api('/api/open-output', { method: 'POST' });
      toast(`已打开 ${(S.state && S.state.output && S.state.output.dir) || '输出文件夹'}`);
    }
  } catch (err) { fail(err); }
}

async function retryTrack(id) {
  try {
    await api('/api/tracks/' + encodeURIComponent(id) + '/retry', { method: 'POST' });
    toast('已重新加入队列');
    await refresh();
  } catch (err) { fail(err); }
}

async function cancelTrack(id) {
  try {
    await api('/api/tracks/' + encodeURIComponent(id) + '/cancel', { method: 'POST' });
    toast('已取消这一首');
    await refresh();
  } catch (err) { fail(err); }
}

async function openOutput() {
  try {
    await api('/api/open-output', { method: 'POST' });
    toast(`已打开 ${(S.state && S.state.output && S.state.output.dir) || '输出文件夹'}`);
  } catch (err) { fail(err); }
}

/* ---------------------------------------------------------------- 选择 */

function selectAllVisible(on) {
  const list = visibleTracks();
  if (on) list.forEach((t) => S.selected.add(t.id));
  else S.selected.clear();
  renderRows();
  renderSel();
}

/* ---------------------------------------------------------------- 设置 sheet */

function effectiveConc() {
  const st = (S.state && S.state.settings) || {};
  if (S.ui.conc != null) return S.ui.conc;
  return Math.min(6, Math.max(1, Number(st.concurrency) || 2));
}

function previewName(tpl, format) {
  const out = (S.state && S.state.output) || {};
  const withArtist = S.tracks.find((t) => t.artist && t.title);
  const sample = withArtist || S.tracks[0] || { artist: '周杰伦', title: '晴天', album: '叶惠美' };
  const t = String(tpl || '').trim() || '{artist} - {title}';
  const name = t
    .replace(/\{artist\}/g, sample.artist || '未知歌手')
    .replace(/\{title\}/g, trackTitle(sample))
    .replace(/\{album\}/g, sample.album || '未知专辑');
  return `${name}.${(format || out.format) === 'flac' ? 'flac' : 'mp3'}`;
}

function stamp(pane, text) {
  const node = $(`[data-stamp="${pane}"]`, el.sheet);
  if (!node) return;
  node.textContent = text;
  node.classList.add('show');
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.remove('show'), 1800);
}

function renderSheet() {
  if (!S.state) return;
  const out = S.state.output || {};
  const st = S.state.settings || {};
  el.setOutDir.textContent = out.dir || '未设置';
  const fmt = out.format === 'flac' ? 'flac' : 'mp3';
  el.setFmtMp3.setAttribute('aria-pressed', String(fmt === 'mp3'));
  el.setFmtFlac.setAttribute('aria-pressed', String(fmt === 'flac'));
  el.setQualityRow.hidden = fmt !== 'mp3';
  if (document.activeElement !== el.setQuality && out.quality) el.setQuality.value = out.quality;
  const tpl = st.template || '{artist} - {title}';
  if (document.activeElement !== el.setTemplate) el.setTemplate.value = tpl;
  el.setTemplatePreview.textContent = previewName(tpl, fmt);
  if (document.activeElement !== el.setConflict) el.setConflict.value = st.onConflict || 'rename';
  const conc = effectiveConc();
  $$('[data-conc]', el.concSeg).forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.conc) === conc)));
  const stConc = Math.min(6, Math.max(1, Number(st.concurrency) || 2));
  el.concNote.hidden = !(S.ui.conc != null && S.ui.conc !== stConc);
  el.swRetry.setAttribute('aria-checked', String(!!st.autoRetry));
  el.swOpen.setAttribute('aria-checked', String(!!st.openFolderWhenDone));
  el.swAuto.setAttribute('aria-checked', String(!!st.autoScanOnStart));
  el.swMotion.setAttribute('aria-checked', String(!!S.ui.motion));
  $$('[data-density]', el.densitySeg).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.density === S.ui.density)));
  $$('[data-speed]', el.animSpeedSeg).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.speed === S.ui.speed)));
  $$('[data-amp]', el.animAmpSeg).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.amp === S.ui.amp)));
  el.swRandom?.setAttribute('aria-checked', String(!!S.ui.random));
  el.swShowWaiting?.setAttribute('aria-checked', String(!!S.ui.showWaiting));
  el.swShowActive?.setAttribute('aria-checked', String(!!S.ui.showActive));
  $$('[data-theme]', el.themeSeg).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.theme === S.ui.theme)));
  $$('[data-view]', el.viewSeg).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === (S.ui.view || 'list'))));
  if (el.accentPick) {
    const current = S.ui.accent || getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
    if (/^#[0-9a-f]{6}/i.test(current)) el.accentPick.value = current;
  }
}

function setSheetPane(pane) {
  $$('.sheetnav button', el.sheet).forEach((b) => {
    if (b.dataset.pane === pane) b.setAttribute('aria-current', 'true');
    else b.removeAttribute('aria-current');
  });
  $$('.pane', el.sheet).forEach((p) => { p.hidden = p.dataset.pane !== pane; });
  el.sheetmain.scrollTop = 0;
  if (pane === 'import') loadApps();
  renderSheet();
}

function openSheet(pane) {
  if (sheetOpen) { setSheetPane(pane || 'output'); return; }
  if (drawerOpen) setDrawer(false, { restoreFocus: false });
  sheetReturnFocus = document.activeElement;
  sheetOpen = true;
  el.sheet.hidden = false;
  setSheetPane(pane || 'output');
  el.app.inert = true;
  requestAnimationFrame(() => el.sheet.classList.add('open'));
  el.sheet.focus({ preventScroll: true });
}

function closeSheet() {
  if (!sheetOpen) return;
  sheetOpen = false;
  el.sheet.classList.remove('open');
  el.app.inert = false;
  setTimeout(() => { if (!sheetOpen) el.sheet.hidden = true; }, 180);
  const back = sheetReturnFocus && sheetReturnFocus.focus ? sheetReturnFocus : el.opensettings;
  back.focus({ preventScroll: true });
}

function trapSheetTab(e) {
  const items = focusables(el.sheet);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const inside = el.sheet.contains(document.activeElement);
  if (e.shiftKey) {
    if (!inside || document.activeElement === first) { e.preventDefault(); last.focus(); }
  } else if (!inside || document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
}

/* ---------------------------------------------------------------- 导入 */

async function loadApps(force) {
  if (S.appsLoading) return;
  if (S.apps && !force) { renderApps(); return; }
  S.appsLoading = true;
  S.appsError = null;
  renderApps();
  try {
    S.apps = await api('/api/apps');
  } catch (err) {
    S.appsError = err;
    console.warn('[sonora] apps', err.code || '', err.message);
  } finally {
    S.appsLoading = false;
    renderApps();
  }
}

function renderApps() {
  if (!el.apps) return;
  if (S.appsLoading) {
    el.apps.innerHTML = '<div class="emptybox">正在查找这台电脑上的音乐应用…</div>';
    renderPhone();
    return;
  }
  if (S.appsError) {
    el.apps.innerHTML = `<div class="emptybox"><span>读不到应用目录，这次的后端可能还没提供这个接口。</span>` +
      '<button class="btn" type="button" data-apps-retry>重试</button></div>';
    renderPhone();
    return;
  }
  const apps = (S.apps && S.apps.apps) || [];
  if (!apps.length) {
    el.apps.innerHTML = '<div class="emptybox"><span>没找到装过音乐应用的目录。也可以直接用「添加文件夹」。</span></div>';
    renderPhone();
    return;
  }
  el.apps.innerHTML = apps.map((a) => {
    const exists = a.exists !== false;
    const count = Number(a.count) || 0;
    const can = exists && count > 0;
    const meta = !exists ? '路径不存在' : count > 0 ? `${count} 首可导入` : '没有找到可导入的文件';
    return `<div class="approw" data-state="${exists ? 'ok' : 'missing'}">
      <div><div class="aname">${esc(a.name)}</div><div class="apath">${esc(a.path)}</div>
      <div class="ameta">${esc(meta)}</div></div>
      <button class="btn" type="button" data-import="${esc(a.id)}" data-path="${esc(a.path)}"${can ? '' : ' disabled'}>导入</button>
    </div>`;
  }).join('');
  renderPhone();
}

function renderPhone() {
  if (!el.devices) return;
  const phone = (S.apps && S.apps.phone) || null;
  if (S.appsLoading) { el.phoneTag.textContent = '检测中'; el.phoneTag.className = 'tag'; return; }
  if (S.appsError || !phone) {
    el.phoneTag.textContent = '未检测';
    el.phoneTag.className = 'tag';
    el.phoneHint.textContent = '把手机用数据线连上电脑、在手机上选「传输文件」，这里会列出设备。';
    el.devices.innerHTML = '';
    return;
  }
  const devices = phone.devices || [];
  if (devices.length) {
    el.phoneTag.textContent = `已连接 ${devices.length} 台`;
    el.phoneTag.className = 'tag ok';
  } else {
    el.phoneTag.textContent = '未连接';
    el.phoneTag.className = 'tag';
  }
  if (phone.hint) el.phoneHint.textContent = phone.hint;
  el.devices.innerHTML = devices.map((d) =>
    `<div class="device"><span>${esc(d.name || 'MTP 设备')}</span></div>`).join('');
}

async function importApp(id, path, btn) {
  if (!id) return;
  const label = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '导入中…'; }
  try {
    const data = await api('/api/apps/import', { method: 'POST', body: { id } });
    let added = (data && data.added) || 0;
    // 后端按 id 命中同类应用的第一条目录；如果和这一行的路径不一致，按行上的路径补一次
    if (data && data.path && path && data.path !== path) {
      try {
        const extra = await api('/api/sources', { method: 'POST', body: { paths: [path] } });
        added = (extra && extra.added) || 0;
      } catch (err) { console.warn('[sonora] import path', err && err.message); }
    }
    toast(added ? `已导入 ${added} 首` : '这个目录里没有新的歌');
    await refresh();
    await loadApps(true);
  } catch (err) {
    fail(err);
    if (btn) { btn.disabled = false; btn.textContent = label || '导入'; }
  }
}

/* ---------------------------------------------------------------- 拖放 */

function hasFiles(e) {
  const dt = e.dataTransfer;
  if (!dt) return false;
  if (dt.files && dt.files.length) return true;
  const types = dt.types;
  return !!types && Array.prototype.indexOf.call(types, 'Files') !== -1;
}

function readAllEntries(reader) {
  return new Promise((resolve, reject) => {
    const all = [];
    const step = () => reader.readEntries((batch) => {
      if (!batch.length) { resolve(all); return; }
      all.push(...batch);
      step();
    }, reject);
    step();
  });
}

async function entryToFiles(entry) {
  if (!entry) return [];
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej));
    return file && file.name.indexOf('.') !== 0 ? [file] : [];
  }
  if (entry.isDirectory) {
    const entries = await readAllEntries(entry.createReader());
    const out = [];
    for (const child of entries) {
      const got = await entryToFiles(child);
      for (const f of got) out.push(f);
    }
    return out;
  }
  return [];
}

async function uploadAll(files) {
  toast(`正在复制 ${files.length} 个文件到收件箱…`);
  let saved = 0, failed = 0;
  for (const file of files) {
    try {
      await api('/api/upload?name=' + encodeURIComponent(file.name), { method: 'POST', file });
      saved++;
    } catch (err) {
      failed++;
      console.warn('[sonora] upload failed', file.name, err && err.message);
    }
  }
  toast(saved
    ? `已复制 ${saved} 个文件到收件箱${failed ? `，${failed} 个失败` : ''}`
    : '复制到收件箱失败');
  if (saved) await refresh();
}

async function onDrop(e) {
  e.preventDefault();
  dragDepth = 0;
  el.dropmask.hidden = true;
  const dt = e.dataTransfer;
  if (!dt) return;
  let files = [];
  const items = dt.items;
  if (items && items.length) {
    const entries = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind && it.kind !== 'file') continue;
      const entry = it.webkitGetAsEntry ? it.webkitGetAsEntry() : null;
      if (entry) entries.push(entry);
      else if (it.getAsFile) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    for (const entry of entries) {
      try {
        const got = await entryToFiles(entry);
        for (const f of got) files.push(f);
      } catch (err) { console.warn('[sonora] read entry failed', err && err.message); }
    }
  }
  if (!files.length && dt.files) files = Array.from(dt.files);
  files = files.filter((f) => f && f.name && f.name.indexOf('.') !== 0).slice(0, 300);
  if (!files.length) { toast('没有拿到可复制的文件'); return; }
  await uploadAll(files);
}

/* ---------------------------------------------------------------- 侧栏抽屉 */

function isMobile() { return mq.matches; }

function syncSidebar() {
  const open = isMobile() && drawerOpen;
  el.sidebar.classList.toggle('open', open);
  el.scrim.classList.toggle('show', open);
  el.menu.setAttribute('aria-expanded', String(open));
  el.sidebar.inert = isMobile() && !open;
  el.canvas.inert = open;
  el.dock.inert = open;
}

function setDrawer(open, opts) {
  const next = isMobile() ? !!open : false;
  const changed = next !== drawerOpen;
  drawerOpen = next;
  syncSidebar();
  if (!isMobile() || !changed) return;
  if (next) {
    const first = focusables(el.sidebar)[0];
    (first || el.sidebar).focus({ preventScroll: true });
  } else if (!opts || opts.restoreFocus !== false) {
    el.menu.focus({ preventScroll: true });
  }
}

function trapTab(e) {
  const items = focusables(el.sidebar);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const inside = el.sidebar.contains(active);
  if (e.shiftKey) {
    if (!inside || active === first) { e.preventDefault(); last.focus(); }
  } else if (!inside || active === last) {
    e.preventDefault();
    first.focus();
  }
}

/* ---------------------------------------------------------------- SSE */

function connectEvents() {
  if (sse) { try { sse.close(); } catch (e) { /* 忽略 */ } sse = null; }
  if (!S.token) return;
  sse = new EventSource('/api/events?t=' + encodeURIComponent(S.token));
  sse.addEventListener('track', (ev) => {
    try { onTrack(JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
  });
  sse.addEventListener('batch', (ev) => {
    try { onBatch(JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
  });
  sse.addEventListener('scan', (ev) => {
    try { onScan(JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
  });
  sse.addEventListener('toast', (ev) => {
    try {
      const d = JSON.parse(ev.data);
      if (d && d.message) toast(d.message);
    } catch (e) { /* 忽略坏帧 */ }
  });
  sse.onerror = () => {
    try { sse.close(); } catch (e) { /* 忽略 */ }
    sse = null;
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = 0;
      connectEvents();
    }, 3000);
  };
}

function onTrack(d) {
  if (!d || !d.id) return;
  const idx = S.tracks.findIndex((x) => x.id === d.id);
  if (idx === -1) return;
  const prev = S.tracks[idx];
  const merged = Object.assign({}, prev, d);
  S.tracks[idx] = merged;
  const structural = prev.state !== merged.state || !matchesFilter(merged) || !matchesQuery(merged);
  if (structural) {
    S.flipNext = true;
    renderRows();
    renderCounts();
    renderFooter();
    renderSel();
    return;
  }
  patchRow(merged);
  renderFooter();
}

function onBatch(d) {
  if (!S.state || !d) return;
  S.state.batch = Object.assign({}, S.state.batch || {}, d);
  if (d.totals) {
    S.state.totals = d.totals;
    renderCounts();
  }
  renderReadout();
  renderPrimary();
}

function onScan(d) {
  const was = S.scanning;
  S.scanning = !!(d && d.running);
  renderTopbar();
  if (was && !S.scanning) {
    el.refreshDir?.classList.add('done');
    setTimeout(() => el.refreshDir?.classList.remove('done'), 900);
  }
  if (!S.scanning) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(async () => {
      await refresh();
      if (S.scanUser) {
        S.scanUser = false;
        toast(`刷新完成 · 共 ${(S.state && S.state.totals && S.state.totals.all) || 0} 首`);
      }
    }, 400);
  }
}

/* ---------------------------------------------------------------- 绑定 */

function bind() {
  const qinfo = $('#qinfo');
  const qpop = $('#qpop');
  if (qinfo && qpop) {
    const closeQ = () => {
      if (!qpop.hidden) { qpop.hidden = true; qinfo.setAttribute('aria-expanded', 'false'); }
    };
    qinfo.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = qpop.hidden;
      qpop.hidden = !open;
      qinfo.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', (e) => {
      if (!qpop.hidden && !qpop.contains(e.target) && e.target !== qinfo) closeQ();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeQ(); });
  }

  el.menu.addEventListener('click', () => setDrawer(!drawerOpen));
  el.scrim.addEventListener('click', () => setDrawer(false));
  el.drawerclose.addEventListener('click', () => setDrawer(false));

  el.addfolder.addEventListener('click', addFolder);
  el.rescan.addEventListener('click', () => rescan());
  el.refreshDir?.addEventListener('click', () => rescan());
  el.changedir.addEventListener('click', changeOutputDir);
  el.setChangedir.addEventListener('click', changeOutputDir);
  el.openfolder.addEventListener('click', openOutput);
  el.primary.addEventListener('click', handlePrimary);
  el.fmtMp3.addEventListener('click', () => setFormat('mp3'));
  el.fmtFlac.addEventListener('click', () => setFormat('flac'));
  el.quality.addEventListener('change', () => setQuality(el.quality.value));
  el.viewall.addEventListener('click', async () => {
    S.limit = Math.max(S.total, MAX_ROWS * 4);
    await loadTracks();
    renderFooter();
  });
  el.unsupportedLink.addEventListener('click', () => setFilter(S.filter === 'unsupported' ? 'all' : 'unsupported'));
  el.selectallBtn.addEventListener('click', () => selectAllVisible(!el.selectall?.checked));
  el.selectallTop?.addEventListener('click', () => selectAllVisible(!el.selectall?.checked));
  el.selectall?.addEventListener('change', () => selectAllVisible(el.selectall.checked));

  el.counts.forEach((btn) => {
    btn.addEventListener('click', () => setFilter(btn.dataset.filter === S.filter ? 'all' : btn.dataset.filter));
  });

  /* 搜索 */
  el.search.addEventListener('input', () => {
    S.q = el.search.value.trim();
    el.searchclear.hidden = !el.search.value;
    S.firstPaint = false;
    renderRows();
    renderFooter();
    renderCrumbs();
    renderSearchCount();
    renderSel();
  });
  el.search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && el.search.value) {
      e.stopPropagation();
      el.search.value = '';
      S.q = '';
      el.searchclear.hidden = true;
      renderRows(); renderFooter(); renderSearchCount(); renderSel();
    }
  });
  el.searchclear.addEventListener('click', () => {
    el.search.value = '';
    S.q = '';
    el.searchclear.hidden = true;
    renderRows(); renderFooter(); renderSearchCount(); renderSel();
    el.search.focus();
  });

  /* 面包屑与清除选择 */
  el.crumbs.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-clear]');
    if (!b) return;
    const what = b.dataset.clear;
    if (what === 'source' || what === 'all') {
      S.sourceId = null;
      renderSources();
      renderCrumbs();
      await loadTracks();
    }
    if (what === 'filter' || what === 'all') setFilter('all');
  });

  el.dock.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act="clearsel"]');
    if (!b) return;
    S.selected.clear();
    renderRows();
    renderSel();
  });

  /* 排序 */
  el.qhead.addEventListener('click', (e) => {
    const b = e.target.closest('[data-sort]');
    if (!b) return;
    cycleSort(b.dataset.sort);
  });

  /* 来源 */
  el.sources.addEventListener('click', async (e) => {
    const rf = e.target.closest('[data-refresh]');
    if (rf) { await rescan([rf.dataset.refresh]); return; }
    const rm = e.target.closest('[data-remove]');
    if (rm) { await removeSource(rm.dataset.remove); return; }
    const src = e.target.closest('[data-src]');
    if (!src) return;
    const next = src.dataset.src || null;
    S.sourceId = S.sourceId === next ? null : next;
    renderSources();
    renderCrumbs();
    await loadTracks();
    if (isMobile()) setDrawer(false);
  });

  /* 行内动作与勾选 */
  el.rows.addEventListener('click', async (e) => {
    const add = e.target.closest('[data-action="add-folder"]');
    if (add) { await addFolder(); return; }
    const retry = e.target.closest('[data-retry]');
    if (retry) { await retryTrack(retry.dataset.retry); return; }
    const cancel = e.target.closest('[data-cancel]');
    if (cancel) { await cancelTrack(cancel.dataset.cancel); return; }
    if (e.target.closest('button, a, input, label, select')) return;
    const row = e.target.closest('.row');
    if (!row || !row.dataset.id) return;
    const id = row.dataset.id;
    if (S.selected.has(id)) S.selected.delete(id);
    else S.selected.add(id);
    row.dataset.selected = String(S.selected.has(id));
    renderSel();
  });
  el.viewSeg?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-view]');
    if (b) setView(b.dataset.view);
  });

  el.rows.addEventListener('click', (e) => {
    const open = e.target.closest('[data-album-open]');
    if (open) { S.album = decodeURIComponent(open.dataset.albumOpen); renderRows(); return; }
    const pick = e.target.closest('[data-album-pick]');
    if (pick) { toggleAlbum(decodeURIComponent(pick.dataset.albumPick)); return; }
  });

  el.albumhead?.addEventListener('click', (e) => {
    if (e.target.closest('[data-album-back]')) { S.album = null; renderRows(); return; }
    const sel = e.target.closest('[data-album-select]');
    if (sel) { toggleAlbum(decodeURIComponent(sel.dataset.albumSelect)); renderRows(); }
  });

  el.rows.addEventListener('dragstart', (e) => {
    const a = e.target.closest('a.filelink');
    if (!a) return;
    const href = new URL(a.getAttribute('href'), location.origin).href;
    const mime = a.dataset.mime || 'application/octet-stream';
    const name = a.dataset.filename || 'audio';
    e.dataTransfer.effectAllowed = 'copyMove';
    try { e.dataTransfer.setData('DownloadURL', `${mime}:${name}:${href}`); } catch (err) { /* 忽略 */ }
    e.dataTransfer.setData('text/uri-list', href);
    e.dataTransfer.setData('text/plain', name);
  });
  el.rows.addEventListener('change', (e) => {
    const cb = e.target.closest('[data-select]');
    if (!cb) return;
    const id = cb.dataset.select;
    if (cb.checked) S.selected.add(id); else S.selected.delete(id);
    const row = cb.closest('.row');
    if (row) row.dataset.selected = String(cb.checked);
    renderSel();
  });

  /* 设置 */
  el.opensettings.addEventListener('click', () => openSheet('output'));
  el.sheetclose.addEventListener('click', closeSheet);
  el.sheetnav.addEventListener('click', (e) => {
    const b = e.target.closest('[data-pane]');
    if (b) setSheetPane(b.dataset.pane);
  });
  el.setFmtMp3.addEventListener('click', () => setFormat('mp3'));
  el.setFmtFlac.addEventListener('click', () => setFormat('flac'));
  el.setQuality.addEventListener('change', () => setQuality(el.setQuality.value));
  el.setTemplate.addEventListener('input', () => {
    el.setTemplatePreview.textContent = previewName(el.setTemplate.value, (S.state.output || {}).format);
  });
  el.setTemplate.addEventListener('change', async () => {
    const v = el.setTemplate.value.trim() || '{artist} - {title}';
    try {
      await patchSettings({ settings: { template: v } }, 'output');
    } catch (err) { fail(err); }
  });
  el.setConflict.addEventListener('change', async () => {
    try {
      await patchSettings({ settings: { onConflict: el.setConflict.value } }, 'output');
    } catch (err) { fail(err); }
  });
  el.concSeg.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-conc]');
    if (!b) return;
    const n = Number(b.dataset.conc);
    if (!Number.isFinite(n)) return;
    S.ui.conc = n;
    saveUI();
    renderSheet();
    try {
      const data = await api('/api/settings', { method: 'PATCH', body: { settings: { concurrency: n } } });
      const got = data && data.settings ? Number(data.settings.concurrency) : null;
      if (got === n) { S.ui.conc = null; saveUI(); }
      applyState(data);
      stamp('convert', '已保存');
      renderSheet();
    } catch (err) {
      S.ui.conc = null;
      saveUI();
      renderSheet();
      fail(err);
    }
  });
  el.swRetry.addEventListener('click', () => toggleSetting(el.swRetry, 'autoRetry', 'convert'));
  el.swOpen.addEventListener('click', () => toggleSetting(el.swOpen, 'openFolderWhenDone', 'convert'));
  el.swAuto.addEventListener('click', () => toggleSetting(el.swAuto, 'autoScanOnStart', 'convert'));
  el.swMotion.addEventListener('click', () => {
    S.ui.motion = el.swMotion.getAttribute('aria-checked') !== 'true';
    saveUI();
    applyUI();
    renderSheet();
    stamp('ui', '已保存');
  });
  el.accentPick?.addEventListener('input', () => {
    S.ui.accent = el.accentPick.value;
    saveUI(); applyUI();
    stamp('ui', '已保存');
  });
  el.accentReset?.addEventListener('click', () => {
    S.ui.accent = '';
    saveUI(); applyUI(); renderSheet();
    stamp('ui', '已保存');
  });
  el.themeSeg?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-theme]');
    if (!b) return;
    S.ui.theme = b.dataset.theme;
    saveUI(); applyUI(); renderSheet(); stamp('ui', '已保存');
  });
  el.animSpeedSeg?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-speed]');
    if (!b) return;
    S.ui.speed = b.dataset.speed;
    saveUI(); applyUI(); renderSheet(); stamp('ui', '已保存');
  });
  el.animAmpSeg?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-amp]');
    if (!b) return;
    S.ui.amp = b.dataset.amp;
    saveUI(); applyUI(); renderSheet(); stamp('ui', '已保存');
  });
  el.swShowWaiting?.addEventListener('click', () => {
    S.ui.showWaiting = !S.ui.showWaiting;
    saveUI(); renderCounts(); renderSheet();
    stamp('ui', '已保存');
  });
  el.swShowActive?.addEventListener('click', () => {
    S.ui.showActive = !S.ui.showActive;
    saveUI(); renderCounts(); renderSheet();
    stamp('ui', '已保存');
  });
  el.swRandom?.addEventListener('click', () => {
    S.ui.random = el.swRandom.getAttribute('aria-checked') !== 'true';
    saveUI(); applyUI(); renderSheet(); stamp('ui', '已保存');
  });
  el.densitySeg.addEventListener('click', (e) => {
    const b = e.target.closest('[data-density]');
    if (!b) return;
    S.ui.density = b.dataset.density;
    saveUI();
    applyUI();
    renderSheet();
    stamp('ui', '已保存');
  });
  el.apps.addEventListener('click', (e) => {
    const retry = e.target.closest('[data-apps-retry]');
    if (retry) { loadApps(true); return; }
    const imp = e.target.closest('[data-import]');
    if (imp) importApp(imp.dataset.import, imp.dataset.path, imp);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (sheetOpen) { e.preventDefault(); closeSheet(); return; }
      if (drawerOpen) { e.preventDefault(); setDrawer(false); return; }
    }
    if (sheetOpen && e.key === 'Tab') trapSheetTab(e);
    else if (drawerOpen && e.key === 'Tab') trapTab(e);
  });

  mq.addEventListener('change', () => {
    if (!isMobile()) drawerOpen = false;
    syncSidebar();
  });
  reduced.addEventListener('change', () => { applyUI(); renderSheet(); });

  let albumResize = 0;
  window.addEventListener('resize', () => {
    if (S.ui.view !== 'albums' || S.album) return;
    clearTimeout(albumResize);
    albumResize = setTimeout(() => renderAlbums(), 160);
  });

  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    if (dragDepth > 0) el.dropmask.hidden = false;
  });
  window.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  });
  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) {
      if (!e.relatedTarget) { dragDepth = 0; el.dropmask.hidden = true; }
      return;
    }
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) el.dropmask.hidden = true;
  });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    onDrop(e).catch((err) => {
      el.dropmask.hidden = true;
      console.warn('[sonora] drop failed', err && err.message);
    });
  });
  window.addEventListener('dragend', () => {
    dragDepth = 0;
    el.dropmask.hidden = true;
  });
}

/* ---------------------------------------------------------------- 启动 */

let heartbeat = 0;

function startHeartbeat() {
  if (heartbeat || !S.token) return;
  const ping = () => {
    fetch(`/api/ping?t=${encodeURIComponent(S.token)}`, { method: 'POST', cache: 'no-store' }).catch(() => {});
  };
  ping();
  heartbeat = setInterval(ping, 3000);
}

window.addEventListener('pagehide', () => {
  if (!S.token) return;
  const url = `/api/bye?t=${encodeURIComponent(S.token)}`;
  try {
    if (navigator.sendBeacon) navigator.sendBeacon(url);
    else fetch(url, { method: 'POST', keepalive: true });
  } catch (e) { /* 忽略 */ }
});

async function boot() {
  loadUI();
  applyUI();
  S.token = takeToken();
  if (!S.token) {
    showGate();
    return;
  }
  el.gate.hidden = true;
  el.app.hidden = false;
  bind();
  renderSortState();
  syncSidebar();
  await refresh();
  connectEvents();
  startHeartbeat();
}

boot();

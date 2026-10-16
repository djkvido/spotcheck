"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const th = (key, params = {}) => t(key, Object.fromEntries(Object.entries(params).map(([k, v]) => [k, escapeHtml(v)])));
const te = (key, params) => escapeHtml(t(key, params));

let numberFormats = new Map();
function fmt(value, digits = 1, fixed = false) {
  if (value == null || Number.isNaN(value)) return "–";
  const key = `${digits}:${fixed}`;
  if (!numberFormats.has(key)) {
    numberFormats.set(key, new Intl.NumberFormat(locale(),
      { minimumFractionDigits: fixed ? digits : 0, maximumFractionDigits: digits }));
  }
  return numberFormats.get(key).format(value);
}
const fmtSigned = (value, digits = 1) => (value == null ? "–"
  : (value > 0 ? "+" : value < 0 ? "−" : "") + fmt(Math.abs(value), digits));
const fmtBer = (value) => fmt(value, 2, true);
function fmtTime(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "–";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(locale(), { dateStyle: "medium", timeStyle: "short" });
const fmtShortDate = (ts) => new Date(ts * 1000).toLocaleDateString(locale(), { day: "numeric", month: "numeric", year: "2-digit" });
const fmtClock = (ts) => new Date(ts * 1000).toLocaleTimeString(locale());

function formatParams(params, signed = false) {
  return Object.fromEntries(Object.entries(params || {}).map(([k, v]) => {
    if (typeof v !== "number") return [k, v];
    if (k === "ber") return [k, fmtBer(v)];
    return [k, signed ? fmtSigned(v) : fmt(v, 2)];
  }));
}

const ICONS = {
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  cut: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/>',
  youtube: '<path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2 29 29 0 0 0 .46-5.25 29 29 0 0 0-.46-5.33z"/><polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02"/>',
  spotify: '<circle cx="12" cy="12" r="10"/><path d="M7 9.5c3.5-1 7-.7 10 1"/><path d="M7.5 12.5c3-.8 6-.5 8.5 1"/><path d="M8 15.3c2.5-.6 5-.4 7 .8"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  undo: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
  tag: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  share: '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>',
  cloud: '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
};
const icon = (name, cls = "") => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const isYoutube = (url) => /youtu\.?be/.test(url || "");
const sourceIcon = (url) => icon(isYoutube(url) ? "youtube" : "link", "sm");
const sourceName = (url) => (isYoutube(url) ? "YouTube" : /soundcloud/.test(url || "") ? "SoundCloud" : t("source.other"));

const STATUS_CLASS = { ok: "s-ok", partial: "s-partial", review: "s-review", bad: "s-bad", missing: "s-missing", unchecked: "s-unchecked" };
const STATUS_ORDER = Object.keys(STATUS_CLASS);
const STAT_CARDS = ["ok", "partial", "review", "bad", "missing"];
const VERDICT_CLASS = { MATCH: "s-ok", UNSURE: "s-review", MISMATCH: "s-bad" };
const statusOf = (tr) => (STATUS_CLASS[tr.status] ? tr.status : "unchecked");
const statusLabel = (status) => t(`status.${STATUS_CLASS[status] ? status : "unchecked"}`);
const ORIGIN_NAMES = { spotify: "Spotify", youtube: "YouTube", soundcloud: "SoundCloud", bandcamp: "Bandcamp" };
const ORIGIN_ICONS = { spotify: "spotify", youtube: "youtube", soundcloud: "cloud", bandcamp: "music" };
const originOf = (item) => (ORIGIN_NAMES[item?.origin] ? item.origin : "spotify");
const originIcon = (origin) => icon(ORIGIN_ICONS[origin] || "link", "sm");

function classifyUrl(url) {
  let m = url.match(/^https:\/\/open\.spotify\.com\/(?:intl-[a-z]+\/)?(?:playlist|album)\/([A-Za-z0-9]+)/);
  if (m) return ["spotify", m[1]];
  m = url.match(/^https:\/\/(?:www\.|m\.|music\.)?youtube\.com\/(?:playlist|watch|browse)\?\S*?\blist=([A-Za-z0-9_-]+)/);
  if (m) return /^(LL|WL|LM|RD)/.test(m[1]) ? null : ["youtube", `yt-${m[1]}`];
  m = url.match(/^https:\/\/(?:www\.|m\.)?soundcloud\.com\/([A-Za-z0-9_-]+)\/sets\/([A-Za-z0-9_-]+)/);
  if (m) return ["soundcloud", `sc-${m[1]}-${m[2]}`];
  m = url.match(/^https:\/\/([a-z0-9-]+)\.bandcamp\.com\/album\/([A-Za-z0-9_-]+)/);
  return m ? ["bandcamp", `bc-${m[1]}-${m[2]}`] : null;
}

function unsupportedKind(url) {
  if (/^https:\/\/(?:www\.|m\.|music\.)?youtube\.com\/(?:playlist|watch|browse)\?\S*?\blist=(?:LL|WL|LM|RD)/.test(url)) return "mix";
  if (/^https:\/\/open\.spotify\.com\/(?:intl-[a-z]+\/)?track\//.test(url)) return "track";
  if (/^https:\/\/(?:www\.|m\.|music\.)?youtube\.com\/watch\?/.test(url) || /^https:\/\/youtu\.be\//.test(url)) return "track";
  if (/^https:\/\/[a-z0-9-]+\.bandcamp\.com\/track\//.test(url)) return "track";
  if (/^https:\/\/(?:www\.|m\.)?soundcloud\.com\/[A-Za-z0-9_-]+\/(?!sets\/)[A-Za-z0-9_-]+/.test(url)) return "track";
  return null;
}

const referenceLabel = (tr) => (originOf(tr) === "spotify" ? "Spotify" : tr.official ? t("ref.official") : t("ref.upload"));

const QUALITY_ORDER = ["full", "high", "good", "standard", "low"];
const QUALITY_CLASS = { full: "s-ok", high: "s-ok", good: "s-ok", standard: "s-partial", low: "s-review" };
function qualityOf(check) {
  if (!check || !check.cutoff_measured) return null;
  const cutoff = check.cutoff;
  if (cutoff == null) return "full";
  if (cutoff >= 19) return "high";
  if (cutoff >= 17.5) return "good";
  return cutoff >= (state.library.settings.min_cutoff_khz ?? 15) ? "standard" : "low";
}
const rangeText = (check) => (check.cutoff == null ? t("fact.rangeFull") : t("fact.rangeValue", { khz: fmt(check.cutoff) }));

let twinCache = { library: null, counts: new Map() };
function twinCount(id) {
  if (twinCache.library !== state.library) {
    const counts = new Map();
    state.library.playlists.forEach((pl) => pl.tracks.forEach((tr) => counts.set(tr.id, (counts.get(tr.id) || 0) + 1)));
    twinCache = { library: state.library, counts };
  }
  return twinCache.counts.get(id) || 0;
}
function trackFindings(tr) {
  const codes = (tr.notes || []).map((n) => n.code);
  if (twinCount(tr.id) > 1) codes.push("duplicate");
  return codes;
}
const FULL = { download: true, validate: true, verify: true, fix: true, trim: true };
const TERMINAL = new Set(["done", "failed", "cancelled"]);

const state = {
  project: null,
  library: { settings: {}, playlists: [], saved_at: null },
  meta: null,
  jobs: {},
  logs: [],
  ffmpeg: null,
  version: "",
  selected: "all",
  filter: "all",
  query: "",
  sort: "order",
  openKey: null,
  lastJob: null,
  settings: { draft: null, page: "match", search: "", errors: {}, deps: null },
  projects: { list: [], current: null, view: null, picker: false, imported: null, musicTouched: false, errors: {} },
  helpTopic: "start",
  view: "playlists",
  finding: "",
  plSort: "name",
};

const VIEW_KEYS = ["selected", "view", "filter", "finding", "sort", "plSort"];
function restoreView() {
  try {
    const saved = JSON.parse(localStorage.getItem("spotcheck-view") || "{}");
    VIEW_KEYS.forEach((k) => { if (typeof saved[k] === "string") state[k] = saved[k]; });
  } catch {}
}
function rememberView() {
  try { localStorage.setItem("spotcheck-view", JSON.stringify(Object.fromEntries(VIEW_KEYS.map((k) => [k, state[k]])))); } catch {}
}

const keyOf = (pl, tr) => `${pl.id}:${tr.id}`;
const enc = encodeURIComponent;

function findTrack(key) {
  if (!key) return null;
  const i = key.lastIndexOf(":");
  const pl = state.library.playlists.find((p) => p.id === key.slice(0, i));
  const tr = pl && pl.tracks.find((x) => x.id === key.slice(i + 1));
  return tr ? { pl, tr } : null;
}
const allTracks = () => state.library.playlists.flatMap((pl) => pl.tracks.map((tr, i) => ({ pl, tr, i })));
const selectedPlaylist = () => (state.selected === "all" ? null : state.library.playlists.find((p) => p.id === state.selected) || null);
const scopeTracks = () => {
  const pl = selectedPlaylist();
  return pl ? pl.tracks.map((tr, i) => ({ pl, tr, i })) : allTracks();
};
const scopeName = () => (selectedPlaylist() ? selectedPlaylist().name : t("scope.project"));
const runningJob = () => Object.values(state.jobs).find((j) => j.state === "running") || null;
const busy = () => Object.values(state.jobs).some((j) => !TERMINAL.has(j.state));

function countByStatus(items) {
  const counts = { all: items.length, proposal: 0 };
  STATUS_ORDER.forEach((s) => { counts[s] = 0; });
  items.forEach(({ tr }) => {
    counts[statusOf(tr)] += 1;
    if (tr.proposal) counts.proposal += 1;
  });
  counts.missingAll = counts.missing + counts.unchecked;
  return counts;
}

function actionCounts(items) {
  const s = state.library.settings;
  const trimmable = ({ tr }) => {
    const check = tr.check || {};
    return Boolean(tr.file) && ((check.lead || 0) >= s.min_silence || (check.tail || 0) >= s.min_silence);
  };
  return {
    all: items.length,
    missing: items.filter(({ tr }) => !tr.file).length,
    check: items.filter(({ tr }) => tr.file).length,
    problems: items.filter(({ tr }) => tr.status === "bad" || tr.status === "review").length,
    trim: s.trim_silence ? items.filter(trimmable).length : 0,
  };
}

function noteText(note) {
  const params = note.params || {};
  if (note.code === "proposal" && params.ber != null) return t("note.proposal_ber", formatParams(params));
  if (note.code === "tags_missing") return t("note.tags_missing", { fields: (params.fields || []).map((f) => t(`tag.${f}`)).join(", ") });
  return t(`note.${note.code}`, formatParams(params, note.code === "length_diff"));
}
const NOTE_CLASS = { damaged: "s-bad", incomplete: "s-bad", mismatch: "s-bad", intro_likely: "s-bad", shorter: "s-bad", silent_start: "s-bad", unsure: "s-review", low_quality: "s-review", upscaled: "s-review", proposal: "s-run" };

function errorText(code, params) {
  return hasMessage(`error.${code}`) ? t(`error.${code}`, formatParams(params)) : String(code);
}

const jobLabel = (job) => t(`job.${job.label.code}`, formatParams(job.label.params));

function logText(entry, names) {
  const params = formatParams(entry.params);
  let text = hasMessage(`log.${entry.code}`) ? t(`log.${entry.code}`, params) : errorText(entry.code, entry.params);
  const own = ["track_failed", "file_locked"].includes(entry.code);
  if (entry.track && !own && names.has(entry.track)) text = `${names.get(entry.track)}: ${text}`;
  return text;
}

function jobResult(job) {
  const s = job.summary || {};
  if (job.state === "cancelled") return { text: t("result.cancelled", { job: jobLabel(job) }) };
  if (job.state === "failed") return { text: t("result.failed", { job: jobLabel(job), error: errorText(s.error || "") }), error: true };
  if (job.kind === "process") {
    if (s.fix) return { text: t(`result.fix.${s.fix}`) };
    const parts = STATUS_ORDER.filter((k) => (s.statuses || {})[k])
      .map((k) => `${s.statuses[k]} ${statusLabel(k).toLowerCase()}`);
    let text = t("result.process", { job: jobLabel(job), tracks: s.tracks || 0, parts: parts.join(", ") || "–" });
    if (s.proposals) text += ` ${t("result.proposals", { n: s.proposals })}`;
    if (s.replaced) text += ` ${t("result.replaced", { n: s.replaced })}`;
    if (s.failed) return { text: `${text} ${t("result.trackErrors", { n: s.failed })}`, error: true };
    return { text };
  }
  if (job.kind === "sync") {
    const text = t("result.sync", { n: s.playlists || 0, added: s.added || 0, removed: s.removed || 0 });
    return s.failed ? { text: `${text} ${t("result.syncFailed", { n: s.failed })}`, error: true } : { text };
  }
  if (job.kind === "tags") {
    const text = t("result.tags", { fixed: s.fixed || 0 });
    return s.failed ? { text: `${text} ${t("result.tagsFailed", { n: s.failed })}`, error: true } : { text };
  }
  if (job.kind === "export") return { text: t("result.export", { files: s.files || 0, copied: s.copied || 0, skipped: s.skipped || 0 }) };
  if (job.kind === "organize") return { text: t("result.organize", { renamed: s.renamed || 0, playlists: s.playlists || 0 }) };
  if (job.kind === "apply") return { text: t("result.apply") };
  if (job.kind === "restore") return { text: t("result.restore") };
  if (job.kind === "deps") return s.ok ? { text: t("result.depsOk") } : { text: t("result.depsFailed"), error: true };
  return { text: t("result.done", { job: jobLabel(job) }) };
}

class ApiError extends Error {
  constructor(status, detail) {
    const fields = detail && typeof detail === "object" && detail.fields ? detail.fields : null;
    super(typeof detail === "string" ? errorText(detail) : fields ? t("error.form") : t("error.unexpected"));
    this.status = status;
    this.code = typeof detail === "string" ? detail : null;
    this.fields = fields;
  }
}

async function api(path, method = "GET", body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-SpotCheck": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "server_unreachable");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail ?? detail; } catch {}
    throw new ApiError(response.status, detail);
  }
  return response.json();
}

let statusTimer = 0;
function say(text, error = false) {
  const el = $("#statusline");
  el.textContent = text;
  el.classList.toggle("error", error);
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { el.textContent = ""; }, error ? 12000 : 7000);
}
const sayError = (error) => say(error.message, true);

function confirmDialog(title, html, okLabel = t("app.continue"), danger = false) {
  return new Promise((resolve) => {
    const dialog = $("#dlg-confirm");
    $("#confirm-title").textContent = title;
    $("#confirm-body").innerHTML = html;
    const ok = $("#confirm-ok");
    ok.textContent = okLabel;
    ok.classList.toggle("danger", danger);
    const finish = (value) => { dialog.onclose = null; dialog.close(); resolve(value); };
    ok.onclick = () => finish(true);
    $("#confirm-cancel").onclick = () => finish(false);
    dialog.onclose = () => resolve(false);
    dialog.showModal();
  });
}
const para = (html) => `<p>${html}</p>`;

function segmentBar(counts, total, extraClass = "") {
  const parts = total ? STATUS_ORDER.map((s) => (counts[s]
    ? `<i class="${STATUS_CLASS[s]}" style="width:${(counts[s] / total) * 100}%"></i>` : "")).join("") : "";
  return `<div class="seg ${extraClass}">${parts}</div>`;
}

function renderTopbar() {
  $("#project-name").textContent = state.project ? state.project.name : "–";
  const ff = state.ffmpeg || {};
  const total = allTracks().length;
  const saved = state.library.saved_at ? fmtClock(state.library.saved_at) : "–";
  $("#sysinfo").innerHTML = `
    <span title="${te("sys.ffmpegTitle")}"><i class="led ${ff.found ? "" : "off"}"></i>FFMPEG ${escapeHtml(ff.version ? ff.version.split("-")[0] : t("sys.missing"))}</span>
    <span class="${ff.chromaprint ? "on" : "off"}" title="${te("sys.chromaprintTitle")}">CHROMAPRINT</span>
    <span class="extra">${te("sys.tracks", { n: total })}</span>
    <span class="extra" title="${te("sys.savedTitle")}">${te("sys.saved", { time: saved })}</span>`;
}

function renderBanner() {
  const ff = state.ffmpeg;
  let key = "";
  if (ff && !ff.found) key = "banner.noFfmpeg";
  else if (ff && !ff.chromaprint) key = "banner.noChromaprint";
  const banner = $("#banner");
  banner.innerHTML = key ? `<span>${te(key)}</span><span class="spacer"></span><button class="btn sm" type="button" data-action="deps">${te("banner.open")}</button>` : "";
  banner.classList.toggle("show", Boolean(key));
}

function renderPlaylists() {
  const lists = state.library.playlists;
  if (!lists.length) {
    $("#playlists").innerHTML = `<p class="pl-empty">${te("playlists.empty")}</p>`;
    return;
  }
  const all = countByStatus(allTracks());
  const item = (id, name, c, thumb) => `<button class="pl ${state.selected === id ? "on" : ""}" type="button" data-playlist="${escapeHtml(id)}" title="${escapeHtml(name)}">
      ${thumb}<span class="pl-text"><span class="name">${escapeHtml(name)}</span>
      <span class="row">${segmentBar(c, c.all)}<span class="n">${c.ok + c.partial}/${c.all}</span></span></span></button>`;
  let html = item("all", t("app.allPlaylists"), all, `<span class="mosaic all">${icon("music", "sm")}</span>`);
  for (const pl of lists) html += item(pl.id, pl.name, countByStatus(pl.tracks.map((tr) => ({ tr }))), mosaic(pl));
  $("#playlists").innerHTML = html;
}

function mosaic(pl, cls = "") {
  const keys = pl.tracks.filter((tr) => tr.file).slice(0, 4).map((tr) => keyOf(pl, tr));
  const cells = [0, 1, 2, 3].map((i) => (keys[i]
    ? `<img src="/api/cover/${enc(keys[i])}" alt="" loading="lazy" data-fallback="cell">`
    : "<i></i>")).join("");
  return `<span class="mosaic ${cls}" aria-hidden="true">${cells}</span>`;
}

function renderActivity() {
  const jobs = Object.values(state.jobs).sort((a, b) => b.created - a.created);
  const running = runningJob();
  const queued = jobs.filter((j) => j.state === "queued");
  let html = `<div class="panel-head"><h2 class="label">${te("dock.activity")}</h2></div>`;
  if (running) {
    const pct = running.total ? Math.round((running.done / running.total) * 100) : 0;
    html += `<div class="job-name">${escapeHtml(jobLabel(running))}</div>
      <div class="job-current">${escapeHtml(running.current || t("dock.preparing"))}</div>
      <div class="row"><div class="progress"><i style="width:${running.total ? pct : 4}%"></i></div>
        <span class="num dim">${running.done}/${running.total || "?"}</span>
        <button class="btn sm" type="button" data-cancel="${escapeHtml(running.id)}">${te("dock.cancel")}</button></div>`;
  } else {
    html += `<div class="idle">${te(queued.length ? "dock.starting" : "dock.idle")}</div>`;
    if (state.lastJob) html += `<div class="result">${escapeHtml(jobResult(state.lastJob).text)}</div>`;
  }
  html += automationHtml();
  if (queued.length) {
    html += `<div class="queue">${te("dock.queued", { n: queued.length })}: ${queued.map((j) => escapeHtml(jobLabel(j))).join(" · ")}</div>`;
  }
  $("#activity").innerHTML = html;
}

function relativeTime(seconds) {
  if (seconds <= 60) return t("auto.soon");
  const minutes = Math.round(seconds / 60);
  return minutes < 90 ? t("auto.inMinutes", { n: minutes }) : t("auto.inHours", { n: Math.round(minutes / 60) });
}

function automationHtml() {
  const s = state.library.settings;
  if (!state.library.playlists.length) return "";
  const line = (on, text) => `<li class="${on ? "on" : "off"}"><i></i>${escapeHtml(text)}</li>`;
  let sync = t("auto.syncOff");
  if (s.auto_sync_hours > 0) {
    const next = Math.min(...state.library.playlists.map((p) => (p.synced || p.added || 0) + s.auto_sync_hours * 3600));
    sync = t("auto.sync", { hours: s.auto_sync_hours, when: relativeTime(next - Date.now() / 1000) });
  }
  return `<ul class="auto" title="${te("auto.title")}">
    ${line(s.auto_check, t(s.auto_check ? "auto.check" : "auto.checkOff"))}
    ${line(s.auto_sync_hours > 0, sync)}
    ${s.auto_sync_hours > 0 ? line(s.auto_download_new, t(s.auto_download_new ? "auto.download" : "auto.downloadOff")) : ""}
  </ul>`;
}

function renderHealth() {
  const c = countByStatus(allTracks());
  const max = Math.max(c.all, 1);
  const line = (status, value) => `<button class="line ${STATUS_CLASS[status]}" type="button" data-health="${status}">
      <span class="caption">${escapeHtml(statusLabel(status))}</span>
      <span class="bar"><i style="width:${(value / max) * 100}%"></i></span><span class="n">${value}</span></button>`;
  $("#health").innerHTML = `<div class="panel-head"><h2 class="label">${te("health.title")}</h2></div>
    ${STAT_CARDS.map((s) => line(s, s === "missing" ? c.missingAll : c[s])).join("")}
    <div class="saved">${state.library.saved_at ? te("health.saved", { time: fmtClock(state.library.saved_at) }) : te("health.notSaved")}</div>`;
}

function renderQuality() {
  const files = allTracks().filter(({ tr }) => tr.file);
  const counts = { high: 0, good: 0, standard: 0, low: 0, unknown: 0 };
  files.forEach(({ tr }) => {
    const q = qualityOf(tr.check);
    counts[q === "full" ? "high" : q || "unknown"] += 1;
  });
  const tags = files.filter(({ tr }) => ((tr.check || {}).tags_missing || []).length).length;
  const twins = allTracks().filter(({ tr }) => twinCount(tr.id) > 1).length;
  const max = Math.max(files.length, 1);
  const line = (key, n, cls, finding) => `<button class="line ${cls}" type="button" ${finding ? `data-finding="${finding}" title="${te("quality.lowFilter")}"` : "disabled"}>
      <span class="caption">${te(`quality.${key}`)}</span><span class="bar"><i style="width:${(n / max) * 100}%"></i></span><span class="n">${n}</span></button>`;
  const rows = ["high", "good", "standard", "low"].map((k) => line(k, counts[k], QUALITY_CLASS[k], k === "low" && counts.low ? "low_quality" : ""));
  if (counts.unknown) rows.push(line("unknown", counts.unknown, "s-unchecked", ""));
  $("#quality").innerHTML = `<div class="panel-head"><h2 class="label">${te("quality.title")}</h2></div>${rows.join("")}
    <div class="q-extra">
      <button class="q-link" type="button" data-finding="tags_missing" ${tags ? "" : "disabled"} title="${te("quality.tagsFilter")}"><span>${te("quality.tagsMissing")}</span><b>${tags}</b></button>
      ${tags ? `<button class="btn sm" type="button" data-action="fix-tags" data-scope="all" title="${te("quality.fixTitle")}">${icon("tag", "sm")}${te("quality.fix")}</button>` : ""}
      <button class="q-link" type="button" data-finding="duplicate" ${twins ? "" : "disabled"} title="${te("quality.dupFilter")}"><span>${te("quality.duplicates")}</span><b>${twins}</b></button>
    </div>`;
}

function renderLog() {
  const names = new Map(allTracks().map(({ tr }) => [tr.id, `${tr.artist} – ${tr.name}`]));
  const el = $("#log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  el.innerHTML = state.logs.slice(-150).map((l) => `<div class="log-line ${escapeHtml(l.level)}"><time>${fmtClock(l.t)}</time><span class="msg">${escapeHtml(logText(l, names))}</span></div>`).join("")
    || `<p class="log-empty">${te("dock.empty")}</p>`;
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function stateCell(tr) {
  if (tr.step) return `<span class="state s-run"><span class="spinner"></span>${te(`step.${tr.step}`)}</span>`;
  const status = statusOf(tr);
  const flag = tr.proposal ? `<span class="flag" title="${te("status.proposalTitle")}">${te("status.proposal")}</span>` : "";
  return `<span class="state ${STATUS_CLASS[status]}">${escapeHtml(statusLabel(status))}</span>${flag}`;
}

function lengthCell(tr) {
  const check = tr.check || {};
  if (!tr.file || check.file_len == null) return `<span class="tm">${fmtTime(tr.duration)}<small>${escapeHtml(referenceLabel(tr))}</small></span>`;
  let delta = "";
  if (check.diff != null && Math.abs(check.diff) >= 1) {
    const cls = Math.abs(check.diff) > 8 ? "bad" : Math.abs(check.diff) > 3 ? "warn" : "minor";
    delta = `<span class="delta ${cls}" title="${te("cell.deltaTitle")}">${fmtSigned(check.diff)}</span>`;
  }
  return `<span class="tm">${fmtTime(check.file_len)}${delta}<small>${te("cell.reference", { ref: referenceLabel(tr), time: fmtTime(tr.duration) })}</small></span>`;
}

function silenceCell(tr) {
  const check = tr.check || {};
  if (check.lead == null) return '<span class="dim">–</span>';
  const v = (value) => `<span class="${value >= 2 ? "warn-text" : ""}">${fmt(value)}</span>`;
  return `<span class="tm" title="${te("cell.silenceTitle")}">${v(check.lead)} / ${v(check.tail)} s<small>${te("cell.startEnd")}</small></span>`;
}

function matchCell(tr) {
  const verify = tr.verify || {};
  if (verify.verdict === "NO_PREVIEW") return `<span class="dim">${te("cell.noPreview")}</span>${qualityBadge(tr)}`;
  if (verify.ber == null) return `<span class="dim">–</span>${qualityBadge(tr)}`;
  const quality = Math.max(0, Math.min(1, (0.5 - verify.ber) / 0.5));
  return `<div class="meter ${VERDICT_CLASS[verify.verdict] || ""}" title="${te("cell.berTitle", { ber: fmtBer(verify.ber) })}">
    <div class="bar"><i style="width:${quality * 100}%"></i></div><span class="v">${fmtBer(verify.ber)}</span></div>${qualityBadge(tr)}`;
}

function linksCell(tr) {
  const verify = tr.verify || {};
  const link = (href, title, html) => `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" title="${escapeHtml(title)}">${html}</a>`;
  const links = [];
  if (tr.source && tr.source.url) links.push(link(tr.source.url, t("link.source", { name: sourceName(tr.source.url) }), sourceIcon(tr.source.url)));
  links.push(link(tr.url, t("link.origin", { origin: ORIGIN_NAMES[originOf(tr)] }), originIcon(originOf(tr))));
  if (verify.ref && verify.ref.link) links.push(link(verify.ref.link, t("link.preview", { src: verify.ref.src }), icon("music", "sm")));
  return `<div class="links">${links.join("")}</div>`;
}

function qualityBadge(tr) {
  const check = tr.check || {};
  const quality = tr.file ? qualityOf(check) : null;
  if (!quality) return "";
  const format = [check.codec, check.bitrate].filter(Boolean).join(" ");
  const range = check.cutoff == null ? t("quality.full") : `${fmt(check.cutoff)} kHz`;
  return `<small class="q ${QUALITY_CLASS[quality]}" title="${te(`quality.${quality}`)}: ${te(`quality.${quality}.title`)}">${escapeHtml(format)}${format ? " · " : ""}${escapeHtml(range)}</small>`;
}

function rowHtml({ pl, tr, i }) {
  const key = keyOf(pl, tr);
  const cover = tr.file
    ? `<img class="cover" loading="lazy" src="/api/cover/${enc(key)}" alt="" data-fallback="blank">`
    : '<span class="cover"></span>';
  const attention = tr.status === "bad" || tr.status === "review";
  const action = tr.file
    ? `<button class="icon-btn" type="button" data-track-action="fix" ${tr.step ? "disabled" : ""} title="${te(tr.step ? "track.busy" : "row.fix")}" aria-label="${te("row.fix")}" style="${attention ? "color:var(--accent-text)" : ""}">${icon("search", "sm")}</button>`
    : `<button class="icon-btn" type="button" data-track-action="download" ${tr.step ? "disabled" : ""} title="${te(tr.step ? "track.busy" : "row.download")}" aria-label="${te("row.download")}">${icon("download", "sm")}</button>`;
  const cls = ["row", state.openKey === key ? "sel" : "", tr.step ? "busy" : ""].join(" ");
  return `<tr class="${cls}" data-key="${escapeHtml(key)}">
    <td class="c-state">${stateCell(tr)}</td>
    <td class="c-no no">${String(i + 1).padStart(2, "0")}:</td>
    <td><div class="track">${cover}<div class="text"><span class="title" title="${escapeHtml(tr.name)}">${escapeHtml(tr.name)}</span>
      <span class="sub">${escapeHtml(tr.artist)}${tr.album ? ` · ${escapeHtml(tr.album)}` : ""}</span></div></div></td>
    <td class="c-len">${lengthCell(tr)}</td>
    <td class="c-sil">${silenceCell(tr)}</td>
    <td class="c-match">${matchCell(tr)}</td>
    <td class="c-src">${linksCell(tr)}</td>
    <td class="c-act">${action}</td></tr>`;
}

function filteredRows(scope) {
  const q = state.query.trim().toLowerCase();
  let rows = scope.filter(({ tr }) => {
    if (state.filter === "proposal") return Boolean(tr.proposal);
    if (state.filter === "missing") return tr.status === "missing" || tr.status === "unchecked";
    return state.filter === "all" || tr.status === state.filter;
  });
  if (state.finding) rows = rows.filter(({ tr }) => trackFindings(tr).includes(state.finding));
  if (q) rows = rows.filter(({ tr }) => `${tr.name} ${tr.artists.join(" ")} ${tr.album} ${tr.file || ""}`.toLowerCase().includes(q));
  const rank = { bad: 0, review: 1, missing: 2, unchecked: 3, partial: 4, ok: 5 };
  if (state.sort === "name") rows.sort((a, b) => a.tr.name.localeCompare(b.tr.name, locale()));
  else if (state.sort === "status") rows.sort((a, b) => (rank[a.tr.status] ?? 3) - (rank[b.tr.status] ?? 3));
  else if (state.sort === "ber") rows.sort((a, b) => ((b.tr.verify || {}).ber ?? -1) - ((a.tr.verify || {}).ber ?? -1));
  return rows;
}

function overviewHtml(scope, c) {
  const pl = selectedPlaylist();
  const counts = actionCounts(scope);
  const settings = state.library.settings;
  const iconButton = (action, key, name, extra = "") => `<button class="icon-btn" type="button" data-action="${action}" title="${te(key)}" aria-label="${te(key)}" ${extra}>${icon(name)}</button>`;
  const synced = pl && (pl.synced || pl.added);
  const meta = pl
    ? `${ORIGIN_NAMES[originOf(pl)]} · ${t(synced ? "overview.playlistMeta" : "overview.playlistMetaShort", { n: pl.tracks.length, folder: pl.folder, date: synced ? fmtDate(synced) : "" })}`
    : t("overview.projectMeta", { playlists: state.library.playlists.length, n: c.all, folder: state.project ? state.project.music : "" });
  const stat = (status) => {
    const value = status === "missing" ? c.missingAll : c[status];
    return `<button class="stat ${STATUS_CLASS[status]} ${state.filter === status ? "on" : ""} ${value ? "" : "zero"}" type="button" data-filter="${status}" title="${te("overview.filterBy", { status: statusLabel(status) })}">
      <span class="v">${value}</span><span class="k">${escapeHtml(statusLabel(status))}</span></button>`;
  };
  const why = (name) => {
    if (name === "trim" && !settings.trim_silence) return t("action.trim.off");
    return counts[name] ? t(`action.${name}.title`) : t(`action.${name}.none`);
  };
  const pillButton = (name, iconName) => `<button class="btn" type="button" data-preset="${name}" ${counts[name] ? "" : "disabled"} title="${escapeHtml(why(name))}">
      ${icon(iconName, "sm")}${te(`action.${name}`)}<span class="count">${counts[name]}</span></button>`;
  return `<section class="panel overview">
    <div class="ov-head">
      <div class="ov-title"><p class="label">${te(pl ? "overview.playlist" : "overview.project")}</p>
        <h1 title="${escapeHtml(pl ? pl.name : state.project?.name)}">${escapeHtml(pl ? pl.name : state.project?.name || "")}</h1>
        <div class="meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</div></div>
      <div class="ov-tools">
        ${iconButton("sync", pl ? "action.syncOne" : "action.syncAll", "refresh")}
        ${iconButton("folder", "action.folder", "folder")}
        ${iconButton("export", "action.export", "share")}
        ${pl ? iconButton("remove", "action.remove", "trash") : ""}
      </div>
    </div>
    <div class="stats">${STAT_CARDS.map(stat).join("")}</div>
    ${segmentBar(c, c.all, "big")}
    <div class="actions-row">
      <button class="run-btn" type="button" data-preset="all" ${counts.all ? "" : "disabled"} title="${escapeHtml(why("all"))}">
        <span class="disc">${icon("play")}</span><span class="txt"><span>${te("action.all")}</span><small>${te("overview.tracks", { n: counts.all })}</small></span></button>
      <div class="pill-group" role="group" aria-label="${te("overview.actions")}">
        ${pillButton("missing", "download")}${pillButton("check", "check")}${pillButton("problems", "wrench")}${pillButton("trim", "cut")}
      </div>
    </div>
    <p class="scope-note">${th("overview.scope", { n: c.all, scope: scopeName() })}${busy() ? ` ${te("overview.queuedNote")}` : ""}</p>
  </section>`;
}

function findingCounts(scope) {
  const counts = new Map();
  scope.forEach(({ tr }) => new Set(trackFindings(tr)).forEach((code) => counts.set(code, (counts.get(code) || 0) + 1)));
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

const filtersActive = () => state.filter !== "all" || Boolean(state.finding) || Boolean(state.query.trim());

function viewSwitchHtml() {
  if (selectedPlaylist()) return "";
  const option = (view, iconName) => `<button class="seg-btn ${state.view === view ? "on" : ""}" type="button" data-view="${view}" aria-pressed="${state.view === view}">${icon(iconName, "sm")}${te(`view.${view}`)}</button>`;
  return `<div class="seg-switch" role="group" aria-label="${te("view.label")}">${option("playlists", "folder")}${option("tracks", "music")}</div>`;
}

function tracksHtml(scope, c) {
  const rows = filteredRows(scope);
  const tab = (f, label, n, cls = "") => `<button class="tab ${cls} ${state.filter === f ? "on" : ""}" type="button" data-filter="${f}">${cls ? '<span class="dot"></span>' : ""}${escapeHtml(label)}<b>${n}</b></button>`;
  const tabs = [tab("all", t("filter.all"), c.all),
    ...STAT_CARDS.map((st) => tab(st, statusLabel(st), st === "missing" ? c.missingAll : c[st], STATUS_CLASS[st])),
    ...(c.proposal ? [tab("proposal", t("filter.proposal"), c.proposal, "s-run")] : [])].join("");
  const sortOptions = ["order", "name", "status", "ber"].map((v) => `<option value="${v}" ${state.sort === v ? "selected" : ""}>${te(`sort.${v}`)}</option>`).join("");
  const findings = findingCounts(scope);
  if (state.finding && !findings.some(([code]) => code === state.finding)) findings.push([state.finding, 0]);
  const findingOptions = `<option value="">${te("filter.anyFinding")}</option>`
    + findings.map(([code, n]) => `<option value="${code}" ${state.finding === code ? "selected" : ""}>${te(`finding.${code}`)} (${n})</option>`).join("");
  const body = rows.length ? rows.map(rowHtml).join("")
    : `<tr class="empty-row"><td colspan="8">${te(state.query ? "empty.noSearch" : "empty.noMatch")}${filtersActive() ? ` <button class="btn sm" type="button" data-action="clear-filters">${te("filter.clear")}</button>` : ""}</td></tr>`;
  let active = "";
  if (filtersActive()) {
    const parts = [];
    if (state.filter !== "all") parts.push(state.filter === "proposal" ? t("filter.proposal") : statusLabel(state.filter));
    if (state.finding) parts.push(t(`finding.${state.finding}`));
    if (state.query.trim()) parts.push(`„${state.query.trim()}“`);
    active = `<div class="filterbar"><span>${te("filter.active")}: <b>${parts.map(escapeHtml).join(" · ")}</b> · ${te("footnote.filtered", { shown: rows.length, total: scope.length })}</span>
      <button class="btn sm" type="button" data-action="clear-filters">${icon("close", "sm")}${te("filter.clear")}</button></div>`;
  }
  return `<section class="panel tracks-panel">
    <div class="tabs" role="tablist">${viewSwitchHtml()}${tabs}
      <span class="tabs-end">
        <label class="sr-only" for="finding">${te("filter.finding")}</label><select id="finding" class="sort" title="${te("filter.findingTitle")}">${findingOptions}</select>
        <label class="sr-only" for="sort">${te("sort.label")}</label><select id="sort" class="sort">${sortOptions}</select></span></div>
    ${active}
    <div class="table-wrap"><table class="tracks">
      <thead><tr><th class="c-state">${te("col.status")}</th><th class="c-no">#</th><th>${te("col.track")}</th><th class="c-len">${te("col.length")}</th>
        <th class="c-sil">${te("col.silence")}</th><th class="c-match">${te("col.match")}</th><th class="c-src">${te("col.source")}</th><th class="c-act"><span class="sr-only">${te("col.actions")}</span></th></tr></thead>
      <tbody>${body}</tbody></table></div>
    <p class="footnote">${te(filtersActive() ? "footnote.filtered" : "footnote.all", { shown: rows.length, total: scope.length })}</p>
  </section>`;
}

function playlistsHtml() {
  const rank = (pl) => {
    const c = countByStatus(pl.tracks.map((tr) => ({ tr })));
    return -(c.bad * 1000 + c.review * 100 + c.missingAll * 10 + c.proposal);
  };
  const lists = [...state.library.playlists];
  if (state.plSort === "name") lists.sort((a, b) => a.name.localeCompare(b.name, locale()));
  else if (state.plSort === "problems") lists.sort((a, b) => rank(a) - rank(b));
  else if (state.plSort === "updated") lists.sort((a, b) => (b.synced || b.added || 0) - (a.synced || a.added || 0));
  const sortOptions = ["name", "problems", "updated"].map((v) => `<option value="${v}" ${state.plSort === v ? "selected" : ""}>${te(`plsort.${v}`)}</option>`).join("");
  const card = (pl) => {
    const c = countByStatus(pl.tracks.map((tr) => ({ tr })));
    const updated = pl.synced || pl.added;
    const chips = STAT_CARDS.map((st) => {
      const n = st === "missing" ? c.missingAll : c[st];
      return n ? `<span class="mini-stat ${STATUS_CLASS[st]}" title="${escapeHtml(statusLabel(st))}"><i></i>${n}</span>` : "";
    }).join("");
    const attention = c.bad + c.review;
    return `<article class="pl-card ${attention ? "attention" : ""}" data-playlist="${escapeHtml(pl.id)}" tabindex="0" role="button" aria-label="${escapeHtml(pl.name)}">
      ${mosaic(pl, "big")}
      <div class="pl-card-body">
        <h3 title="${escapeHtml(pl.name)}">${escapeHtml(pl.name)}</h3>
        <div class="meta"><span class="origin" title="${escapeHtml(ORIGIN_NAMES[originOf(pl)])}">${originIcon(originOf(pl))}</span>${te("plcard.meta", { n: c.all })}</div>
        ${segmentBar(c, c.all)}
        <div class="minis">${chips}${c.proposal ? `<span class="flag">${te("status.proposal")} ${c.proposal}</span>` : ""}</div>
        <div class="meta dim" title="${updated ? escapeHtml(fmtDate(updated)) : ""}">${updated ? te("plcard.updated", { date: fmtShortDate(updated) }) : te("plcard.never")}</div>
      </div>
    </article>`;
  };
  return `<section class="panel tracks-panel">
    <div class="tabs">${viewSwitchHtml()}<span class="tabs-end">
      <label class="sr-only" for="pl-sort">${te("sort.label")}</label><select id="pl-sort" class="sort">${sortOptions}</select></span></div>
    <div class="pl-grid">${lists.map(card).join("")}
      <button class="pl-card add" type="button" data-action="add">${icon("plus")}<span>${te("app.addPlaylist")}</span></button></div>
    <p class="footnote">${te("plcard.footnote", { n: lists.length })}</p>
  </section>`;
}

function renderMain() {
  const main = $("#main");
  const tableScroll = $(".table-wrap", main)?.scrollTop || 0;
  if (!state.library.playlists.length) {
    main.innerHTML = `<section class="panel welcome"><button class="dropzone" type="button" data-action="add">
      <h2>${te("empty.title")}</h2><p>${te("empty.text")}</p><p class="cta">${te("empty.cta")}</p></button></section>`;
    return;
  }
  const scope = scopeTracks();
  const c = countByStatus(scope);
  const showPlaylists = !selectedPlaylist() && state.view === "playlists" && !filtersActive();
  main.innerHTML = overviewHtml(scope, c) + (showPlaylists ? playlistsHtml() : tracksHtml(scope, c));
  const wrap = $(".table-wrap", main);
  if (wrap) wrap.scrollTop = tableScroll;
  rememberView();
}

function renderAll() {
  renderTopbar();
  renderBanner();
  renderPlaylists();
  renderMain();
  renderActivity();
  renderHealth();
  renderQuality();
  renderLog();
  if (state.openKey) renderDrawer(true);
}

let frame = 0;
function scheduleRender() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    renderTopbar();
    renderPlaylists();
    renderMain();
    renderActivity();
    renderHealth();
    renderQuality();
  });
}

function matchChart(verify, settings) {
  const curve = verify.curve || [];
  if (!curve.length) return "";
  const W = Math.round(Math.max(300, Math.min(window.innerWidth - 24, 640) - 64));
  const H = 170, left = 34, right = 92, top = 12, bottom = 24;
  const plotW = W - left - right, plotH = H - top - bottom;
  const x = (i) => left + (i / Math.max(curve.length - 1, 1)) * plotW;
  const y = (b) => top + (1 - Math.min(b, 0.5) / 0.5) * plotH;
  const points = curve.map((b, i) => `${x(i).toFixed(1)},${y(b).toFixed(1)}`).join(" ");
  const best = curve.indexOf(Math.min(...curve));
  const span = Math.max((verify.file_len || 0) - (verify.window || 0), 0);
  const threshold = (value, label, color) => `
    <line x1="${left}" x2="${left + plotW}" y1="${y(value)}" y2="${y(value)}" stroke="${color}" stroke-dasharray="4 4"/>
    <text x="${left + plotW + 6}" y="${y(value) + 3}">${te(label)} ${fmtBer(value)}</text>`;
  return `<svg class="chart ${VERDICT_CLASS[verify.verdict] || ""}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${te("chart.label")}">
    <line x1="${left}" x2="${left}" y1="${top}" y2="${top + plotH}" stroke="var(--line-strong)"/>
    <line x1="${left}" x2="${left + plotW}" y1="${top + plotH}" y2="${top + plotH}" stroke="var(--line-strong)"/>
    <text x="${left - 6}" y="${y(0.5) + 3}" text-anchor="end">${fmt(0.5)}</text>
    <text x="${left - 6}" y="${y(0) + 3}" text-anchor="end">0</text>
    ${threshold(settings.accept_ber, "chart.match", "var(--good)")}
    ${threshold(settings.unsure_ber, "chart.unsure", "var(--warn)")}
    <polyline points="${points}" fill="none" stroke="var(--text-mid)" stroke-width="1.4" vector-effect="non-scaling-stroke"/>
    <line x1="${x(best)}" x2="${x(best)}" y1="${top}" y2="${top + plotH}" stroke="var(--accent)" stroke-dasharray="2 3"/>
    <circle cx="${x(best)}" cy="${y(curve[best])}" r="5" fill="var(--accent)" stroke="var(--bg)" stroke-width="2"/>
    <text x="${left}" y="${H - 6}">0:00</text>
    <text x="${left + plotW}" y="${H - 6}" text-anchor="end">${fmtTime(span)}</text>
    <text x="${x(best)}" y="${H - 6}" text-anchor="middle" style="fill:var(--accent-text)">${fmtTime(verify.at)}</text>
  </svg>`;
}

function timelineHtml(tr) {
  const check = tr.check;
  const max = Math.max(check.file_len, tr.duration) || 1;
  const pct = (value) => `${((value / max) * 100).toFixed(2)}%`;
  return `
    <div class="caption">${te("timeline.reference", { ref: referenceLabel(tr), time: fmtTime(tr.duration) })}</div>
    <div class="timeline"><i class="s-partial" style="left:0;width:${pct(tr.duration)}"></i></div>
    <div class="caption">${te("timeline.file", { time: fmtTime(check.file_len) })}</div>
    <div class="timeline"><i class="s-ok" style="left:0;width:${pct(check.file_len)}"></i>
      ${check.lead > 0 ? `<i class="s-review" style="left:0;width:${pct(check.lead)}"></i>` : ""}
      ${check.tail > 0 ? `<i class="s-review" style="left:${pct(check.file_len - check.tail)};width:${pct(check.tail)}"></i>` : ""}</div>
    <div class="legend"><span class="s-partial">${te("legend.reference")}</span><span class="s-ok">${te("legend.file")}</span><span class="s-review">${te("legend.silence")}</span></div>`;
}

function player(label, sub, query, key) {
  return `<div class="player"><div class="head"><span>${escapeHtml(label)}</span><span class="dim">${escapeHtml(sub)}</span></div>
    <audio controls preload="none" src="/api/audio/${enc(key)}${query}"></audio></div>`;
}

function renderDrawer(keepPlayback) {
  const found = findTrack(state.openKey);
  const drawer = $("#drawer");
  if (!found) { closeDrawer(); return; }
  const { pl, tr } = found;
  const key = state.openKey;
  const check = tr.check || {};
  const verify = tr.verify || {};
  const settings = state.library.settings;
  const sameTrack = drawer.dataset.key === key;
  if (keepPlayback && sameTrack && $$("audio", drawer).some((a) => !a.paused)) {
    $("#drawer-state", drawer).innerHTML = drawerPill(tr);
    return;
  }
  const scroll = sameTrack ? drawer.scrollTop : 0;
  drawer.dataset.key = key;
  const hasFile = Boolean(tr.file);
  const ref = verify.ref;
  const proposal = tr.proposal;
  const isBusy = Boolean(tr.step);
  const disabled = isBusy ? `disabled title="${te("track.busy")}"` : "";
  const external = (href, html) => `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${html}</a>`;
  const section = (labelKey, body, cls = "") => `<section class="panel section ${cls}"><h3 class="label">${te(labelKey)}</h3>${body}</section>`;

  let proposalSection = "";
  if (proposal) {
    let meta = t("proposal.meta", { source: proposal.source || sourceName(proposal.url), channel: proposal.channel, diff: fmtSigned(proposal.diff) });
    if (proposal.ber != null) meta += t("proposal.ber", { ber: fmtBer(proposal.ber), current: verify.ber != null ? fmtBer(verify.ber) : "–" });
    proposalSection = section("section.proposal", `
      <p style="margin:0 0 12px"><b>${escapeHtml(proposal.title)}</b><br><span class="dim mono" style="font-size:12px">${escapeHtml(meta)}</span></p>
      <div class="players">
        ${proposal.audio ? player(t("player.proposalMatch"), t("player.from", { time: fmtTime(proposal.at) }), "?src=proposal_match", key) : ""}
        ${verify.ber != null ? player(t("player.currentMatch"), t("player.from", { time: fmtTime(verify.at) }), "?src=match", key) : ""}
        ${ref ? player(t("player.preview", { src: ref.src }), "", "?src=preview", key) : ""}
      </div>
      <div class="d-actions" style="margin-top:12px">
        <button class="btn primary" type="button" data-action="apply-proposal" ${disabled}>${icon("check", "sm")}${te("action.applyProposal")}</button>
        <button class="btn" type="button" data-action="reject-proposal" ${disabled}>${icon("close", "sm")}${te("action.reject")}</button>
        <a class="btn soft" href="${escapeHtml(proposal.page || proposal.url)}" target="_blank" rel="noopener">${sourceIcon(proposal.url)}${te("action.openSource")}</a>
      </div>
      <p class="hint">${te("proposal.hint")}</p>`, "highlight");
  }

  const listening = hasFile ? section("section.listen", `<div class="players">
      ${player(t("player.start"), t("player.first30"), "?src=start", key)}
      ${verify.ber != null && !proposal ? player(t("player.match"), t("player.from", { time: fmtTime(verify.at) }), "?src=match", key)
        + player(t("player.preview", { src: ref ? ref.src : "" }), ref ? `${ref.artist} – ${ref.title}` : "", "?src=preview", key) : ""}
      ${player(t("player.full"), "", "", key)}
    </div>`) : "";

  const chart = verify.curve ? section("section.match", `${matchChart(verify, settings)}
      <p class="hint">${th("match.hint", { ber: fmtBer(verify.ber), time: fmtTime(verify.at) })}</p>`) : "";

  const fact = (label, value, cls = "") => `<dt>${te(label)}</dt><dd class="${cls}">${value}</dd>`;
  let integrity = "–";
  if (check.decode_errors != null) {
    integrity = check.decode_errors ? `<span class="bad-text">${te("fact.integrityErrors", { n: check.decode_errors })}</span>` : te("fact.integrityOk");
  }
  const quality = qualityOf(check);
  const tagsMissing = check.tags_missing || [];
  const qualitySection = hasFile && check.file_len != null ? section("section.quality", `<dl class="facts">
      ${fact("fact.format", check.codec ? te("fact.formatValue", { codec: check.codec, kbps: check.bitrate ?? "–" }) : "–")}
      ${fact("fact.range", quality ? escapeHtml(rangeText(check)) : te("quality.unknown"))}
      ${fact("fact.quality", quality ? `<span class="pill ${QUALITY_CLASS[quality]}">${te(`quality.${quality}`)}</span> <span class="dim">${te(`quality.${quality}.title`)}</span>` : `<span class="dim">${te("quality.unknown.title")}</span>`, "text")}
      ${check.tags_missing ? fact("fact.tags", tagsMissing.length ? te("fact.tagsMissing", { fields: tagsMissing.map((f) => t(`tag.${f}`)).join(", ") }) : te("fact.tagsOk"), "text") : ""}
    </dl><p class="hint">${te("quality.hint")}</p>`) : "";
  const lengths = check.file_len != null ? section("section.length", `${timelineHtml(tr)}
      <dl class="facts">
        ${fact("fact.diff", `${fmtSigned(check.diff)} s`)}
        ${fact("fact.silence", `${fmt(check.lead)} s / ${fmt(check.tail)} s`)}
        ${fact("fact.extra", `${fmtSigned(check.extra)} s`)}
        ${fact("fact.volume", check.head_db == null ? "–" : `${fmtSigned(check.head_db)} dB`)}
        ${fact("fact.integrity", integrity)}
        ${tr.trimmed ? fact("fact.trimmed", te("fact.trimmedValue", { start: fmt(tr.trimmed.start), end: fmt(tr.trimmed.end) })) : ""}
      </dl>`) : "";

  const notes = (tr.notes || []).length ? section("section.notes", `<ul class="notes">${tr.notes
    .map((n) => `<li class="${NOTE_CLASS[n.code] || "s-partial"}">${escapeHtml(noteText(n))}</li>`).join("")}</ul>`) : "";

  const candidates = (tr.candidates || []).length ? section("section.candidates", `<div class="table-scroll"><table class="mini">
      <thead><tr><th>${te("cand.candidate")}</th><th>${te("cand.source")}</th><th>${te("cand.diff")}</th><th>${te("cand.ber")}</th></tr></thead><tbody>
      ${tr.candidates.map((c) => `<tr><td class="wide">${external(c.page || c.url, escapeHtml(c.title))}<br>
        <span class="dim">${escapeHtml(c.channel)}${c.error ? ` · ${te(`cand.${c.error}`)}` : ""}</span></td>
        <td class="dim">${escapeHtml(c.source || sourceName(c.url))}</td>
        <td class="num">${fmtSigned(c.diff)} s</td><td class="num">${c.ber == null ? "–" : fmtBer(c.ber)}</td></tr>`).join("")}
      </tbody></table></div>`) : "";

  const backups = (tr.backups || []).length ? section("section.backups", `<div class="table-scroll"><table class="mini">
      <thead><tr><th>${te("backup.reason")}</th><th>${te("backup.length")}</th><th>${te("backup.ber")}</th><th>${te("backup.listen")}</th><th></th></tr></thead><tbody>
      ${tr.backups.map((b, i) => `<tr><td>${te(`reason.${b.reason}`)}<br><span class="dim mono" style="font-size:12px">${escapeHtml(fmtDate(b.t))}</span></td>
        <td class="num">${b.file_len == null ? "–" : fmtTime(b.file_len)}</td><td class="num">${b.ber == null ? "–" : fmtBer(b.ber)}</td>
        <td style="min-width:160px"><audio controls preload="none" src="/api/audio/${enc(key)}?src=backup&index=${i}"></audio></td>
        <td><button class="btn sm" type="button" data-action="restore" data-index="${i}" ${disabled}>${icon("undo", "sm")}${te("action.restore")}</button></td></tr>`).join("")}
      </tbody></table></div><p class="hint">${te("backup.hint")}</p>`) : "";

  const twins = state.library.playlists.filter((other) => other.id !== pl.id && other.tracks.some((x) => x.id === tr.id)).map((other) => other.name);
  const sourceLink = tr.source && tr.source.url
    ? fact("fact.source", `${external(tr.source.url, escapeHtml(tr.source.url))} <span class="dim">(${te(`via.${tr.source.via}`)})</span>`) : "";
  const previewLink = ref && ref.link ? fact("fact.preview", external(ref.link, escapeHtml(`${ref.src}: ${ref.artist} – ${ref.title}`))) : "";

  const actions = hasFile
    ? `<button class="btn primary" type="button" data-track-action="fix" ${disabled} title="${te("track.fixTitle")}">${icon("search", "sm")}${te("action.findBetter")}</button>
       <button class="btn" type="button" data-track-action="recheck" ${disabled} title="${te("track.recheckTitle")}">${icon("check", "sm")}${te("action.recheck")}</button>
       <button class="btn" type="button" data-track-action="trim" ${disabled || (settings.trim_silence ? "" : `disabled title="${te("action.trim.off")}"`)}>${icon("cut", "sm")}${te("action.trimTrack")}</button>
       ${tagsMissing.length ? `<button class="btn" type="button" data-action="fix-tags" data-scope="track" ${disabled} title="${te("action.fixTags.title")}">${icon("tag", "sm")}${te("action.fixTags")}</button>` : ""}
       <button class="btn soft" type="button" data-action="reveal">${icon("folder", "sm")}${te("action.reveal")}</button>`
    : `<button class="btn primary" type="button" data-track-action="download" ${disabled}>${icon("download", "sm")}${te("action.downloadVerify")}</button>`;

  drawer.innerHTML = `
    <section class="panel"><div class="d-head">
      ${hasFile ? `<img class="cover" src="/api/cover/${enc(key)}" alt="" data-fallback="blank">` : '<span class="cover"></span>'}
      <div class="info"><p class="label">${escapeHtml(pl.name)}</p><h2>${escapeHtml(tr.name)}</h2>
        <div class="by">${escapeHtml(tr.artists.join(", "))}</div>
        <div class="album">${escapeHtml(tr.album || "")}${tr.year ? ` · ${escapeHtml(tr.year)}` : ""}</div>
        <span id="drawer-state">${drawerPill(tr)}</span></div>
      <button class="icon-btn" type="button" data-action="close-drawer" aria-label="${te("drawer.close")}" title="${te("drawer.close")}">${icon("close")}</button>
    </div><div class="d-actions" style="margin-top:14px">${actions}</div></section>
    ${proposalSection}${notes}${listening}${chart}${lengths}${qualitySection}${candidates}${backups}
    ${section("section.links", `<dl class="facts">
      ${fact(`origin.${originOf(tr)}`, external(tr.url, escapeHtml(tr.url)))}
      ${tr.official && tr.official.link && tr.official.link !== (ref || {}).link ? fact("fact.official", external(tr.official.link, escapeHtml(`Deezer: ${tr.official.artist} – ${tr.official.title}`))) : ""}
      ${sourceLink}${previewLink}
      ${twins.length ? fact("fact.alsoIn", escapeHtml(twins.join(", ")), "text") : ""}
      ${fact("fact.file", escapeHtml(tr.file || t("fact.notDownloaded")))}</dl>`)}`;
  drawer.scrollTop = scroll;
  $("#scrim").classList.add("on");
  drawer.classList.add("on");
}

function drawerPill(tr) {
  if (tr.step) return `<span class="pill s-run"><span class="spinner"></span>${te(`step.${tr.step}`)}</span>`;
  const status = statusOf(tr);
  return `<span class="pill ${STATUS_CLASS[status]}">${escapeHtml(statusLabel(status))}</span>${tr.proposal ? ` <span class="flag">${te("status.proposal")}</span>` : ""}`;
}

function openDrawer(key) {
  state.openKey = key;
  history.replaceState(null, "", `#track=${enc(key)}`);
  renderDrawer(false);
  $$("tr.row.sel").forEach((r) => r.classList.remove("sel"));
  $$("tr.row").find((r) => r.dataset.key === key)?.classList.add("sel");
}

function closeDrawer() {
  state.openKey = null;
  const drawer = $("#drawer");
  drawer.classList.remove("on");
  delete drawer.dataset.key;
  $("#scrim").classList.remove("on");
  $$("#drawer audio").forEach((a) => a.pause());
  $$("tr.row.sel").forEach((r) => r.classList.remove("sel"));
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
}

function exportCsv() {
  const separator = currentLanguage === "cs" ? ";" : ",";
  const number = new Intl.NumberFormat(locale(), { maximumFractionDigits: 3, useGrouping: false });
  const num = (value) => (value == null ? "" : number.format(value));
  const cell = (value) => {
    const text = String(value ?? "");
    return /["\n\r]/.test(text) || text.includes(separator) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const columns = ["playlist", "artist", "title", "album", "spotifyLength", "fileLength", "leadSilence", "tailSilence",
    "ber", "verdict", "status", "format", "cutoff", "source", "file", "notes"];
  const lines = [columns.map((c) => cell(t(`csv.${c}`))).join(separator)];
  for (const { pl, tr } of scopeTracks()) {
    const check = tr.check || {};
    const verify = tr.verify || {};
    lines.push([
      pl.name, tr.artist, tr.name, tr.album, num(tr.duration), num(check.file_len), num(check.lead), num(check.tail),
      num(verify.ber), verify.verdict || "", statusLabel(statusOf(tr)), [check.codec, check.bitrate].filter(Boolean).join(" "),
      check.cutoff_measured ? (check.cutoff == null ? "full" : num(check.cutoff)) : "", (tr.source || {}).url || "", tr.file || "",
      (tr.notes || []).map(noteText).join(" | "),
    ].map(cell).join(separator));
  }
  const name = (selectedPlaylist()?.name || state.project?.name || "spotcheck").replace(/[<>:"/\\|?*]+/g, "").trim();
  download(`${name}.csv`, `﻿${lines.join("\r\n")}\r\n`, "text/csv;charset=utf-8");
  say(t("result.exported", { n: lines.length - 1 }));
}

function download(filename, content, type) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type }));
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

const PRESETS = {
  all: { flags: FULL, only: null, label: "process_all", confirm: true },
  missing: { flags: FULL, only: "missing", label: "download_missing", confirm: true },
  check: { flags: { validate: true, verify: true }, only: "files", label: "check", confirm: false },
  problems: { flags: { validate: true, verify: true, fix: true, trim: true }, only: "problems", label: "fix_problems", confirm: true },
  trim: { flags: { trim: true }, only: "files", label: "trim", confirm: true, trimOnly: true },
};
const TRACK_ACTIONS = {
  download: { flags: FULL, label: "track_download" },
  fix: { flags: { validate: true, verify: true, fix: true, force_fix: true }, label: "track_fix" },
  recheck: { flags: { validate: true, verify: true }, label: "track_check" },
  trim: { flags: { trim: true }, label: "track_trim" },
};

function changesList(preset) {
  const s = state.library.settings;
  const items = [];
  if (preset.flags.download) items.push(te("changes.download"));
  if (preset.flags.validate) items.push(te("changes.check"));
  if (preset.flags.fix) items.push(th(s.auto_replace ? "changes.auto" : "changes.propose"));
  if (preset.flags.trim) items.push(s.trim_silence ? th("changes.trim", { seconds: fmt(s.min_silence) }) : te("changes.trimOff"));
  return `<ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
}

async function runProcess(preset, { trackKeys = null } = {}) {
  const body = {
    flags: preset.flags, only: preset.only || null, label: preset.label, track_ids: trackKeys,
    playlist_id: trackKeys || state.selected === "all" ? null : state.selected,
  };
  try {
    await api("/api/process", "POST", body);
    say(t(busy() ? "toast.queuedAfter" : "toast.started"));
  } catch (error) {
    sayError(error);
  }
}

async function onPreset(name) {
  const preset = PRESETS[name];
  const count = actionCounts(scopeTracks())[name];
  if (!count) return;
  if (preset.confirm) {
    const html = para(te(`action.${name}.what`)) + para(th("confirm.scope", { n: count, scope: scopeName() }))
      + changesList(preset) + para(te("changes.undo")) + (busy() ? para(te("overview.queuedNote")) : "");
    if (!await confirmDialog(t(`action.${name}`), html, t("confirm.start"))) return;
  }
  runProcess(preset);
}

async function onTrackAction(name, key) {
  const found = findTrack(key);
  if (!found || found.tr.step) return;
  const preset = TRACK_ACTIONS[name];
  const s = state.library.settings;
  if (name === "trim") {
    const html = para(te("confirm.trimTrack")) + para(th("changes.trim", { seconds: fmt(s.min_silence) })) + para(te("changes.undo"));
    if (!await confirmDialog(t("action.trimTrack"), html, t("confirm.trimOk"))) return;
  }
  if (name === "fix" && s.auto_replace) {
    if (!await confirmDialog(t("action.findBetter"), para(th("changes.auto")) + para(te("changes.undo")))) return;
  }
  runProcess(preset, { trackKeys: [key] });
}

async function onAction(name, el) {
  const key = state.openKey;
  try {
    switch (name) {
      case "add": openAddDialog(); break;
      case "sync": openSyncDialog(); break;
      case "deps": openSettings("deps"); break;
      case "close-drawer": closeDrawer(); break;
      case "export": openExportDialog(); break;
      case "fix-tags": await fixTags(el.dataset.scope, key); break;
      case "clear-filters":
        state.filter = "all";
        state.finding = "";
        state.query = "";
        $("#search").value = "";
        renderMain();
        break;
      case "remove": {
        const pl = selectedPlaylist();
        if (!pl) break;
        const html = para(th("confirm.remove.text", { name: pl.name })) + para(te("confirm.remove.files"));
        if (await confirmDialog(t("confirm.remove.title"), html, t("confirm.remove.ok"), true)) {
          await api(`/api/playlists/${enc(pl.id)}`, "DELETE");
          state.selected = "all";
          say(t("toast.removed", { name: pl.name }));
        }
        break;
      }
      case "folder":
        await api("/api/open", "POST", state.selected === "all" ? {} : { playlist_id: state.selected });
        break;
      case "reveal":
        await api("/api/open", "POST", { track_id: key });
        break;
      case "apply-proposal":
        if (await confirmDialog(t("confirm.apply.title"), para(te("confirm.apply.text")) + para(te("changes.undo")), t("confirm.apply.ok"))) {
          $$("#drawer audio").forEach((a) => a.pause());
          await api(`/api/tracks/${enc(key)}/proposal/apply`, "POST");
          say(t("toast.replacing"));
        }
        break;
      case "reject-proposal":
        await api(`/api/tracks/${enc(key)}/proposal/reject`, "POST");
        say(t("toast.rejected"));
        break;
      case "restore":
        if (await confirmDialog(t("confirm.restore.title"), para(te("confirm.restore.text")), t("confirm.restore.ok"))) {
          $$("#drawer audio").forEach((a) => a.pause());
          await api(`/api/tracks/${enc(key)}/restore`, "POST", { index: Number(el.dataset.index) });
          say(t("toast.restoring"));
        }
        break;
      default:
        break;
    }
  } catch (error) {
    sayError(error);
  }
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (target.closest("a") || target.closest("dialog")) return;
  const el = target.closest("[data-playlist],[data-filter],[data-health],[data-finding],[data-view],[data-preset],[data-track-action],[data-action],[data-cancel],tr.row");
  if (!el) {
    if (target.id === "scrim") closeDrawer();
    return;
  }
  if (el.dataset.playlist) { state.selected = el.dataset.playlist; renderAll(); return; }
  if (el.dataset.filter) {
    state.filter = state.filter === el.dataset.filter && el.classList.contains("stat") ? "all" : el.dataset.filter;
    if (state.filter !== "all") state.view = "tracks";
    renderMain();
    return;
  }
  if (el.dataset.health) { state.selected = "all"; state.filter = el.dataset.health; state.view = "tracks"; renderAll(); return; }
  if (el.dataset.view) { state.view = el.dataset.view; renderMain(); return; }
  if (el.dataset.finding) {
    Object.assign(state, { selected: "all", finding: el.dataset.finding, filter: "all", view: "tracks" });
    renderAll();
    return;
  }
  if (el.dataset.preset) { onPreset(el.dataset.preset); return; }
  if (el.dataset.trackAction) {
    event.stopPropagation();
    const key = el.closest("tr.row")?.dataset.key || state.openKey;
    if (key) onTrackAction(el.dataset.trackAction, key);
    return;
  }
  if (el.dataset.cancel) {
    api(`/api/jobs/${enc(el.dataset.cancel)}/cancel`, "POST").then(() => say(t("toast.cancelling"))).catch(sayError);
    return;
  }
  if (el.dataset.action) { onAction(el.dataset.action, el); return; }
  if (el.matches("tr.row")) openDrawer(el.dataset.key);
});

document.addEventListener("click", (event) => {
  const close = event.target.closest("[data-close]");
  if (close) close.closest("dialog").close();
});

$("#search").addEventListener("input", (event) => {
  state.query = event.target.value;
  if (state.query.trim()) state.view = "tracks";
  renderMain();
});
document.addEventListener("change", (event) => {
  if (event.target.id === "sort") { state.sort = event.target.value; renderMain(); }
  if (event.target.id === "finding") { state.finding = event.target.value; if (state.finding) state.view = "tracks"; renderMain(); }
  if (event.target.id === "pl-sort") { state.plSort = event.target.value; renderMain(); }
});
document.addEventListener("keydown", (event) => {
  const card = event.target.closest?.(".pl-card[data-playlist]");
  if (card && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); card.click(); }
});
document.addEventListener("keydown", (event) => {
  const typing = /^(input|textarea|select)$/i.test(document.activeElement.tagName);
  if (event.key === "/" && !typing && !document.querySelector("dialog[open]")) { event.preventDefault(); $("#search").focus(); }
  if (event.key === "Escape" && state.openKey && !document.querySelector("dialog[open]")) closeDrawer();
});
window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#track=(.+)$/);
  if (m) openDrawer(decodeURIComponent(m[1])); else if (state.openKey) closeDrawer();
});
$("#btn-theme").addEventListener("click", () => {
  const current = document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  const next = current === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("spotcheck-theme", next); } catch {}
});
$("#btn-lang").addEventListener("click", () => {
  setLanguage(currentLanguage === "cs" ? "en" : "cs");
  numberFormats = new Map();
  translatePage();
  if ($("#dlg-settings").open) renderSettings();
  if ($("#dlg-projects").open) renderProjects();
  if ($("#dlg-help").open) renderHelp();
  if ($("#dlg-add").open) checkAddUrls();
  if ($("#dlg-sync").open) renderSyncScope();
  if ($("#dlg-export").open) renderExportDialog();
  if ($("#dlg-terms").open) updateTermsLink();
  delete $("#drawer").dataset.key;
  renderAll();
});
$("#sysinfo").addEventListener("click", () => openSettings("deps"));
document.addEventListener("error", (event) => {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || !img.dataset.fallback) return;
  if (img.dataset.fallback === "cell") img.replaceWith(document.createElement("i"));
  else img.removeAttribute("src");
}, true);

let closed = false;
let eventSource = null;
$("#btn-quit").addEventListener("click", async () => {
  const html = para(te("quit.text")) + (busy() ? para(te("quit.busy")) : "");
  if (!await confirmDialog(t("quit.title"), html, t("quit.ok"), busy())) return;
  try {
    await api("/api/quit", "POST");
    closed = true;
    if (eventSource) eventSource.close();
    document.body.innerHTML = `<main class="closed"><h1>${te("quit.done")}</h1><p>${te("quit.doneText")}</p></main>`;
  } catch (error) {
    sayError(error);
  }
});
$("#btn-help").addEventListener("click", () => openHelp());

function updateTermsLink() {
  $("#terms-link").href = state.terms.urls[currentLanguage] || state.terms.urls.en;
}

function showTerms() {
  updateTermsLink();
  $("#terms-agree").checked = false;
  $("#terms-accept").disabled = true;
  if (!$("#dlg-terms").open) $("#dlg-terms").showModal();
}

$("#dlg-terms").addEventListener("cancel", (event) => event.preventDefault());
$("#dlg-terms").addEventListener("close", () => { if (!closed && !state.terms.accepted) showTerms(); });
$("#terms-lang").addEventListener("click", () => $("#btn-lang").click());
$("#terms-agree").addEventListener("change", (event) => { $("#terms-accept").disabled = !event.target.checked; });
$("#terms-accept").addEventListener("click", async () => {
  try {
    state.terms = await api("/api/terms", "POST", { version: state.terms.version });
    $("#dlg-terms").close();
    renderAll();
  } catch (error) {
    sayError(error);
  }
});
$("#terms-decline").addEventListener("click", async () => {
  try { await api("/api/quit", "POST"); } catch {}
  closed = true;
  if (eventSource) eventSource.close();
  document.body.innerHTML = `<main class="closed"><h1>${te("terms.declined")}</h1><p>${te("terms.declinedText")}</p></main>`;
});

function openAddDialog() {
  $("#add-urls").value = "";
  checkAddUrls();
  $("#dlg-add").showModal();
  $("#add-urls").focus();
}

function checkAddUrls() {
  const known = new Set(state.library.playlists.map((p) => p.id));
  const seen = new Set();
  const rows = [];
  let valid = 0;
  for (const url of $("#add-urls").value.split(/[\s,]+/).filter(Boolean)) {
    const found = classifyUrl(url);
    let kind = found ? "ok" : unsupportedKind(url) || "bad";
    if (found && seen.has(found[1])) kind = "dup";
    else if (found) {
      seen.add(found[1]);
      kind = known.has(found[1]) ? "known" : "ok";
      valid += 1;
    }
    const origin = found ? ` · ${ORIGIN_NAMES[found[0]]}` : "";
    const cls = kind === "dup" ? "dim" : kind === "track" || kind === "mix" ? "bad" : kind;
    rows.push(`<li><span class="${cls}">${te(`add.state.${kind}`)}${escapeHtml(origin)}</span><span title="${escapeHtml(url)}">${escapeHtml(url)}</span></li>`);
  }
  $("#add-check").innerHTML = rows.join("");
  $("#add-submit").disabled = valid === 0;
}
$("#add-urls").addEventListener("input", checkAddUrls);
$("#add-submit").addEventListener("click", async () => {
  try {
    const result = await api("/api/playlists", "POST", { urls: $("#add-urls").value, follow_up: $("#add-follow").checked });
    $("#dlg-add").close();
    say(result.rejected.length ? t("toast.skipped", { n: result.rejected.length }) : t("toast.loading"));
  } catch (error) {
    sayError(error);
  }
});

async function fixTags(scope, trackKey) {
  const items = scope === "track" ? [findTrack(trackKey)].filter(Boolean) : allTracks();
  const n = items.filter(({ tr }) => tr.file && ((tr.check || {}).tags_missing || []).length).length;
  if (!n) { say(t("error.no_tags_missing"), true); return; }
  if (!await confirmDialog(t("confirm.tags.title"), para(te("confirm.tags.text", { n })), t("confirm.tags.ok"))) return;
  const body = scope === "track" ? { track_ids: [trackKey] } : {};
  await api("/api/tags", "POST", body);
  say(t(busy() ? "toast.queuedAfter" : "toast.started"));
}

function scopeOptions(name, chosen) {
  const pl = selectedPlaylist();
  const n = state.library.playlists.length;
  const option = (value, title, sub, checked) => `<label><input type="radio" name="${name}" value="${value}" ${checked ? "checked" : ""}>
    <span><b>${escapeHtml(title)}</b><small>${escapeHtml(sub)}</small></span></label>`;
  return (pl ? option(pl.id, pl.name, t("sync.one", { n: pl.tracks.length }), chosen !== "all") : "")
    + option("all", t("sync.all"), t("sync.allSub", { n }), !pl || chosen === "all");
}

const EXPORT_KEY = "spotcheck-export";
function renderExportDialog() {
  const chosen = $("input[name=export-scope]:checked")?.value;
  $("#export-scope").innerHTML = scopeOptions("export-scope", chosen);
  const copy = $("input[name=export-mode]:checked")?.value === "copy";
  $("#export-copy").hidden = !copy;
  $("#export-submit").disabled = copy && !$("#export-target").value.trim();
}

function openExportDialog() {
  if (!state.library.playlists.length) { exportCsv(); return; }
  $("#export-scope").innerHTML = "";
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(EXPORT_KEY) || "{}"); } catch {}
  $(`input[name=export-mode][value="${saved.mode === "copy" ? "copy" : "music"}"]`).checked = true;
  $("#export-target").value = saved.target || "";
  $("#export-number").checked = saved.number ?? Boolean(state.library.settings.number_files);
  $("#export-open").checked = saved.open ?? true;
  $("#export-error").textContent = "";
  $("#export-target").removeAttribute("aria-invalid");
  renderExportDialog();
  $("#dlg-export").showModal();
}

$("#dlg-export").addEventListener("input", renderExportDialog);
$("#export-csv").addEventListener("click", () => { $("#dlg-export").close(); exportCsv(); });
$("#export-browse").addEventListener("click", async () => {
  try {
    const result = await api("/api/pick-folder", "POST", { initial: $("#export-target").value || null });
    if (result.path) { $("#export-target").value = result.path; renderExportDialog(); }
  } catch (error) {
    sayError(error);
  }
});
$("#export-submit").addEventListener("click", async () => {
  const scope = $("input[name=export-scope]:checked")?.value || "all";
  const body = {
    playlist_ids: scope === "all" ? null : [scope],
    mode: $("input[name=export-mode]:checked").value,
    target: $("#export-target").value.trim(),
    number: $("#export-number").checked,
    reveal: $("#export-open").checked,
  };
  try {
    const result = await api("/api/export", "POST", body);
    try { localStorage.setItem(EXPORT_KEY, JSON.stringify({ mode: body.mode, target: body.target, number: body.number, open: body.reveal })); } catch {}
    $("#dlg-export").close();
    say(result.job ? t(busy() ? "toast.queuedAfter" : "toast.exporting") : t("toast.m3uWritten", { n: result.written }));
  } catch (error) {
    if (error.fields && error.fields.target) {
      $("#export-error").textContent = fieldErrorText(error.fields.target);
      $("#export-target").setAttribute("aria-invalid", "true");
    } else {
      sayError(error);
    }
  }
});

function renderSyncScope() {
  $("#sync-scope").innerHTML = scopeOptions("sync-scope", $("input[name=sync-scope]:checked")?.value);
}

function openSyncDialog() {
  if (!state.library.playlists.length) { openAddDialog(); return; }
  $("#sync-scope").innerHTML = "";
  renderSyncScope();
  $("#dlg-sync").showModal();
}
$("#sync-submit").addEventListener("click", async () => {
  const value = $("input[name=sync-scope]:checked")?.value || "all";
  try {
    await api("/api/playlists/sync", "POST", { playlist_ids: value === "all" ? null : [value], follow_up: $("#sync-follow").checked });
    $("#dlg-sync").close();
    say(t(busy() ? "toast.queuedAfter" : "toast.reloading"));
  } catch (error) {
    sayError(error);
  }
});

const SETTINGS_PAGES = [
  { id: "match", groups: [["group.thresholds", ["accept_ber", "unsure_ber", "strong_ber"]], ["group.length", ["exact_diff", "dur_tolerance"]]] },
  { id: "files", groups: [["group.format", ["audio_format", "mp3_bitrate"]], ["group.folder", ["number_files", "write_playlists"]], ["group.quality", ["min_cutoff_khz", "min_bitrate"]]] },
  { id: "sources", groups: [["group.previews", ["use_deezer", "use_itunes"]], ["group.candidates", ["candidate_sources", "max_candidates"]], ["group.spotdl", ["audio_providers"]]] },
  { id: "search", groups: [["group.queries", ["queries"]], ["group.words", ["bad_source_words", "not_original_words"]]] },
  { id: "trim", groups: [["group.trim", ["trim_silence", "min_silence", "keep_lead", "keep_tail"]]] },
  { id: "safety", groups: [["group.safety", ["auto_replace", "keep_backups"]]] },
  { id: "auto", groups: [["group.auto", ["auto_check", "auto_sync_hours", "auto_download_new"]]] },
  { id: "performance", groups: [["group.performance", ["threads"]]] },
  { id: "deps" },
  { id: "about" },
];
const STEPS = { accept_ber: 0.01, unsure_ber: 0.01, strong_ber: 0.01, dur_tolerance: 0.5, exact_diff: 0.5, min_bitrate: 8, min_silence: 0.1, keep_lead: 0.1, keep_tail: 0.1, max_candidates: 1, keep_backups: 1, threads: 1, auto_sync_hours: 1, min_cutoff_khz: 0.5 };
const CHOICE_LABELS = { "youtube-music": "YouTube Music", youtube: "YouTube", soundcloud: "SoundCloud", bandcamp: "Bandcamp", piped: "Piped" };

const pageOfField = (key) => SETTINGS_PAGES.find((p) => (p.groups || []).some(([, fields]) => fields.includes(key)))?.id;
const settingsDirty = () => JSON.stringify(state.settings.draft) !== JSON.stringify(state.library.settings);

function validateDraft() {
  const d = state.settings.draft;
  const bounds = state.meta.bounds;
  const errors = {};
  for (const [key, [lo, hi]] of Object.entries(bounds)) {
    if (typeof d[key] !== "number" || Number.isNaN(d[key])) errors[key] = { code: "not_a_number", params: {} };
    else if (d[key] < lo || d[key] > hi) errors[key] = { code: "out_of_range", params: { min: lo, max: hi } };
  }
  if (!errors.accept_ber && !errors.unsure_ber && d.unsure_ber < d.accept_ber) errors.unsure_ber = { code: "unsure_below_accept", params: {} };
  if (!errors.accept_ber && !errors.strong_ber && d.strong_ber > d.accept_ber) errors.strong_ber = { code: "strong_above_accept", params: {} };
  for (const key of ["bad_source_words", "not_original_words"]) {
    if (!String(d[key]).trim()) errors[key] = { code: "required", params: {} };
    else {
      try { new RegExp(d[key], "i"); } catch (e) { errors[key] = { code: "invalid_regex", params: { error: e.message } }; }
    }
  }
  if (!d.audio_providers.length) errors.audio_providers = { code: "select_one", params: {} };
  if (!d.candidate_sources.length) errors.candidate_sources = { code: "select_one", params: {} };
  if (!d.use_deezer && !d.use_itunes) errors.use_deezer = { code: "preview_source_required", params: {} };
  for (const [key, allowed] of Object.entries(state.meta.choices || {})) {
    if (!allowed.includes(d[key])) errors[key] = { code: "invalid_choice", params: {} };
  }
  if (!d.queries.length) errors.queries = { code: "required", params: {} };
  else {
    for (const query of d.queries) {
      const fields = [...query.matchAll(/\{([^{}]*)\}/g)].map((m) => m[1]);
      const unknown = fields.find((f) => !state.meta.query_fields.includes(f));
      if (unknown !== undefined) { errors.queries = { code: "query_unknown_field", params: { query, field: unknown } }; break; }
      if (!fields.length) { errors.queries = { code: "query_no_field", params: { query } }; break; }
    }
    if (!errors.queries && d.queries.every((q) => q.includes("{hint}"))) errors.queries = { code: "query_needs_plain", params: {} };
  }
  state.settings.errors = errors;
  return errors;
}

const fieldErrorText = (err) => t(`field.${err.code}`, formatParams(err.params));

function fieldHtml(key) {
  const d = state.settings.draft;
  const value = d[key];
  const err = state.settings.errors[key];
  const hint = hasMessage(`set.${key}.hint`) ? `<p class="hint">${t(`set.${key}.hint`)}</p>` : "";
  const error = err ? `<p class="error" id="err-${key}">${escapeHtml(fieldErrorText(err))}</p>` : "";
  const invalid = err ? `aria-invalid="true" aria-describedby="err-${key}"` : "";
  const label = te(`set.${key}`);
  if (typeof value === "boolean") {
    return `<div class="field toggle" data-field="${key}"><label for="set-${key}">${label}</label>
      <input type="checkbox" class="switch" id="set-${key}" data-key="${key}" ${value ? "checked" : ""}>${hint}${error}</div>`;
  }
  if (key === "audio_providers" || key === "candidate_sources") {
    const options = key === "audio_providers" ? state.meta.audio_providers : state.meta.candidate_sources;
    return `<div class="field" data-field="${key}"><span class="f-label">${label}</span><div class="checks">
      ${options.map((o) => `<label><input type="checkbox" data-list="${key}" value="${o}" ${value.includes(o) ? "checked" : ""}>${escapeHtml(CHOICE_LABELS[o] || o)}</label>`).join("")}
      </div>${hint}${error}</div>`;
  }
  const choices = (state.meta.choices || {})[key];
  if (choices) {
    const off = key === "mp3_bitrate" && d.audio_format !== "mp3";
    const options = choices.map((o) => `<label class="${value === o ? "on" : ""}"><input type="radio" name="set-${key}" data-choice="${key}" value="${o}" ${value === o ? "checked" : ""} ${off ? "disabled" : ""}><span>${te(`choice.${key}.${o}`)}</span></label>`).join("");
    const detail = hasMessage(`choice.${key}.${value}.hint`) ? `<p class="choice-detail">${te(`choice.${key}.${value}.hint`)}</p>` : "";
    return `<div class="field" data-field="${key}"><span class="f-label">${label}</span>
      <div class="seg-choice ${off ? "off" : ""}" role="radiogroup" aria-label="${label}">${options}</div>
      ${off ? `<p class="hint">${te(`set.${key}.off`)}</p>` : detail + hint}${error}</div>`;
  }
  if (key === "queries") {
    return `<div class="field" data-field="${key}"><label for="set-${key}">${label}</label>
      <textarea id="set-${key}" data-key="${key}" spellcheck="false" ${invalid}>${escapeHtml(value.join("\n"))}</textarea>${hint}${error}</div>`;
  }
  if (typeof value === "number") {
    const [lo, hi] = state.meta.bounds[key];
    const step = STEPS[key] || 1;
    const pct = Math.max(0, Math.min(100, ((value - lo) / (hi - lo)) * 100));
    return `<div class="field" data-field="${key}"><label for="set-${key}">${label}</label><div class="range-row">
      <input type="range" data-key="${key}" min="${lo}" max="${hi}" step="${step}" value="${value}" style="--pct:${pct}%" aria-label="${label}">
      <input type="number" id="set-${key}" data-key="${key}" min="${lo}" max="${hi}" step="${step}" value="${value}" ${invalid}></div>${hint}${error}</div>`;
  }
  return `<div class="field" data-field="${key}"><label for="set-${key}">${label}</label>
    <input type="text" id="set-${key}" data-key="${key}" value="${escapeHtml(value)}" spellcheck="false" ${invalid}>${hint}${error}</div>`;
}

function settingsNavHtml() {
  const errorsByPage = new Set(Object.keys(state.settings.errors).map(pageOfField));
  return SETTINGS_PAGES.map((p) => `<button class="nav-item ${!state.settings.search && state.settings.page === p.id ? "on" : ""}" type="button" role="tab" data-page="${p.id}">
    <span class="main-text">${te(`tab.${p.id}`)}</span>${errorsByPage.has(p.id) ? '<span class="err"></span>' : ""}</button>`).join("");
}

function renderSettings() {
  validateDraft();
  $("#settings-nav").innerHTML = settingsNavHtml();
  const page = $("#settings-page");
  const query = state.settings.search.trim().toLowerCase();
  const scroll = page.dataset.page === (query ? "?" : state.settings.page) ? page.scrollTop : 0;
  if (query) {
    const matches = SETTINGS_PAGES.flatMap((p) => (p.groups || []).flatMap(([, fields]) => fields))
      .filter((key) => `${t(`set.${key}`)} ${hasMessage(`set.${key}.hint`) ? t(`set.${key}.hint`) : ""}`.toLowerCase().includes(query));
    page.innerHTML = `<h3 class="page-title">${te("settings.results", { q: state.settings.search })}</h3>
      ${matches.length ? `<div class="group"><div class="g-body">${matches.map(fieldHtml).join("")}</div></div>` : `<p class="dim">${te("settings.noResults")}</p>`}`;
    page.dataset.page = "?";
  } else {
    const def = SETTINGS_PAGES.find((p) => p.id === state.settings.page);
    page.dataset.page = def.id;
    if (def.id === "deps") page.innerHTML = depsPageHtml();
    else if (def.id === "about") page.innerHTML = aboutPageHtml();
    else {
      page.innerHTML = `<h3 class="page-title">${te(`tab.${def.id}`)}</h3><p class="page-intro">${te(`tab.${def.id}.intro`)}</p>
        ${def.groups.map(([title, fields]) => `<div class="group"><div class="g-head">${te(title)}</div><div class="g-body">${fields.map(fieldHtml).join("")}</div></div>`).join("")}`;
    }
  }
  page.scrollTop = scroll;
  const hasErrors = Object.keys(state.settings.errors).length > 0;
  $("#settings-save").disabled = hasErrors || !settingsDirty();
  $("#settings-save").title = hasErrors ? t("settings.fixErrors") : settingsDirty() ? "" : t("settings.noChanges");
}

function onSettingsInput(el) {
  const d = state.settings.draft;
  if (el.dataset.list) {
    const key = el.dataset.list;
    d[key] = $$(`#settings-page [data-list="${key}"]`).filter((x) => x.checked).map((x) => x.value);
  } else if (el.dataset.choice) {
    d[el.dataset.choice] = el.value;
    renderSettings();
    return;
  } else {
    const key = el.dataset.key;
    if (el.type === "checkbox") d[key] = el.checked;
    else if (el.tagName === "TEXTAREA") d[key] = el.value.split("\n").map((x) => x.trim()).filter(Boolean);
    else if (el.type === "number" || el.type === "range") {
      d[key] = el.value === "" ? NaN : Number(el.value);
      const field = el.closest(".field");
      $$("input[type=range], input[type=number]", field).forEach((other) => { if (other !== el) other.value = el.value; });
      const range = $("input[type=range]", field);
      const [lo, hi] = state.meta.bounds[key];
      range.style.setProperty("--pct", `${Math.max(0, Math.min(100, ((Number(el.value) - lo) / (hi - lo)) * 100))}%`);
    } else d[key] = el.value;
  }
  refreshSettingsErrors();
}

function refreshSettingsErrors() {
  const errors = validateDraft();
  $$("#settings-page .field").forEach((field) => {
    const key = field.dataset.field;
    const err = errors[key];
    $(".error", field)?.remove();
    $$("input:not([type=checkbox]):not([type=range]), textarea", field).forEach((input) => {
      if (err) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
    });
    if (err) field.insertAdjacentHTML("beforeend", `<p class="error">${escapeHtml(fieldErrorText(err))}</p>`);
  });
  $("#settings-nav").innerHTML = settingsNavHtml();
  const hasErrors = Object.keys(errors).length > 0;
  $("#settings-save").disabled = hasErrors || !settingsDirty();
  $("#settings-save").title = hasErrors ? t("settings.fixErrors") : settingsDirty() ? "" : t("settings.noChanges");
}

function depsPageHtml() {
  const deps = state.settings.deps;
  let body = `<p class="dim">${te("deps.loading")}</p>`;
  if (deps && deps.error) body = `<p class="bad-text">${te("deps.error", { message: deps.error })}</p>`;
  else if (deps) {
    const rows = deps.items.map((item) => {
      const cls = !item.installed ? "s-bad" : item.update ? "s-review" : "s-ok";
      const status = t(!item.installed ? "deps.missing" : item.update ? "deps.update" : "deps.ok");
      const update = item.update ? ` → <b class="warn-text">${escapeHtml(item.latest)}</b>` : "";
      return `<div class="dep"><div><b>${escapeHtml(item.name)}</b><div class="why">${te(`dep.${item.key}`)}</div>
          ${item.path ? `<span class="path">${escapeHtml(item.path)}</span>` : ""}</div>
        <span class="ver">${escapeHtml(item.version || "")}${update}</span>
        <span class="pill ${cls}">${escapeHtml(status)}</span></div>`;
    }).join("");
    const links = deps.links.map((l) => `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener"><b>${escapeHtml(l.name)}</b><span>${te(`linkInfo.${l.id}`)}</span></a>`).join("");
    body = `<div class="group"><div class="g-head">${te("deps.components")}</div><div class="g-body">${rows}</div></div>
      <div class="page-actions">
        <button class="btn" type="button" data-install="update-python">${icon("download", "sm")}${te(deps.packaged ? "deps.updateYtdlp" : "deps.updatePython")}</button>
        ${deps.packaged ? "" : `<button class="btn" type="button" data-install="ffmpeg">${te("deps.installFfmpeg")}</button>
        <button class="btn" type="button" data-install="deno">${te("deps.installDeno")}</button>`}
      </div><p class="hint">${te("deps.installHint")}</p>
      <div class="group" style="margin-top:16px"><div class="g-head">${te("deps.links")}</div><div class="g-body links-list">${links}</div></div>`;
  }
  return `<h3 class="page-title">${te("tab.deps")}</h3><p class="page-intro">${te("tab.deps.intro")}</p>${body}`;
}

function aboutPageHtml() {
  const p = state.project || {};
  const deps = state.settings.deps;
  return `<h3 class="page-title">${te("tab.about")}</h3>
    <p class="page-intro">${te("about.text")}</p>
    <div class="group"><div class="g-body" style="padding:12px 14px"><dl class="kv">
      <dt>${te("about.version")}</dt><dd>SpotCheck ${escapeHtml(state.version)}</dd>
      <dt>${te("about.project")}</dt><dd>${escapeHtml(p.name || "")}</dd>
      <dt>${te("about.music")}</dt><dd>${escapeHtml(p.music || "")}</dd>
      <dt>${te("about.data")}</dt><dd>${escapeHtml(p.data || "")}</dd>
      ${deps && !deps.error ? `<dt>Python</dt><dd>${escapeHtml(deps.python)} · ${escapeHtml(deps.platform)}</dd>` : ""}
      <dt>${te("about.license")}</dt><dd>GPL-3.0-or-later</dd>
      <dt>${te("about.terms")}</dt><dd><a href="${escapeHtml(state.terms.urls[currentLanguage] || state.terms.urls.en)}" target="_blank" rel="noopener">${te("about.termsLink")}</a></dd>
      <dt>${te("about.source")}</dt><dd><a href="https://github.com/djkvido/spotcheck" target="_blank" rel="noopener">github.com/djkvido/spotcheck</a></dd>
    </dl></div></div>
    <h3 class="page-title">${te("diag.title")}</h3>
    <p class="page-intro">${te("diag.text")}</p>
    <div class="diag-actions"><button class="btn" type="button" data-diagnostics="quick">${te("diag.quick")}</button><button class="btn" type="button" data-diagnostics="network">${te("diag.network")}</button></div>
    <textarea id="diag-text" class="diag-text" readonly rows="12" spellcheck="false" hidden></textarea>
    <p class="hint">${te("about.disclaimer")}</p>`;
}

function installSource(action) {
  const deps = state.settings.deps || {};
  if (action === "update-python") return deps.packaged ? "deps.source.packaged" : "deps.source.python";
  if (action === "ffmpeg") return deps.platform === "win32" ? "deps.source.ffmpegWindows" : "deps.source.ffmpeg";
  return "deps.source.deno";
}

async function loadDeps() {
  try {
    state.settings.deps = await api("/api/deps");
  } catch (error) {
    state.settings.deps = { error: error.message };
  }
  if ($("#dlg-settings").open && ["deps", "about"].includes(state.settings.page)) renderSettings();
}

function openSettings(page = state.settings.page) {
  state.settings.draft = structuredClone(state.library.settings);
  state.settings.page = page;
  state.settings.search = "";
  $("#settings-search").value = "";
  renderSettings();
  $("#dlg-settings").showModal();
  loadDeps();
}

async function closeSettings() {
  if (settingsDirty() && !await confirmDialog(t("settings.discardTitle"), para(te("settings.discardText")), t("settings.discardOk"), true)) return;
  $("#dlg-settings").close();
}

$("#btn-settings").addEventListener("click", () => openSettings());
$("#settings-close").addEventListener("click", closeSettings);
$("#settings-cancel").addEventListener("click", closeSettings);
$("#dlg-settings").addEventListener("cancel", (event) => { event.preventDefault(); closeSettings(); });
$("#settings-nav").addEventListener("click", (event) => {
  const item = event.target.closest("[data-page]");
  if (!item) return;
  state.settings.page = item.dataset.page;
  state.settings.search = "";
  $("#settings-search").value = "";
  renderSettings();
});
$("#settings-search").addEventListener("input", (event) => { state.settings.search = event.target.value; renderSettings(); });
$("#settings-page").addEventListener("input", (event) => {
  if (event.target.matches("[data-key],[data-list],[data-choice]")) onSettingsInput(event.target);
});
async function runDiagnostics(button) {
  const box = $("#diag-text");
  const buttons = Array.from(document.querySelectorAll("[data-diagnostics]"));
  buttons.forEach((b) => { b.disabled = true; });
  say(t("diag.running"));
  try {
    const result = await api(`/api/diagnostics?network=${button.dataset.diagnostics === "network"}`);
    box.hidden = false;
    box.value = result.text;
    box.focus();
    box.select();
    let copied = false;
    try {
      await navigator.clipboard.writeText(result.text);
      copied = true;
    } catch (error) {
      copied = false;
    }
    say(t(copied ? "diag.copied" : "diag.ready"));
  } catch (error) {
    sayError(error);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

$("#settings-page").addEventListener("click", async (event) => {
  const diagnostics = event.target.closest("[data-diagnostics]");
  if (diagnostics) { await runDiagnostics(diagnostics); return; }
  const install = event.target.closest("[data-install]");
  if (!install) return;
  const action = install.dataset.install;
  const html = para(te(installSource(action))) + para(te("deps.confirmLicenses"));
  if (!await confirmDialog(t("deps.confirmTitle"), html, t("deps.confirmOk"))) return;
  try {
    await api("/api/deps/install", "POST", { action });
    say(t("toast.installing"));
  } catch (error) {
    sayError(error);
  }
});
$("#settings-save").addEventListener("click", async () => {
  try {
    state.library.settings = await api("/api/settings", "PUT", state.settings.draft);
    state.settings.draft = structuredClone(state.library.settings);
    $("#dlg-settings").close();
    say(t("toast.saved"));
    renderAll();
  } catch (error) {
    if (error.fields) {
      state.settings.errors = error.fields;
      const first = pageOfField(Object.keys(error.fields)[0]);
      if (first) state.settings.page = first;
      renderSettings();
    }
    sayError(error);
  }
});
$("#settings-reset").addEventListener("click", async () => {
  if (!await confirmDialog(t("confirm.reset.title"), para(te("confirm.reset.text")), t("confirm.reset.ok"))) return;
  state.settings.draft = structuredClone(state.meta.defaults);
  renderSettings();
});

async function loadProjects() {
  const data = await api("/api/projects");
  state.projects.list = data.projects;
  state.projects.current = data.current;
  state.projects.picker = data.folder_picker;
}

async function openProjects(view = null) {
  try {
    await loadProjects();
  } catch (error) {
    sayError(error);
    return;
  }
  state.projects.view = view || state.projects.current;
  state.projects.errors = {};
  renderProjects();
  if (!$("#dlg-projects").open) $("#dlg-projects").showModal();
}

function renderProjects() {
  const p = state.projects;
  $("#projects-nav").innerHTML = p.list.map((item) => `<button class="nav-item ${p.view === item.id ? "on" : ""}" type="button" data-project="${escapeHtml(item.id)}">
      <span class="main-text">${escapeHtml(item.name)}<span class="sub">${te("project.counts", { playlists: item.playlists, tracks: item.tracks })}</span></span>
      ${item.id === p.current ? `<span class="tag">${te("project.active")}</span>` : ""}</button>`).join("");
  $("#projects-page").innerHTML = p.view === "new" ? newProjectHtml() : projectPageHtml(p.list.find((x) => x.id === p.view));
}

const projectFieldError = (field) => (state.projects.errors[field]
  ? `<p class="error">${escapeHtml(t(`field.${state.projects.errors[field].code}`, formatParams(state.projects.errors[field].params)))}</p>` : "");

function musicFieldHtml(value) {
  return `<div class="field"><label for="project-music">${te("project.music")}</label>
    <div class="path-row"><input type="text" id="project-music" value="${escapeHtml(value)}" spellcheck="false" ${state.projects.errors.music ? 'aria-invalid="true"' : ""}>
    ${state.projects.picker ? `<button class="btn" type="button" id="project-browse">${icon("folder", "sm")}${te("project.browse")}</button>` : ""}</div>
    <p class="hint">${te("project.musicHint")}</p>${projectFieldError("music")}</div>`;
}

function newProjectHtml() {
  const imported = state.projects.imported;
  const blocked = busy();
  return `<h3 class="page-title">${te(imported ? "project.importTitle" : "project.newTitle")}</h3>
    <p class="page-intro">${te(imported ? "project.importIntro" : "project.newIntro", imported ? { n: imported.playlists.length } : {})}</p>
    <div class="group"><div class="g-body">
      <div class="field"><label for="project-name-input">${te("project.name")}</label>
        <input type="text" id="project-name-input" maxlength="60" value="${escapeHtml(imported ? imported.name : "")}" ${state.projects.errors.name ? 'aria-invalid="true"' : ""}>${projectFieldError("name")}</div>
      ${musicFieldHtml(state.projects.newMusic || "")}
    </div></div>
    ${blocked ? `<p class="hint warn-text">${te("project.busy")}</p>` : ""}
    <div class="page-actions"><button class="btn primary" type="button" id="project-create" ${blocked ? "disabled" : ""}>${te("project.create")}</button></div>`;
}

function projectPageHtml(item) {
  if (!item) return "";
  const active = item.id === state.projects.current;
  const blocked = busy();
  return `<h3 class="page-title">${escapeHtml(item.name)}</h3>
    <div class="group"><div class="g-body" style="padding:12px 14px"><dl class="kv">
      <dt>${te("project.state")}</dt><dd>${te(active ? "project.isActive" : "project.isInactive")}</dd>
      <dt>${te("project.contents")}</dt><dd>${te("project.counts", { playlists: item.playlists, tracks: item.tracks })}</dd>
      <dt>${te("project.music")}</dt><dd>${escapeHtml(item.music)}</dd>
      <dt>${te("project.dataFolder")}</dt><dd>${escapeHtml(item.data)}</dd>
      <dt>${te("project.created")}</dt><dd>${escapeHtml(fmtDate(item.created))}</dd>
    </dl></div></div>
    <div class="page-actions">
      ${active ? "" : `<button class="btn primary" type="button" data-project-action="open" ${blocked ? "disabled" : ""} title="${blocked ? te("project.busy") : ""}">${te("project.open")}</button>`}
      ${active ? `<button class="btn" type="button" data-project-action="export">${icon("save", "sm")}${te("project.export")}</button>` : ""}
      ${active ? "" : `<button class="btn danger" type="button" data-project-action="remove">${icon("trash", "sm")}${te("project.remove")}</button>`}
    </div>
    <div class="group" style="margin-top:18px"><div class="g-head">${te("project.edit")}</div><div class="g-body">
      <div class="field"><label for="project-name-input">${te("project.name")}</label>
        <input type="text" id="project-name-input" maxlength="60" value="${escapeHtml(item.name)}" ${state.projects.errors.name ? 'aria-invalid="true"' : ""}>${projectFieldError("name")}</div>
      ${musicFieldHtml(item.music)}
    </div></div>
    <div class="page-actions"><button class="btn" type="button" data-project-action="save">${te("project.saveChanges")}</button></div>`;
}

let suggestTimer = 0;
$("#projects-page").addEventListener("input", (event) => {
  if (event.target.id === "project-music") {
    state.projects.musicTouched = true;
    state.projects.newMusic = event.target.value;
  }
  if (event.target.id === "project-name-input" && state.projects.view === "new" && !state.projects.musicTouched) {
    clearTimeout(suggestTimer);
    const name = event.target.value;
    suggestTimer = setTimeout(async () => {
      try {
        const { music } = await api(`/api/projects/suggest?name=${enc(name)}`);
        state.projects.newMusic = music;
        const input = $("#project-music");
        if (input && !state.projects.musicTouched) input.value = music;
      } catch (error) {  }
    }, 250);
  }
});

$("#projects-nav").addEventListener("click", (event) => {
  const item = event.target.closest("[data-project]");
  if (!item) return;
  state.projects.view = item.dataset.project;
  state.projects.errors = {};
  state.projects.imported = null;
  renderProjects();
});

function startNewProject(imported = null) {
  state.projects.view = "new";
  state.projects.errors = {};
  state.projects.imported = imported;
  state.projects.musicTouched = false;
  state.projects.newMusic = "";
  renderProjects();
  const name = $("#project-name-input");
  name.focus();
  name.dispatchEvent(new Event("input", { bubbles: true }));
}
$("#project-new").addEventListener("click", () => startNewProject());
$("#project-import").addEventListener("click", () => $("#project-file").click());
$("#project-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (data.format !== "spotcheck-project" || !Array.isArray(data.playlists)) throw new Error("format");
    startNewProject({ name: String(data.name || ""), settings: data.settings || null, playlists: data.playlists.map((p) => String(p.url || "")).filter(Boolean) });
  } catch (error) {
    say(t("error.import_invalid"), true);
  }
});

$("#projects-page").addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  const p = state.projects;
  try {
    if (target.id === "project-browse") {
      const input = $("#project-music");
      const { path } = await api("/api/pick-folder", "POST", { initial: input.value || null });
      if (path) {
        input.value = path;
        p.musicTouched = true;
        p.newMusic = path;
      }
      return;
    }
    if (target.id === "project-create") {
      const body = { name: $("#project-name-input").value, music: $("#project-music").value };
      if (p.imported) Object.assign(body, { playlists: p.imported.playlists, settings: p.imported.settings });
      const result = await api("/api/projects", "POST", body);
      $("#dlg-projects").close();
      say(t(result.job ? "toast.projectImported" : "toast.projectCreated", { name: result.project.name }));
      return;
    }
    const action = target.dataset.projectAction;
    const item = p.list.find((x) => x.id === p.view);
    if (!action || !item) return;
    if (action === "open") {
      await api(`/api/projects/${enc(item.id)}/open`, "POST");
      $("#dlg-projects").close();
      say(t("toast.projectOpened", { name: item.name }));
    } else if (action === "export") {
      exportProject();
    } else if (action === "remove") {
      const html = para(th("project.removeText", { name: item.name })) + para(te("project.removeFiles"));
      if (await confirmDialog(t("project.removeTitle"), html, t("project.remove"), true)) {
        await api(`/api/projects/${enc(item.id)}`, "DELETE");
        await loadProjects();
        p.view = p.current;
        renderProjects();
        say(t("toast.projectRemoved", { name: item.name }));
      }
    } else if (action === "save") {
      const name = $("#project-name-input").value;
      const music = $("#project-music").value;
      const changes = {};
      if (name.trim() !== item.name) changes.name = name;
      if (music.trim() !== item.music) changes.music = music;
      if (!Object.keys(changes).length) { say(t("settings.noChanges")); return; }
      if (changes.music && !await confirmDialog(t("project.moveTitle"), para(te("project.moveText")), t("project.moveOk"))) return;
      await api(`/api/projects/${enc(item.id)}`, "PATCH", changes);
      p.errors = {};
      await loadProjects();
      renderProjects();
      say(t("toast.projectSaved"));
    }
  } catch (error) {
    if (error.fields) {
      p.errors = error.fields;
      const name = $("#project-name-input")?.value;
      const music = $("#project-music")?.value;
      renderProjects();
      if (name !== undefined) $("#project-name-input").value = name;
      if (music !== undefined) $("#project-music").value = music;
    }
    sayError(error);
  }
});

function exportProject() {
  const data = {
    format: "spotcheck-project",
    version: 1,
    name: state.project.name,
    exported: new Date().toISOString(),
    settings: state.library.settings,
    playlists: state.library.playlists.map((pl) => ({ name: pl.name, url: pl.url })),
  };
  const name = state.project.name.replace(/[<>:"/\\|?*]+/g, "").trim() || "project";
  download(`${name}.spotcheck.json`, JSON.stringify(data, null, 2), "application/json");
  say(t("toast.projectExported"));
}

$("#btn-project").addEventListener("click", () => openProjects());

const HELP_TOPICS = ["start", "actions", "statuses", "verify", "files", "auto", "safety", "projects"];

function renderHelp() {
  $("#help-nav").innerHTML = HELP_TOPICS.map((id) => `<button class="nav-item ${state.helpTopic === id ? "on" : ""}" type="button" data-topic="${id}">${te(`help.${id}.title`)}</button>`).join("");
  $("#help-page").innerHTML = `<h3 class="page-title">${te(`help.${state.helpTopic}.title`)}</h3>${t(`help.${state.helpTopic}.body`)}`;
  $("#help-page").scrollTop = 0;
}
function openHelp(topic = state.helpTopic) {
  state.helpTopic = topic;
  renderHelp();
  $("#dlg-help").showModal();
}
$("#help-nav").addEventListener("click", (event) => {
  const item = event.target.closest("[data-topic]");
  if (!item) return;
  state.helpTopic = item.dataset.topic;
  renderHelp();
});

async function loadState() {
  const data = await api("/api/state");
  state.project = data.project;
  state.library = data.library;
  state.meta = data.settings_meta;
  state.ffmpeg = data.ffmpeg;
  state.version = data.version;
  state.terms = data.terms;
  state.logs = data.logs;
  state.jobs = Object.fromEntries(data.jobs.map((j) => [j.id, j]));
  if (state.selected !== "all" && !selectedPlaylist()) state.selected = "all";
  if (state.openKey && !findTrack(state.openKey)) closeDrawer();
  renderAll();
  if (!state.terms.accepted) showTerms();
}

function onJobEvent(job) {
  const previous = state.jobs[job.id];
  state.jobs[job.id] = job;
  if (TERMINAL.has(job.state) && (!previous || !TERMINAL.has(previous.state))) {
    const result = jobResult(job);
    state.lastJob = job;
    say(result.text, Boolean(result.error));
    if (job.kind === "deps" && $("#dlg-settings").open) loadDeps();
  }
  scheduleRender();
}

function connectEvents() {
  const source = new EventSource("/api/events");
  eventSource = source;
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    switch (event.type) {
      case "track": {
        const pl = state.library.playlists.find((p) => p.id === event.playlist_id);
        const index = pl ? pl.tracks.findIndex((x) => x.id === event.track.id) : -1;
        if (index >= 0) pl.tracks[index] = event.track;
        scheduleRender();
        if (state.openKey === `${event.playlist_id}:${event.track.id}`) renderDrawer(true);
        break;
      }
      case "job":
        onJobEvent(event.job);
        break;
      case "log":
        state.logs.push(event);
        if (state.logs.length > 400) state.logs.shift();
        renderLog();
        break;
      case "saved":
        state.library.saved_at = event.t;
        renderTopbar();
        renderHealth();
        break;
      case "library":
        loadState();
        break;
      case "project":
        closeDrawer();
        state.selected = "all";
        state.filter = "all";
        state.lastJob = null;
        loadState();
        break;
      case "settings":
        state.library.settings = event.settings;
        scheduleRender();
        break;
      default:
        break;
    }
  };
  source.onerror = () => { if (closed) source.close(); else say(t("error.server_unreachable"), true); };
  source.onopen = () => { if ($("#statusline").classList.contains("error")) say(t("toast.reconnected")); };
}

translatePage();
restoreView();
loadState()
  .then(() => {
    connectEvents();
    const m = location.hash.match(/^#track=(.+)$/);
    if (m && findTrack(decodeURIComponent(m[1]))) openDrawer(decodeURIComponent(m[1]));
  })
  .catch((error) => {
    $("#main").innerHTML = `<section class="panel welcome"><div class="dropzone"><h2>${te("app.serverDown")}</h2><p>${escapeHtml(error.message)}</p></div></section>`;
  });

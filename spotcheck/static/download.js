"use strict";

const DOWNLOAD_KEY = "spotcheck-download";
const DL_TERMINAL = new Set(["done", "failed", "cancelled"]);
const DL_STATE_CLASS = { queued: "s-missing", running: "s-run", done: "s-ok", failed: "s-bad", cancelled: "s-missing" };
const DL_AUDIO = { mp3: "MP3", m4a: "M4A (AAC)", opus: "Opus", ogg: "OGG Vorbis", flac: "FLAC", wav: "WAV" };
const DL_LOSSY = new Set(["mp3", "m4a"]);
const DL_HEIGHT_NAMES = { 4320: "8K", 2160: "4K", 1440: "2K" };
const DL_MBITS = { 144: 0.1, 240: 0.25, 360: 0.5, 480: 1, 720: 1.5, 1080: 3, 1440: 9, 2160: 18, 4320: 40 };
const DL_PROBES = 3;
const DL_SLOW = 8000;
const DL_LANG = /^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$/;
ICONS.video = '<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>';
ICONS.clipboard = '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>';
ICONS.pause = '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>';

const dl = {
  loaded: false,
  data: null,
  input: "",
  cards: [],
  waiting: [],
  active: 0,
  items: new Map(),
  seq: 0,
  options: { mode: "video", container: "mp4", height: 1080, hdr: false, audio: "mp3", bitrate: "320", subtitles: "none", languages: "cs, en", auto_subtitles: false, thumbnail: true },
  auto: false,
  notify: false,
  outputMode: "default",
  folder: "",
  free: null,
  batch: null,
  lastPick: null,
};

function dlRestore() {
  try {
    const saved = JSON.parse(localStorage.getItem(DOWNLOAD_KEY) || "{}");
    if (saved.options && typeof saved.options === "object") Object.assign(dl.options, saved.options);
    if (["default", "folder"].includes(saved.outputMode)) dl.outputMode = saved.outputMode;
    if (typeof saved.folder === "string") dl.folder = saved.folder;
    dl.auto = saved.auto === true;
    dl.notify = saved.notify === true && "Notification" in window && Notification.permission === "granted";
  } catch {}
}

function dlRemember() {
  const { options, outputMode, folder, auto, notify } = dl;
  try { localStorage.setItem(DOWNLOAD_KEY, JSON.stringify({ options, outputMode, folder, auto, notify })); } catch {}
}

const dlThumb = (probe, index, cls = "") => (probe && index != null
  ? `<img class="${cls}" src="/api/download/thumb/${enc(probe)}/${enc(index)}" alt="" loading="lazy" data-fallback="thumb">`
  : "");
const heightLabel = (h) => (h ? `${h}p${DL_HEIGHT_NAMES[h] ? ` · ${DL_HEIGHT_NAMES[h]}` : ""}` : t("download.best"));
const dlHost = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };
const dlLanguages = () => dl.options.languages.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);

function dlOptionsPayload(override = null) {
  const o = { ...dl.options, ...(override || {}) };
  return {
    mode: o.mode,
    container: o.container,
    height: o.height ? Number(o.height) : null,
    hdr: Boolean(o.hdr),
    audio: o.audio,
    bitrate: String(o.bitrate),
    subtitles: o.subtitles,
    languages: o.mode === "video" && o.subtitles !== "none" ? dlLanguages() : [],
    auto_subtitles: Boolean(o.auto_subtitles),
    thumbnail: Boolean(o.thumbnail),
  };
}

function dlTargetLabel(options) {
  if (!options) return "";
  if (options.mode === "audio") return `${DL_AUDIO[options.audio] || options.audio}${DL_LOSSY.has(options.audio) ? ` · ${options.bitrate} kbps` : ""}`;
  const subs = options.subtitles !== "none" ? ` · ${t("download.withSubtitles")}` : "";
  const quality = options.height ? t("download.upToHeight", { height: heightLabel(options.height) }) : t("download.best");
  return `${String(options.container).toUpperCase()} · ${quality}${options.hdr ? " · HDR" : ""}${subs}`;
}

function dlActualLabel(item) {
  const a = item.actual;
  if (!a) return dlTargetLabel(item.options);
  if (item.options.mode === "audio") return `${DL_AUDIO[item.options.audio] || item.ext.toUpperCase()}${a.acodec ? ` · ${String(a.acodec).toUpperCase()}` : ""}`;
  return [item.ext.toUpperCase(), a.height ? `${a.height}p` : "", a.vcodec ? String(a.vcodec).toUpperCase() : ""].filter(Boolean).join(" · ");
}

function dlAudioBytes(seconds, options, known = {}) {
  if (!seconds) return null;
  if (DL_LOSSY.has(options.audio)) return Number(options.bitrate) * 125 * seconds;
  if (options.audio === "wav") return 176400 * seconds;
  if (options.audio === "flac") return 0.6 * 176400 * seconds;
  return known.opus || known.best || 160 * 125 * seconds;
}

function dlEstimate(result, options) {
  if (options.mode === "audio") return { bytes: dlAudioBytes(result.duration, options, result.audio_bytes || {}), height: null };
  const variants = result.variants || [];
  const variant = variants.find((v) => !options.height || v.height <= options.height) || variants[variants.length - 1];
  if (!variant) return { bytes: null, height: null };
  return { bytes: variant.bytes[options.container] ?? variant.bytes.mkv ?? null, height: variant.height };
}

function dlEntryEstimate(seconds, options) {
  if (!seconds) return null;
  if (options.mode === "audio") return dlAudioBytes(seconds, options);
  const heights = Object.keys(DL_MBITS).map(Number);
  const height = options.height ? heights.filter((h) => h <= options.height).pop() || 360 : 1080;
  return (DL_MBITS[height] * 1e6 / 8 + 16000) * seconds;
}

function dlCardOptions(card) {
  return { ...dl.options, ...(card.override || {}) };
}

function dlQueued(url, options) {
  const wanted = JSON.stringify(dlOptionsPayload(options));
  return [...dl.items.values()].find((i) => i.url === url && i.state !== "failed" && i.state !== "cancelled" && JSON.stringify(i.options) === wanted) || null;
}

async function dlReloadItems() {
  try {
    const data = await api("/api/download");
    dl.data.items = data.items;
    dl.data.paused = data.paused;
    dl.data.limit = data.limit;
    dl.items = new Map(data.items.map((item) => [item.id, item]));
  } catch {
    return;
  }
  dl.cards.forEach(dlPatchCard);
  dlRenderQueue();
  dlRenderStart();
  dlUpdateTitle();
}

async function dlLoad() {
  try {
    dl.data = await api("/api/download");
  } catch (error) {
    $("#downloader").innerHTML = `<section class="panel welcome"><div class="dropzone"><h2>${te("download.loadFailed")}</h2><p>${escapeHtml(error.message)}</p></div></section>`;
    return;
  }
  dl.loaded = true;
  dl.items = new Map(dl.data.items.map((item) => [item.id, item]));
  if (!dl.folder) dl.folder = dl.data.default_folder;
  if (!dl.data.audio_formats.includes(dl.options.audio)) dl.options.audio = dl.data.audio_formats[0] || "mp3";
  if (!dl.data.available_containers.includes(dl.options.container)) dl.options.container = "mp4";
  dl.free = dl.outputMode === "default" ? dl.data.free : null;
  dlRender();
  dlRefreshSpace();
}

async function dlRefreshSpace() {
  try {
    const { free } = await api("/api/download/space", "POST", { initial: dl.outputMode === "folder" ? dl.folder.trim() || null : null });
    dl.free = free;
  } catch {
    dl.free = null;
  }
  dlRenderStart();
}

function dlRender() {
  if (!dl.loaded) {
    $("#downloader").innerHTML = `<section class="panel welcome"><div class="dropzone"><p>${te("download.loading")}</p></div></section>`;
    return;
  }
  $("#downloader").innerHTML = `
    <div class="cv-main">
      <section class="panel dl-input ${dl.cards.length ? "has-cards" : ""}" id="dl-input" aria-labelledby="dl-input-title"></section>
      <section class="panel cv-queue dl-queue" id="dl-queue" aria-labelledby="dl-queue-title"></section>
    </div>
    <section class="panel cv-target" id="dl-options" aria-labelledby="dl-options-title"></section>`;
  dlRenderInput();
  dlRenderOptions();
  dlRenderQueue();
}

function dlEntryRow(card, entry, index) {
  const checked = card.selected.has(index);
  const known = dlQueued(entry.url, card.override);
  const hidden = card.filter && !entry.title.toLowerCase().includes(card.filter.toLowerCase());
  const mark = known ? `<span class="pill ${known.state === "done" ? "s-ok" : "s-run"}">${te(known.state === "done" ? "download.duplicate" : "download.inQueue")}</span>` : "";
  return `<li class="dl-entry" ${hidden ? "hidden" : ""}>
      <label class="dl-entry-row"><input type="checkbox" data-dl-pick="${card.key}:${index}" ${checked ? "checked" : ""}>
        <span class="dl-thumb xs">${dlThumb(card.result.probe, entry.thumb)}</span>
        <span class="dl-entry-text"><span class="name">${escapeHtml(entry.title)}</span>
        <span class="meta">${escapeHtml([entry.uploader, entry.duration ? fmtDuration(entry.duration) : ""].filter(Boolean).join(" · "))}</span></span>${mark}</label>
    </li>`;
}

function dlPlaylistSummary(card) {
  const options = dlCardOptions(card);
  let seconds = 0;
  let bytes = 0;
  for (const i of card.selected) {
    const entry = card.result.entries[i];
    seconds += entry.duration || 0;
    bytes += dlEntryEstimate(entry.duration, options) || 0;
  }
  return t("download.playlistMeta", { chosen: card.selected.size, n: card.result.entries.length, duration: fmtDuration(seconds), size: bytes ? fmtBytes(bytes) : "–" });
}

function dlOverrideSelect(card) {
  const r = card.result;
  const value = !card.override ? "" : card.override.mode === "audio" ? "audio" : `v${card.override.height || 0}`;
  const opts = [["", t("download.useSettings")]];
  if (r.kind === "single") {
    if (r.video) opts.push(["v0", `${t("download.best")}`], ...r.heights.map((h) => [`v${h}`, heightLabel(h)]));
  } else {
    opts.push(["v0", t("download.best")], ...dl.data.heights.map((h) => [`v${h}`, t("download.upToHeight", { height: heightLabel(h) })]));
  }
  opts.push(["audio", t("download.mode.audio")]);
  return `<label class="dl-override"><span class="sr-only">${te("download.custom")}</span><select data-dl-override="${card.key}">${opts.map(([v, text]) => `<option value="${v}" ${v === value ? "selected" : ""}>${escapeHtml(text)}</option>`).join("")}</select></label>`;
}

function dlCardHtml(card) {
  const remove = `<button class="icon-btn" type="button" data-dl-remove="${card.key}" title="${te("convert.removeFile")}" aria-label="${te("convert.removeFile")}">${icon("close", "sm")}</button>`;
  if (card.status === "loading") {
    return `<li class="dl-card loading" data-dl-card="${card.key}">
        <div class="dl-card-head"><span class="dl-thumb"><i class="dl-spinner"></i></span>
          <span class="cv-file-text"><span class="name">${te("download.probe.loading", { host: dlHost(card.url) })}</span>
          <span class="meta">${escapeHtml(card.url)}</span>${card.slow ? `<span class="hint">${te("download.probe.slow")}</span>` : ""}</span>${remove}</div></li>`;
  }
  if (card.status === "failed") {
    return `<li class="dl-card failed" data-dl-card="${card.key}">
        <div class="dl-card-head"><span class="dl-thumb">${icon("link")}</span>
          <span class="cv-file-text"><span class="name">${escapeHtml(card.url)}</span>
          <span class="bad-text">${escapeHtml(errorText(card.error || "download_failed"))}</span></span>
          <button class="btn sm" type="button" data-dl-again="${card.key}">${icon("refresh", "sm")}${te("convert.retry")}</button>${remove}</div></li>`;
  }
  const r = card.result;
  const options = dlCardOptions(card);
  if (r.kind === "playlist") {
    const all = card.selected.size === r.entries.length;
    return `<li class="dl-card" data-dl-card="${card.key}">
        <div class="dl-card-head"><span class="dl-thumb">${dlThumb(r.probe, r.entries[0]?.thumb) || icon("music")}</span>
          <span class="cv-file-text"><span class="name">${escapeHtml(r.title)}</span>
          <span class="meta" data-dl-summary="${card.key}">${escapeHtml(dlPlaylistSummary(card))}${r.truncated ? ` · ${te("download.truncated")}` : ""}</span></span>
          ${dlOverrideSelect(card)}${remove}</div>
        <div class="dl-playlist-tools">
          <button class="btn sm soft" type="button" data-dl-all="${card.key}">${te(all ? "download.selectNone" : "download.selectAll")}</button>
          <button class="btn sm soft" type="button" data-dl-invert="${card.key}">${te("download.invert")}</button>
          <input type="search" data-dl-filter="${card.key}" value="${escapeHtml(card.filter)}" placeholder="${te("download.filter")}" aria-label="${te("download.filter")}">
        </div>
        <ul class="dl-entries">${r.entries.map((e, i) => dlEntryRow(card, e, i)).join("")}</ul>
      </li>`;
  }
  const estimate = dlEstimate(r, options);
  const quality = options.mode === "audio" ? dlTargetLabel(dlOptionsPayload(card.override)) : `${options.container.toUpperCase()} · ${estimate.height ? `${estimate.height}p` : "–"}`;
  const size = estimate.bytes ? t("download.estimate", { size: fmtBytes(estimate.bytes) }) : t("download.estimateUnknown");
  const notes = [];
  if (options.mode === "video" && !r.video) notes.push(`<span class="warn-text">${te("download.noVideo")}</span>`);
  else if (options.mode === "video" && options.height && estimate.height && estimate.height < Number(options.height)) {
    notes.push(`<span class="warn-text">${te("download.fallback", { wanted: `${options.height}p`, actual: `${estimate.height}p` })}</span>`);
  }
  if (r.live) notes.push(`<span class="warn-text">${te("error.download_live")}</span>`);
  if (options.mode === "video") {
    const subs = [...new Set([...(r.subtitles || [])])];
    notes.push(`<span>${subs.length ? te("download.subsFound", { list: subs.slice(0, 12).join(", ") + (subs.length > 12 ? "…" : "") }) : te("download.subsNone")}</span>`);
  }
  const known = dlQueued(r.url, card.override);
  if (known) notes.push(`<span class="pill ${known.state === "done" ? "s-ok" : "s-run"}">${te(known.state === "done" ? "download.duplicate" : "download.inQueue")}</span>`);
  const facts = [r.site, r.uploader, r.duration ? fmtDuration(r.duration) : ""].filter(Boolean).join(" · ");
  return `<li class="dl-card" data-dl-card="${card.key}">
      <div class="dl-card-head"><span class="dl-thumb">${dlThumb(r.probe, r.thumb) || icon("video")}</span>
        <span class="cv-file-text"><span class="name" title="${escapeHtml(r.url)}">${escapeHtml(r.title)}</span>
        <span class="meta">${escapeHtml(facts)}</span>
        <span class="dl-chip"><b>${escapeHtml(quality)}</b> · ${escapeHtml(size)}${card.override ? ` · ${te("download.custom")}` : ""}</span>
        <span class="dl-notes">${notes.join("")}</span></span>
        ${dlOverrideSelect(card)}${remove}</div>
    </li>`;
}

function dlRenderInput() {
  const box = $("#dl-input");
  if (!box) return;
  const list = dl.cards.length ? `<ul class="dl-found">${dl.cards.map(dlCardHtml).join("")}</ul>` : `<p class="hint dl-empty">${te("download.emptyHint")}</p>`;
  box.classList.toggle("has-cards", dl.cards.length > 0);
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="dl-input-title">${te("download.links")}</h2>
      ${dl.cards.length ? `<button class="btn sm soft" type="button" data-dl="clear-found">${te("convert.clearFiles")}</button>` : ""}</div>
    <div class="dl-link-row">
      <textarea id="dl-url" rows="1" spellcheck="false" placeholder="${te("download.placeholder")}" aria-label="${te("download.links")}">${escapeHtml(dl.input)}</textarea>
      <button class="btn primary" type="button" data-dl="probe" ${dl.input.trim() ? "" : "disabled"}>${icon("plus", "sm")}${te("download.analyze")}</button>
      <button class="btn soft" type="button" data-dl="paste" title="${te("download.paste")}">${icon("clipboard", "sm")}<span class="dl-paste-label">${te("download.paste")}</span></button>
    </div>
    <div class="dl-input-foot">
      <label class="check-row inline"><input type="checkbox" data-dl-flag="auto" ${dl.auto ? "checked" : ""}><span>${te("download.auto")}</span></label>
      <details class="dl-sites"><summary>${te("download.sitesTitle")}</summary><p class="hint">${te("download.sites")}</p></details>
    </div>
    ${dl.auto ? `<p class="hint">${te("download.autoHint")}</p>` : ""}
    ${list}`;
  dlRenderStart();
}

function dlSelectHtml(key, label, value, options) {
  const opts = options.map(([v, text]) => `<option value="${escapeHtml(v)}" ${String(v) === String(value) ? "selected" : ""}>${escapeHtml(text)}</option>`).join("");
  return `<label class="cv-field"><span class="f-label">${te(label)}</span><select data-dl-option="${key}">${opts}</select></label>`;
}

function dlCheckHtml(key, label, hint = "") {
  return `<label class="check-row"><input type="checkbox" data-dl-option="${key}" ${dl.options[key] ? "checked" : ""}><span>${te(label)}${hint ? `<span class="hint">${te(hint)}</span>` : ""}</span></label>`;
}

function dlSubtitleChips() {
  const chosen = new Set(dlLanguages());
  const found = new Set(["cs", "en"]);
  for (const card of dl.cards) (card.result?.subtitles || []).forEach((l) => found.add(l));
  chosen.forEach((l) => found.add(l));
  return [...found].slice(0, 24).map((l) => `<button type="button" class="dl-lang ${chosen.has(l) ? "on" : ""}" data-dl-lang="${escapeHtml(l)}" aria-pressed="${chosen.has(l)}">${escapeHtml(l)}</button>`).join("");
}

function dlRenderOptions() {
  const box = $("#dl-options");
  if (!box || !dl.data) return;
  const o = dl.options;
  const modes = ["video", "audio"].map((m) => `<button type="button" role="tab" class="seg-btn ${o.mode === m ? "on" : ""}" data-dl-mode="${m}" aria-selected="${o.mode === m}">${icon(m === "video" ? "video" : "music", "sm")}${te(`download.mode.${m}`)}</button>`).join("");
  let main;
  let more;
  if (o.mode === "video") {
    const heights = [["", t("download.best")]].concat(dl.data.heights.map((h) => [h, t("download.upToHeight", { height: heightLabel(h) })]));
    const containers = dl.data.containers.map((c) => {
      const ok = dl.data.available_containers.includes(c);
      return `<button type="button" class="seg-btn ${o.container === c ? "on" : ""}" data-dl-container="${c}" aria-pressed="${o.container === c}" ${ok ? "" : `disabled title="${te("convert.needsCodecs")}"`}>${c.toUpperCase()}<small>${te(`download.container.${c}`)}</small></button>`;
    }).join("");
    main = `${dlSelectHtml("height", "download.quality", o.height || "", heights)}
      <div class="cv-field"><span class="f-label">${te("download.format")}</span><div class="seg-switch cv-quality dl-formats" role="group">${containers}</div></div>
      <p class="hint">${te(o.container === "mp4" ? "download.mp4Hint" : o.container === "webm" ? "download.webmHint" : "download.qualityHint")}</p>`;
    more = `${dlSelectHtml("subtitles", "download.subtitles", o.subtitles, [["none", t("download.subs.none")], ["embed", t("download.subs.embed")], ["files", t("download.subs.files")]])}
      ${o.subtitles !== "none" ? `<div class="cv-field"><span class="f-label">${te("download.languages")}</span><div class="dl-langs">${dlSubtitleChips()}</div>
        <input type="text" data-dl-option="languages" value="${escapeHtml(o.languages)}" placeholder="cs, en" spellcheck="false" autocomplete="off" aria-label="${te("download.languages")}"></div>
        <p class="hint">${te("download.languagesHint")}</p>
        ${dlCheckHtml("auto_subtitles", "download.autoSubtitles")}` : ""}
      ${dlCheckHtml("hdr", "download.hdr", "download.hdrHint")}
      ${dlCheckHtml("thumbnail", "download.thumbnailVideo")}`;
  } else {
    const formats = Object.keys(DL_AUDIO).map((f) => {
      const ok = dl.data.audio_formats.includes(f);
      return `<button type="button" class="cv-preset ${o.audio === f ? "on" : ""}" data-dl-audio="${f}" aria-pressed="${o.audio === f}" ${ok ? "" : `disabled title="${te("convert.needsCodecs")}"`}>
          <b>${escapeHtml(DL_AUDIO[f])}</b><span>${te(`download.audio.${f}`)}</span></button>`;
    }).join("");
    main = `<div class="cv-presets">${formats}</div>
      ${DL_LOSSY.has(o.audio) ? dlSelectHtml("bitrate", "download.bitrate", o.bitrate, dl.data.bitrates.map((b) => [b, `${b} kbps`])) : ""}
      <p class="hint">${te("download.audioHint")}</p>`;
    more = dlCheckHtml("thumbnail", "download.thumbnailAudio");
  }
  const folderInput = `<div class="cv-folder"><input type="text" data-dl-folder value="${escapeHtml(dl.folder)}" spellcheck="false" aria-label="${te("convert.folder")}" ${dl.outputMode === "folder" ? "" : "disabled"}>
      ${dl.data.picker ? `<button class="btn sm" type="button" data-dl="browse" ${dl.outputMode === "folder" ? "" : "disabled"}>${te("project.browse")}</button>` : ""}</div>`;
  const open = box.querySelector(".dl-more")?.open ? "open" : "";
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="dl-options-title">${te("download.options")}</h2></div>
    <div class="seg-switch cv-modes" role="tablist" aria-label="${te("download.options")}">${modes}</div>
    <div class="cv-target-body">${main}
      <div class="cv-field cv-output"><span class="f-label">${te("convert.output")}</span>
        <label class="check-row radio"><input type="radio" name="dl-output" value="default" ${dl.outputMode === "default" ? "checked" : ""}><span>${te("download.outputDefault")}<span class="hint path">${escapeHtml(dl.data.default_folder)}</span></span></label>
        <label class="check-row radio"><input type="radio" name="dl-output" value="folder" ${dl.outputMode === "folder" ? "checked" : ""}><span>${te("convert.outputFolder")}</span></label>
        ${folderInput}
        <p class="hint">${te("download.neverOverwrite")}</p>
      </div>
      <details class="cv-adjust dl-more" ${open}><summary>${te("download.more")}</summary><div class="cv-adjust-body">${more}
        <label class="check-row"><input type="checkbox" data-dl-flag="notify" ${dl.notify ? "checked" : ""}><span>${te("download.notify")}</span></label></div></details>
    </div>
    <div class="cv-start" id="dl-start"></div>`;
  dlRenderStart();
}

function dlChosen() {
  const chosen = [];
  for (const card of dl.cards) {
    if (card.status !== "ready") continue;
    const r = card.result;
    const options = dlCardOptions(card);
    const payload = card.override ? dlOptionsPayload(card.override) : null;
    if (r.kind === "playlist") {
      [...card.selected].sort((a, b) => a - b).forEach((i) => {
        const e = r.entries[i];
        chosen.push({ url: e.url, title: e.title, uploader: e.uploader, duration: e.duration, probe: r.probe, thumb: e.thumb, options: payload, bytes: dlEntryEstimate(e.duration, options), card: card.key });
      });
    } else if (options.mode === "audio" ? r.audio || r.video : r.video) {
      if (dlQueued(r.url, card.override)?.state === "queued" || dlQueued(r.url, card.override)?.state === "running") continue;
      chosen.push({ url: r.url, title: r.title, uploader: r.uploader, duration: r.duration, probe: r.probe, thumb: r.thumb, options: payload, bytes: dlEstimate(r, options).bytes, card: card.key });
    }
  }
  return chosen;
}

function dlDestination() {
  const folder = dl.outputMode === "folder" ? dl.folder.trim() : dl.data.default_folder;
  return folder;
}

function dlRenderStart() {
  const box = $("#dl-start");
  if (!box || !dl.data) return;
  const chosen = dlChosen();
  const bytes = chosen.reduce((sum, c) => sum + (c.bytes || 0), 0);
  const langs = dlLanguages();
  const loading = dl.cards.some((c) => c.status === "loading");
  let blocker = "";
  if (!dl.data.ready) blocker = t("convert.noFfmpeg");
  else if (!dl.cards.length) blocker = t("download.addFirst");
  else if (!chosen.length) blocker = t(loading ? "download.waitProbe" : dl.cards.every((c) => c.result?.kind === "single" && !c.result.video) && dl.options.mode === "video" ? "download.noVideoAny" : "download.nothingChosen");
  else if (dl.options.mode === "audio" && !dl.data.audio_formats.includes(dl.options.audio)) blocker = t("convert.needsCodecs");
  else if (dl.options.mode === "video" && dl.options.subtitles !== "none" && langs.some((l) => !DL_LANG.test(l))) blocker = t("download.badLanguage");
  else if (dl.outputMode === "folder" && !dl.folder.trim()) blocker = t("error.folder_required");
  const destination = t("download.target", { folder: dlDestination(), free: dl.free != null ? fmtBytes(dl.free) : "?" });
  const lowSpace = !blocker && bytes && dl.free != null && bytes > dl.free * 0.95;
  const summary = blocker || `${dlTargetLabel(dlOptionsPayload())}${bytes ? ` · ${t("download.estimate", { size: fmtBytes(bytes) })}` : ""}`;
  box.innerHTML = `${lowSpace ? `<p class="cv-warning">${te("download.lowSpace", { need: fmtBytes(bytes), free: fmtBytes(dl.free) })}</p>` : ""}
    <button class="run-btn" type="button" data-dl="start" ${blocker ? "disabled" : ""}>
      <span class="disc">${icon("download")}</span><span class="txt"><span>${te("download.run", { n: chosen.length })}</span>
      <small>${escapeHtml(summary)}</small>${blocker ? "" : `<small class="dl-dest">${escapeHtml(destination)}</small>`}</span></button>`;
}

function dlStatusFacts(item) {
  const pct = Math.round((item.progress || 0) * 100);
  const phase = item.phase && item.phase !== "downloading" ? t(`download.phase.${item.phase}`) : "";
  return [item.phase === "preparing" ? "" : `${pct} %`, item.speed ? `${fmtBytes(item.speed)}/s` : "", item.eta != null && item.phase === "downloading" ? t("convert.eta", { time: fmtDuration(item.eta) }) : "", phase].filter(Boolean).join(" · ");
}

function dlItemHtml(item) {
  const pct = Math.round((item.progress || 0) * 100);
  const busy = item.phase && item.phase !== "downloading";
  const status = item.state === "running"
    ? `<div class="row"><div class="progress ${busy ? "busy" : ""}"><i style="width:${Math.max(pct, 2)}%"></i></div><span class="num dim" data-dl-facts>${escapeHtml(dlStatusFacts(item))}</span></div>`
    : "";
  const target = item.state === "done" && item.output ? `→ ${item.output.split(/[\\/]/).pop()} · ${dlActualLabel(item)}` : dlTargetLabel(item.options);
  const extras = (item.extra_files || []).length ? ` · ${te("download.extraFiles", { n: item.extra_files.length })}` : "";
  const notes = (item.notes || []).map((n) => `<p class="cv-warning">${te(`download.note.${n}`)}</p>`).join("");
  const error = item.state === "failed"
    ? `<p class="bad-text">${escapeHtml(errorText(item.error || "download_failed"))}</p>${item.detail ? `<details class="cv-detail"><summary>${te("convert.details")}</summary><pre>${escapeHtml(item.detail)}</pre></details>` : ""}`
    : "";
  const actions = [];
  if (!DL_TERMINAL.has(item.state)) actions.push(`<button class="btn sm" type="button" data-dl-cancel="${item.id}">${te("app.cancel")}</button>`);
  if (item.state === "failed" || item.state === "cancelled") actions.push(`<button class="btn sm" type="button" data-dl-retry="${item.id}">${icon("refresh", "sm")}${te("convert.retry")}</button>`);
  if (item.state === "done") {
    actions.push(`<button class="btn sm" type="button" data-dl-play="${item.id}">${icon("play", "sm")}${te("download.play")}</button>`);
    actions.push(`<button class="btn sm" type="button" data-dl-reveal="${item.id}">${icon("folder", "sm")}${te("convert.reveal")}</button>`);
  }
  return `<li class="cv-item dl-item" data-dl-item="${item.id}">
      <div class="cv-item-head"><span class="dl-thumb sm">${dlThumb(item.probe, item.thumb) || icon(item.options.mode === "video" ? "video" : "music", "sm")}</span>
        <span class="cv-file-text"><span class="name" title="${escapeHtml(item.url)}">${escapeHtml(item.title)}</span>
          <span class="meta">${escapeHtml(target)}</span></span>
        <span class="pill ${DL_STATE_CLASS[item.state] || ""}" data-dl-pill>${te(`download.state.${item.state}`)}</span></div>
      ${status}
      <div class="cv-item-foot"><span class="meta num">${item.size ? escapeHtml(fmtBytes(item.size)) : ""}${extras}</span><span class="spacer"></span>${actions.join("")}</div>
      ${notes}${error}
    </li>`;
}

function dlOrdered() {
  const rank = { running: 0, queued: 1 };
  return [...dl.items.values()].sort((a, b) => (rank[a.state] ?? 2) - (rank[b.state] ?? 2) || (a.state === "queued" ? a.created - b.created : (b.finished || b.created) - (a.finished || a.created)));
}

function dlBatchStats() {
  const items = [...dl.items.values()].filter((i) => dl.batch && i.created >= dl.batch.since);
  const done = items.filter((i) => i.state === "done");
  const failed = items.filter((i) => i.state === "failed");
  const running = items.filter((i) => i.state === "running");
  const pending = items.filter((i) => !DL_TERMINAL.has(i.state));
  const speed = running.reduce((s, i) => s + (i.speed || 0), 0);
  const saved = done.reduce((s, i) => s + (i.size || 0), 0);
  const progress = items.length ? items.reduce((s, i) => s + (DL_TERMINAL.has(i.state) ? 1 : i.progress || 0), 0) / items.length : 0;
  const eta = running.reduce((m, i) => Math.max(m, i.eta || 0), 0);
  return { items, done, failed, pending, speed, saved, progress, eta };
}

function dlBatchHtml() {
  if (!dl.batch) return "";
  const s = dlBatchStats();
  if (!s.items.length) return "";
  if (s.pending.length) {
    const facts = [t("download.batch", { done: s.done.length, n: s.items.length }), s.failed.length ? t("download.batchFailed", { n: s.failed.length }) : "", s.saved ? fmtBytes(s.saved) : "", s.speed ? `${fmtBytes(s.speed)}/s` : "", s.eta ? t("convert.eta", { time: fmtDuration(s.eta) }) : "", dl.data.paused ? t("download.pausedNote") : ""].filter(Boolean).join(" · ");
    return `<div class="dl-batch"><div class="progress"><i style="width:${Math.max(Math.round(s.progress * 100), 2)}%"></i></div><span class="num">${escapeHtml(facts)}</span></div>`;
  }
  const text = s.failed.length
    ? t("download.batchDoneErrors", { done: s.done.length, failed: s.failed.length })
    : t("download.batchDone", { n: s.done.length, size: fmtBytes(s.saved), folder: dl.batch.folder });
  return `<div class="dl-batch done ${s.failed.length ? "has-errors" : ""}"><span>${escapeHtml(text)}</span><span class="spacer"></span>
      ${s.failed.length ? `<button class="btn sm" type="button" data-dl="retry-failed">${icon("refresh", "sm")}${te("download.retryFailed", { n: s.failed.length })}</button>` : ""}
      <button class="btn sm" type="button" data-dl="open-folder">${icon("folder", "sm")}${te("download.openFolder")}</button>
      <button class="icon-btn" type="button" data-dl="dismiss-batch" aria-label="${te("app.close")}">${icon("close", "sm")}</button></div>`;
}

function dlRenderQueue() {
  const box = $("#dl-queue");
  if (!box) return;
  const items = dlOrdered();
  const finished = items.filter((i) => DL_TERMINAL.has(i.state)).length;
  const active = items.length - finished;
  const limits = [["none", t("download.limitNone")]].concat(dl.data.limits.map((l) => [l, `${l.replace("K", " kB").replace("M", " MB")}/s`]));
  const scroll = $(".cv-items", box)?.scrollTop || 0;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="dl-queue-title">${te("download.queue")}</h2>
      ${active ? `<button class="btn sm soft" type="button" data-dl="pause">${icon(dl.data.paused ? "play" : "pause", "sm")}${te(dl.data.paused ? "download.resumeQueue" : "download.pauseQueue")}</button>` : ""}
      <label class="dl-limit" title="${te("download.limitHint")}"><span class="sr-only">${te("download.limit")}</span><select data-dl-limit>${limits.map(([v, text]) => `<option value="${v}" ${v === (dl.data.limit || "none") ? "selected" : ""}>${escapeHtml(text)}</option>`).join("")}</select></label>
      ${active > 1 ? `<button class="btn sm soft" type="button" data-dl="cancel-all">${te("convert.cancelAll")}</button>` : ""}
      ${finished ? `<button class="btn sm soft" type="button" data-dl="clear" title="${te("download.clearDoneHint")}">${te("download.clearDone")}</button>` : ""}
      <button class="btn sm" type="button" data-dl="open-folder">${icon("folder", "sm")}${te("download.openFolder")}</button></div>
    <div id="dl-batch">${dlBatchHtml()}</div>
    ${items.length ? `<ul class="cv-items">${items.map(dlItemHtml).join("")}</ul>` : `<p class="dim cv-empty">${te("download.queueEmpty")}</p>`}`;
  const list = $(".cv-items", box);
  if (list) list.scrollTop = scroll;
  dlUpdateTitle();
}

function dlUpdateBatch() {
  const box = $("#dl-batch");
  if (box) box.innerHTML = dlBatchHtml();
  dlUpdateTitle();
}

function dlUpdateTitle() {
  const s = dl.batch ? dlBatchStats() : null;
  const running = s && s.pending.length;
  document.title = running ? `(${s.done.length}/${s.items.length}) ${Math.round(s.progress * 100)} % · SpotCheck` : "SpotCheck";
  const tab = $('[data-app-tab="download"]');
  if (!tab) return;
  let badge = $(".app-badge", tab);
  const show = running && state.app !== "download";
  if (show && !badge) { badge = document.createElement("i"); badge.className = "app-badge"; tab.append(badge); }
  if (badge) { if (show) badge.textContent = String(s.pending.length); else badge.remove(); }
}

function dlPatchItem(item, el) {
  const facts = $("[data-dl-facts]", el);
  const bar = $(".progress i", el);
  if (!facts || !bar) return false;
  facts.textContent = dlStatusFacts(item);
  bar.style.width = `${Math.max(Math.round((item.progress || 0) * 100), 2)}%`;
  $(".progress", el).classList.toggle("busy", Boolean(item.phase && item.phase !== "downloading"));
  return true;
}

function dlUpdateItem(item) {
  const previous = dl.items.get(item.id);
  dl.items.set(item.id, item);
  const el = $(`[data-dl-item="${CSS.escape(item.id)}"]`);
  if (!(previous && el && previous.state === item.state && item.state === "running" && dlPatchItem(item, el))) {
    if (!previous || previous.state !== item.state) dlRenderQueue();
    else if (el) el.outerHTML = dlItemHtml(item);
  }
  dlUpdateBatch();
  if (previous && !DL_TERMINAL.has(previous.state) && DL_TERMINAL.has(item.state)) {
    if (item.state === "failed") say(t("convert.toast.failed", { name: item.title, error: errorText(item.error || "download_failed") }), true);
    dlFinishBatch();
    if (dl.cards.length) dlRenderInput();
  }
}

function dlFinishBatch() {
  if (!dl.batch || dl.batch.notified) return;
  const s = dlBatchStats();
  if (s.pending.length || !s.items.length) return;
  dl.batch.notified = true;
  const text = s.failed.length
    ? t("download.batchDoneErrors", { done: s.done.length, failed: s.failed.length })
    : t("download.batchDone", { n: s.done.length, size: fmtBytes(s.saved), folder: dl.batch.folder });
  say(text, s.failed.length > 0);
  if (dl.notify && "Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification(t("download.notifyTitle"), { body: text }); } catch {}
  }
}

function dlOnEvent(event) {
  if (!dl.loaded) return;
  if (event.type === "download") dlUpdateItem(event.item);
  else if (event.type === "download_list") dlReloadItems();
  else if (event.type === "download_settings") {
    dl.data.paused = event.paused;
    dl.data.limit = event.limit;
    dlRenderQueue();
  }
}

function dlPump() {
  while (dl.active < DL_PROBES && dl.waiting.length) {
    const card = dl.waiting.shift();
    dl.active += 1;
    const slow = setTimeout(() => { card.slow = true; dlPatchCard(card); }, DL_SLOW);
    api("/api/download/probe", "POST", { url: card.url })
      .then((result) => {
        card.result = result;
        card.status = "ready";
        if (result.kind === "playlist") {
          card.selected = new Set(result.entries.map((e, i) => (dlQueued(e.url, null)?.state === "done" ? -1 : i)).filter((i) => i >= 0));
        }
        if (dl.cards.some((c) => c !== card && c.status === "ready" && c.result.url === result.url && c.result.kind === result.kind)) {
          dl.cards = dl.cards.filter((c) => c !== card);
          say(t("download.alreadyListed"));
        }
      })
      .catch((error) => { card.status = "failed"; card.error = error.code || error.message; })
      .finally(() => {
        clearTimeout(slow);
        dl.active -= 1;
        dlRenderInput();
        if (dl.auto && dl.cards.includes(card) && card.status === "ready" && card.result.kind === "single") dlStart([card]);
        dlPump();
      });
  }
}

function dlPatchCard(card) {
  const el = $(`[data-dl-card="${CSS.escape(card.key)}"]`);
  if (el) el.outerHTML = dlCardHtml(card);
}

function dlAddLinks(text) {
  const words = String(text).split(/\s+/).map((x) => x.trim().replace(/^[<("']+|[>)"',.;!?]+$/g, "")).filter(Boolean);
  const linkish = words.filter((w) => /^https?:\/\//i.test(w) || /^(www\.)?[\w-]+(\.[\w-]+)+\/\S*/i.test(w));
  const links = [...new Set(linkish.length ? linkish : words)];
  if (!links.length) return;
  const known = new Set(dl.cards.map((c) => c.url));
  for (const url of links) {
    if (known.has(url)) continue;
    const card = { key: `d${++dl.seq}`, url, status: "loading", result: null, error: null, selected: new Set(), filter: "", override: null, slow: false };
    dl.cards.push(card);
    dl.waiting.push(card);
  }
  dl.input = "";
  dlRenderInput();
  dlPump();
}

async function dlStart(cards = null) {
  const pool = cards || dl.cards;
  const saved = dl.cards;
  dl.cards = pool;
  const entries = dlChosen();
  dl.cards = saved;
  if (!entries.length) return;
  const button = $('[data-dl="start"]');
  if (button) button.disabled = true;
  const pending = [...dl.items.values()].some((i) => !DL_TERMINAL.has(i.state));
  if (!dl.batch || !pending) dl.batch = { since: Date.now() / 1000 - 1, folder: dlDestination(), notified: false };
  else dl.batch.notified = false;
  try {
    const folder = dl.outputMode === "folder" ? dl.folder.trim() : null;
    const payload = entries.map(({ bytes, card, ...rest }) => rest);
    const { items } = await api("/api/download", "POST", { entries: payload, options: dlOptionsPayload(), folder });
    const started = new Set(entries.map((e) => e.card));
    dl.cards = dl.cards.filter((c) => !started.has(c.key));
    say(t("download.toast.started", { n: new Set(items).size }));
    dlRemember();
    dlRenderInput();
  } catch (error) {
    sayError(error);
    dlRenderStart();
  }
}

async function dlPaste() {
  try {
    const text = await navigator.clipboard.readText();
    if (text.trim()) dlAddLinks(text);
  } catch {
    say(t("download.pasteFailed"), true);
    $("#dl-url")?.focus();
  }
}

async function dlBrowse() {
  try {
    const { path } = await api("/api/pick-folder", "POST", { initial: dl.folder || null });
    if (path) { dl.folder = path; dlRemember(); dlRenderOptions(); dlRefreshSpace(); }
  } catch (error) {
    sayError(error);
  }
}

function dlSetLanguages(list) {
  dl.options.languages = [...new Set(list)].join(", ");
  dlRemember();
}

function dlPickEntries(card, index, checked, range) {
  const from = range && dl.lastPick && dl.lastPick.key === card.key ? Math.min(dl.lastPick.index, index) : index;
  const to = range && dl.lastPick && dl.lastPick.key === card.key ? Math.max(dl.lastPick.index, index) : index;
  for (let i = from; i <= to; i += 1) {
    if (checked) card.selected.add(i); else card.selected.delete(i);
    const box = $(`[data-dl-pick="${CSS.escape(`${card.key}:${i}`)}"]`);
    if (box) box.checked = checked;
  }
  dl.lastPick = { key: card.key, index };
  const summary = $(`[data-dl-summary="${CSS.escape(card.key)}"]`);
  if (summary) summary.textContent = dlPlaylistSummary(card);
  const all = $(`[data-dl-all="${CSS.escape(card.key)}"]`);
  if (all) all.textContent = t(card.selected.size === card.result.entries.length ? "download.selectNone" : "download.selectAll");
  dlRenderStart();
}

$("#downloader").addEventListener("click", (event) => {
  const pick = event.target.closest("[data-dl-pick]");
  if (pick && event.shiftKey) {
    const [key, index] = pick.dataset.dlPick.split(":");
    const card = dl.cards.find((c) => c.key === key);
    if (card) dlPickEntries(card, Number(index), pick.checked, true);
    return;
  }
  const el = event.target.closest("[data-dl],[data-dl-mode],[data-dl-container],[data-dl-audio],[data-dl-remove],[data-dl-again],[data-dl-all],[data-dl-invert],[data-dl-lang],[data-dl-cancel],[data-dl-retry],[data-dl-reveal],[data-dl-play]");
  if (!el || el.disabled) return;
  const d = el.dataset;
  const cardOf = (key) => dl.cards.find((c) => c.key === key);
  if (d.dlMode) { dl.options.mode = d.dlMode; dlRemember(); dlRenderOptions(); dlRenderInput(); return; }
  if (d.dlContainer) { dl.options.container = d.dlContainer; dlRemember(); dlRenderOptions(); dlRenderInput(); return; }
  if (d.dlAudio) { dl.options.audio = d.dlAudio; dlRemember(); dlRenderOptions(); dlRenderInput(); return; }
  if (d.dlLang) {
    const list = dlLanguages();
    dlSetLanguages(list.includes(d.dlLang) ? list.filter((l) => l !== d.dlLang) : [...list, d.dlLang]);
    dlRenderOptions();
    return;
  }
  if (d.dlRemove) {
    dl.waiting = dl.waiting.filter((c) => c.key !== d.dlRemove);
    dl.cards = dl.cards.filter((c) => c.key !== d.dlRemove);
    dlRenderInput();
    return;
  }
  if (d.dlAgain) {
    const card = cardOf(d.dlAgain);
    if (card) { Object.assign(card, { status: "loading", error: null, slow: false }); dl.waiting.push(card); dlRenderInput(); dlPump(); }
    return;
  }
  if (d.dlAll || d.dlInvert) {
    const card = cardOf(d.dlAll || d.dlInvert);
    if (!card) return;
    const every = card.result.entries.map((_, i) => i);
    card.selected = d.dlAll
      ? (card.selected.size === every.length ? new Set() : new Set(every))
      : new Set(every.filter((i) => !card.selected.has(i)));
    dlPatchCard(card);
    dlRenderStart();
    return;
  }
  if (d.dlCancel) { api(`/api/download/${enc(d.dlCancel)}/cancel`, "POST").catch(sayError); return; }
  if (d.dlRetry) { api(`/api/download/${enc(d.dlRetry)}/retry`, "POST").catch(sayError); return; }
  if (d.dlReveal) { api(`/api/download/${enc(d.dlReveal)}/reveal`, "POST").catch(sayError); return; }
  if (d.dlPlay) { api(`/api/download/${enc(d.dlPlay)}/open`, "POST").catch(sayError); return; }
  switch (d.dl) {
    case "probe": dlAddLinks($("#dl-url")?.value || dl.input); break;
    case "paste": dlPaste(); break;
    case "clear-found": dl.cards = []; dl.waiting = []; dlRenderInput(); break;
    case "start": dlStart(); break;
    case "browse": dlBrowse(); break;
    case "clear": api("/api/download/clear", "POST").catch(sayError); break;
    case "cancel-all": api("/api/download/cancel-all", "POST").catch(sayError); break;
    case "pause": api("/api/download/settings", "POST", { paused: !dl.data.paused }).catch(sayError); break;
    case "dismiss-batch": dl.batch = null; dlUpdateBatch(); break;
    case "retry-failed":
      dlBatchStats().failed.forEach((i) => api(`/api/download/${enc(i.id)}/retry`, "POST").catch(() => {}));
      if (dl.batch) dl.batch.notified = false;
      break;
    case "open-folder": api("/api/download/open-folder", "POST", { initial: dl.outputMode === "folder" ? dl.folder.trim() || null : null }).catch(sayError); break;
    default: break;
  }
});

$("#downloader").addEventListener("input", (event) => {
  const el = event.target;
  if (el.id === "dl-url") {
    dl.input = el.value;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    const button = $('[data-dl="probe"]');
    if (button) button.disabled = !dl.input.trim();
    return;
  }
  if (el.dataset.dlFilter) {
    const card = dl.cards.find((c) => c.key === el.dataset.dlFilter);
    if (!card) return;
    card.filter = el.value;
    const needle = el.value.toLowerCase();
    $$(".dl-entry", el.closest(".dl-card")).forEach((row, i) => { row.hidden = Boolean(needle) && !card.result.entries[i].title.toLowerCase().includes(needle); });
    return;
  }
  if (el.dataset.dlFolder !== undefined) { dl.folder = el.value; dlRemember(); dlRenderStart(); return; }
  if (el.dataset.dlOption === "languages") { dl.options.languages = el.value; dlRemember(); dlRenderStart(); }
});

$("#downloader").addEventListener("change", (event) => {
  const el = event.target;
  if (el.name === "dl-output") { dl.outputMode = el.value; dlRemember(); dlRenderOptions(); dlRefreshSpace(); return; }
  if (el.dataset.dlFolder !== undefined) { dlRefreshSpace(); return; }
  if (el.dataset.dlPick) {
    const [key, index] = el.dataset.dlPick.split(":");
    const card = dl.cards.find((c) => c.key === key);
    if (card) dlPickEntries(card, Number(index), el.checked, false);
    return;
  }
  if (el.dataset.dlOverride) {
    const card = dl.cards.find((c) => c.key === el.dataset.dlOverride);
    if (!card) return;
    const v = el.value;
    card.override = !v ? null : v === "audio" ? { mode: "audio" } : { mode: "video", height: Number(v.slice(1)) || null };
    dlPatchCard(card);
    dlRenderStart();
    return;
  }
  if (el.dataset.dlLimit !== undefined) { api("/api/download/settings", "POST", { limit: el.value }).catch(sayError); return; }
  if (el.dataset.dlFlag) {
    const flag = el.dataset.dlFlag;
    if (flag === "notify" && el.checked && "Notification" in window && Notification.permission !== "granted") {
      Notification.requestPermission().then((p) => { dl.notify = p === "granted"; el.checked = dl.notify; dlRemember(); });
      return;
    }
    dl[flag] = el.checked;
    dlRemember();
    if (flag === "auto") dlRenderInput();
    return;
  }
  const key = el.dataset.dlOption;
  if (!key || key === "languages") return;
  dl.options[key] = el.type === "checkbox" ? el.checked : key === "height" ? (el.value ? Number(el.value) : null) : el.value;
  dlRemember();
  if (key === "subtitles") dlRenderOptions();
  dlRenderInput();
});

$("#downloader").addEventListener("keydown", (event) => {
  if (event.target.id === "dl-url" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    dlAddLinks(event.target.value);
  }
});

document.addEventListener("paste", (event) => {
  if (state.app !== "download" || !dl.loaded) return;
  const target = event.target;
  const typing = target.closest?.("input, textarea, select") && target.id !== "dl-url";
  if (typing || document.querySelector("dialog[open]")) return;
  const text = event.clipboardData?.getData("text") || "";
  if (!/https?:\/\/|\.[a-z]{2,}\//i.test(text)) return;
  event.preventDefault();
  dlAddLinks(target.id === "dl-url" ? `${target.value}\n${text}` : text);
});

document.addEventListener("error", (event) => {
  const img = event.target;
  if (img instanceof HTMLImageElement && img.dataset.fallback === "thumb") img.remove();
}, true);

dlRestore();

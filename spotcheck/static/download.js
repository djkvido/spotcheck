"use strict";

const DOWNLOAD_KEY = "spotcheck-download";
const DL_TERMINAL = new Set(["done", "failed", "cancelled"]);
const DL_STATE_CLASS = { queued: "s-missing", running: "s-run", done: "s-ok", failed: "s-bad", cancelled: "s-missing" };
const DL_AUDIO = { mp3: "MP3", m4a: "M4A (AAC)", opus: "Opus", ogg: "OGG Vorbis", flac: "FLAC", wav: "WAV" };
const DL_LOSSY = new Set(["mp3", "m4a"]);
const DL_HEIGHT_NAMES = { 4320: "8K", 2160: "4K", 1440: "2K" };
ICONS.video = '<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>';
ICONS.clipboard = '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>';

const dl = {
  loaded: false,
  data: null,
  input: "",
  probing: 0,
  found: [],
  items: new Map(),
  seq: 0,
  options: { mode: "video", container: "mp4", height: 1080, hdr: false, audio: "mp3", bitrate: "320", subtitles: "none", languages: "cs, en", auto_subtitles: false, thumbnail: true },
  outputMode: "default",
  folder: "",
  batchStarted: 0,
};

function dlRestore() {
  try {
    const saved = JSON.parse(localStorage.getItem(DOWNLOAD_KEY) || "{}");
    if (saved.options && typeof saved.options === "object") Object.assign(dl.options, saved.options);
    if (["default", "folder"].includes(saved.outputMode)) dl.outputMode = saved.outputMode;
    if (typeof saved.folder === "string") dl.folder = saved.folder;
  } catch {}
}

function dlRemember() {
  const { options, outputMode, folder } = dl;
  try { localStorage.setItem(DOWNLOAD_KEY, JSON.stringify({ options, outputMode, folder })); } catch {}
}

const dlThumb = (probe, index) => (probe && index != null
  ? `<img src="/api/download/thumb/${enc(probe)}/${enc(index)}" alt="" loading="lazy" data-fallback="thumb">`
  : "");
const heightLabel = (h) => (h ? `${h}p${DL_HEIGHT_NAMES[h] ? ` · ${DL_HEIGHT_NAMES[h]}` : ""}` : t("download.best"));

function dlLanguages() {
  return dl.options.languages.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
}

function dlOptionsPayload() {
  const o = dl.options;
  return {
    mode: o.mode,
    container: o.container,
    height: o.height ? Number(o.height) : null,
    hdr: Boolean(o.hdr),
    audio: o.audio,
    bitrate: String(o.bitrate),
    subtitles: o.subtitles,
    languages: dlLanguages(),
    auto_subtitles: Boolean(o.auto_subtitles),
    thumbnail: Boolean(o.thumbnail),
  };
}

function dlTargetLabel(options) {
  if (!options) return "";
  if (options.mode === "audio") {
    return `${DL_AUDIO[options.audio] || options.audio}${DL_LOSSY.has(options.audio) ? ` · ${options.bitrate} kbps` : ""}`;
  }
  const subs = options.subtitles !== "none" ? ` · ${t("download.withSubtitles")}` : "";
  return `${String(options.container).toUpperCase()} · ${heightLabel(options.height)}${options.hdr ? " · HDR" : ""}${subs}`;
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
  dlRender();
}

function dlRender() {
  if (!dl.loaded) {
    $("#downloader").innerHTML = `<section class="panel welcome"><div class="dropzone"><p>${te("download.loading")}</p></div></section>`;
    return;
  }
  $("#downloader").innerHTML = `
    <div class="cv-main">
      <section class="panel dl-input" id="dl-input" aria-labelledby="dl-input-title"></section>
      <section class="panel cv-queue" id="dl-queue" aria-labelledby="dl-queue-title"></section>
    </div>
    <section class="panel cv-target" id="dl-options" aria-labelledby="dl-options-title"></section>`;
  dlRenderInput();
  dlRenderOptions();
  dlRenderQueue();
}

function dlEntryRow(found, entry, index) {
  const checked = found.selected.has(index);
  return `<li class="dl-entry">
      <label class="check-row"><input type="checkbox" data-dl-pick="${found.key}:${index}" ${checked ? "checked" : ""}>
        <span class="dl-entry-text"><span class="name">${escapeHtml(entry.title)}</span>
        <span class="meta">${escapeHtml([entry.uploader, entry.duration ? fmtDuration(entry.duration) : ""].filter(Boolean).join(" · "))}</span></span></label>
    </li>`;
}

function dlFoundCard(found) {
  const r = found.result;
  const remove = `<button class="icon-btn" type="button" data-dl-remove="${found.key}" title="${te("convert.removeFile")}" aria-label="${te("convert.removeFile")}">${icon("close", "sm")}</button>`;
  if (r.kind === "playlist") {
    const all = found.selected.size === r.entries.length;
    return `<li class="dl-card" data-dl-found="${found.key}">
        <div class="dl-card-head"><span class="dl-thumb">${dlThumb(r.probe, r.entries[0]?.thumb) || icon("music")}</span>
          <span class="cv-file-text"><span class="name">${escapeHtml(r.title)}</span>
          <span class="meta">${te("download.playlistMeta", { n: r.entries.length, chosen: found.selected.size })}${r.truncated ? ` · ${te("download.truncated")}` : ""}</span></span>
          <button class="btn sm soft" type="button" data-dl-all="${found.key}">${te(all ? "download.selectNone" : "download.selectAll")}</button>${remove}</div>
        <ul class="dl-entries">${r.entries.map((e, i) => dlEntryRow(found, e, i)).join("")}</ul>
      </li>`;
  }
  const facts = [r.site, r.uploader, r.duration ? fmtDuration(r.duration) : "", r.heights.length ? `${t("download.upTo")} ${heightLabel(r.heights[0])}` : t("download.audioOnly")];
  const warn = dl.options.mode === "video" && !r.video ? `<span class="pill s-review">${te("download.noVideo")}</span>` : "";
  return `<li class="dl-card" data-dl-found="${found.key}">
      <div class="dl-card-head"><span class="dl-thumb">${dlThumb(r.probe, r.thumb) || icon("video")}</span>
        <span class="cv-file-text"><span class="name" title="${escapeHtml(r.url)}">${escapeHtml(r.title)}</span>
        <span class="meta">${escapeHtml(facts.filter(Boolean).join(" · "))}</span></span>${warn}${remove}</div>
    </li>`;
}

function dlRenderInput() {
  const box = $("#dl-input");
  if (!box) return;
  const busy = dl.probing > 0;
  const list = dl.found.length ? `<ul class="dl-found">${dl.found.map(dlFoundCard).join("")}</ul>` : `<p class="hint dl-empty">${te("download.emptyHint")}</p>`;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="dl-input-title">${te("download.links")}</h2>
      ${dl.found.length ? `<button class="btn sm soft" type="button" data-dl="clear-found">${te("convert.clearFiles")}</button>` : ""}</div>
    <div class="dl-link-row">
      <textarea id="dl-url" rows="2" spellcheck="false" placeholder="${te("download.placeholder")}" aria-label="${te("download.links")}">${escapeHtml(dl.input)}</textarea>
      <div class="dl-link-actions">
        <button class="btn primary" type="button" data-dl="probe" ${busy || !dl.input.trim() ? "disabled" : ""}>${icon("search", "sm")}${te(busy ? "download.analyzing" : "download.analyze")}</button>
        <button class="btn soft" type="button" data-dl="paste">${icon("clipboard", "sm")}${te("download.paste")}</button>
      </div>
    </div>
    <p class="hint">${te("download.sites")}</p>
    ${list}`;
  dlRenderStart();
}

function dlSelectHtml(key, label, value, options, disabled = false) {
  const opts = options.map(([v, text]) => `<option value="${escapeHtml(v)}" ${String(v) === String(value) ? "selected" : ""}>${escapeHtml(text)}</option>`).join("");
  return `<label class="cv-field"><span class="f-label">${te(label)}</span><select data-dl-option="${key}" ${disabled ? "disabled" : ""}>${opts}</select></label>`;
}

function dlCheckHtml(key, label, hint = "") {
  return `<label class="check-row"><input type="checkbox" data-dl-option="${key}" ${dl.options[key] ? "checked" : ""}><span>${te(label)}${hint ? `<span class="hint">${te(hint)}</span>` : ""}</span></label>`;
}

function dlRenderOptions() {
  const box = $("#dl-options");
  if (!box || !dl.data) return;
  const o = dl.options;
  const modes = ["video", "audio"].map((m) => `<button type="button" role="tab" class="seg-btn ${o.mode === m ? "on" : ""}" data-dl-mode="${m}" aria-selected="${o.mode === m}">${icon(m === "video" ? "video" : "music", "sm")}${te(`download.mode.${m}`)}</button>`).join("");
  let body = "";
  if (o.mode === "video") {
    const heights = [["", t("download.best")]].concat(dl.data.heights.map((h) => [h, heightLabel(h)]));
    const containers = dl.data.containers.map((c) => `<button type="button" class="seg-btn ${o.container === c ? "on" : ""}" data-dl-container="${c}" aria-pressed="${o.container === c}">${c.toUpperCase()}<small>${te(`download.container.${c}`)}</small></button>`).join("");
    body = `${dlSelectHtml("height", "download.quality", o.height || "", heights)}
      <p class="hint">${te(o.container === "mp4" ? "download.mp4Hint" : "download.qualityHint")}</p>
      <div class="cv-field"><span class="f-label">${te("download.format")}</span><div class="seg-switch cv-quality dl-formats" role="group">${containers}</div></div>
      ${dlCheckHtml("hdr", "download.hdr", "download.hdrHint")}
      ${dlSelectHtml("subtitles", "download.subtitles", o.subtitles, [["none", t("download.subs.none")], ["embed", t("download.subs.embed")], ["files", t("download.subs.files")]])}
      ${o.subtitles !== "none" ? `<label class="cv-field"><span class="f-label">${te("download.languages")}</span><input type="text" data-dl-option="languages" value="${escapeHtml(o.languages)}" placeholder="cs, en" spellcheck="false" autocomplete="off"></label>
        <p class="hint" id="dl-lang-hint">${te("download.languagesHint")}</p>
        ${dlCheckHtml("auto_subtitles", "download.autoSubtitles")}` : ""}`;
  } else {
    const formats = Object.keys(DL_AUDIO).map((f) => {
      const ok = dl.data.audio_formats.includes(f);
      return `<button type="button" class="cv-preset ${o.audio === f ? "on" : ""}" data-dl-audio="${f}" aria-pressed="${o.audio === f}" ${ok ? "" : `disabled title="${te("convert.needsCodecs")}"`}>
          <b>${escapeHtml(DL_AUDIO[f])}</b><span>${te(`download.audio.${f}`)}</span></button>`;
    }).join("");
    body = `<div class="cv-presets">${formats}</div>
      ${DL_LOSSY.has(o.audio) ? dlSelectHtml("bitrate", "download.bitrate", o.bitrate, dl.data.bitrates.map((b) => [b, `${b} kbps`])) : ""}
      <p class="hint">${te("download.audioHint")}</p>`;
  }
  const folderInput = `<div class="cv-folder"><input type="text" data-dl-folder value="${escapeHtml(dl.folder)}" spellcheck="false" aria-label="${te("convert.folder")}" ${dl.outputMode === "folder" ? "" : "disabled"}>
      ${dl.data.picker ? `<button class="btn sm" type="button" data-dl="browse" ${dl.outputMode === "folder" ? "" : "disabled"}>${te("project.browse")}</button>` : ""}</div>`;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="dl-options-title">${te("download.options")}</h2></div>
    <div class="seg-switch cv-modes" role="tablist" aria-label="${te("download.options")}">${modes}</div>
    <div class="cv-target-body">${body}
      ${dlCheckHtml("thumbnail", o.mode === "video" ? "download.thumbnailVideo" : "download.thumbnailAudio")}
      <div class="cv-field cv-output"><span class="f-label">${te("convert.output")}</span>
        <label class="check-row radio"><input type="radio" name="dl-output" value="default" ${dl.outputMode === "default" ? "checked" : ""}><span>${te("download.outputDefault")}<span class="hint path">${escapeHtml(dl.data.default_folder)}</span></span></label>
        <label class="check-row radio"><input type="radio" name="dl-output" value="folder" ${dl.outputMode === "folder" ? "checked" : ""}><span>${te("convert.outputFolder")}</span></label>
        ${folderInput}
        <p class="hint">${te("download.neverOverwrite")}</p>
      </div>
    </div>
    <div class="cv-start" id="dl-start"></div>`;
  dlRenderStart();
}

function dlChosen() {
  const chosen = [];
  for (const found of dl.found) {
    const r = found.result;
    if (r.kind === "playlist") {
      r.entries.forEach((e, i) => {
        if (found.selected.has(i)) chosen.push({ url: e.url, title: e.title, uploader: e.uploader, duration: e.duration, probe: r.probe, thumb: e.thumb });
      });
    } else if (dl.options.mode === "audio" ? r.audio || r.video : r.video) {
      chosen.push({ url: r.url, title: r.title, uploader: r.uploader, duration: r.duration, probe: r.probe, thumb: r.thumb });
    }
  }
  return chosen;
}

function dlRenderStart() {
  const box = $("#dl-start");
  if (!box || !dl.data) return;
  const chosen = dlChosen();
  const langs = dlLanguages();
  let blocker = "";
  if (!dl.data.ready) blocker = t("convert.noFfmpeg");
  else if (!dl.found.length) blocker = t("download.addFirst");
  else if (!chosen.length) blocker = t("download.nothingChosen");
  else if (dl.options.mode === "audio" && !dl.data.audio_formats.includes(dl.options.audio)) blocker = t("convert.needsCodecs");
  else if (dl.options.mode === "video" && dl.options.subtitles !== "none" && langs.some((l) => !/^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$/.test(l))) blocker = t("download.badLanguage");
  else if (dl.outputMode === "folder" && !dl.folder.trim()) blocker = t("error.folder_required");
  box.innerHTML = `<button class="run-btn" type="button" data-dl="start" ${blocker ? "disabled" : ""}>
      <span class="disc">${icon("download")}</span><span class="txt"><span>${te("download.run", { n: chosen.length })}</span>
      <small>${escapeHtml(blocker || dlTargetLabel(dlOptionsPayload()))}</small></span></button>`;
}

function dlItemHtml(item) {
  const pct = Math.round((item.progress || 0) * 100);
  let status = "";
  if (item.state === "running") {
    const phase = item.phase && item.phase !== "downloading" ? te(`download.phase.${item.phase}`) : "";
    const facts = [`${pct} %`, item.speed ? `${fmtBytes(item.speed)}/s` : "", item.eta != null && item.phase === "downloading" ? te("convert.eta", { time: fmtDuration(item.eta) }) : "", phase].filter(Boolean).join(" · ");
    status = `<div class="row"><div class="progress ${item.phase && item.phase !== "downloading" ? "busy" : ""}"><i style="width:${Math.max(pct, 2)}%"></i></div><span class="num dim">${escapeHtml(facts)}</span></div>`;
  }
  const name = item.output ? item.output.split(/[\\/]/).pop() : `${item.title}.${item.ext}`;
  const extras = (item.extra_files || []).length ? ` · ${te("download.extraFiles", { n: item.extra_files.length })}` : "";
  const error = item.state === "failed"
    ? `<p class="bad-text">${escapeHtml(errorText(item.error || "download_failed"))}</p>${item.detail ? `<details class="cv-detail"><summary>${te("convert.details")}</summary><pre>${escapeHtml(item.detail)}</pre></details>` : ""}`
    : "";
  const actions = [];
  if (!DL_TERMINAL.has(item.state)) actions.push(`<button class="btn sm" type="button" data-dl-cancel="${item.id}">${te("app.cancel")}</button>`);
  if (item.state === "failed" || item.state === "cancelled") actions.push(`<button class="btn sm" type="button" data-dl-retry="${item.id}">${icon("refresh", "sm")}${te("convert.retry")}</button>`);
  if (item.state === "done") actions.push(`<button class="btn sm" type="button" data-dl-reveal="${item.id}">${icon("folder", "sm")}${te("convert.reveal")}</button>`);
  return `<li class="cv-item" data-dl-item="${item.id}">
      <div class="cv-item-head"><span class="dl-thumb sm">${dlThumb(item.probe, item.thumb) || icon(item.options.mode === "video" ? "video" : "music", "sm")}</span>
        <span class="cv-file-text"><span class="name" title="${escapeHtml(item.url)}">${escapeHtml(item.title)}</span>
          <span class="meta">→ ${escapeHtml(name)} · ${escapeHtml(dlTargetLabel(item.options))}</span></span>
        <span class="pill ${DL_STATE_CLASS[item.state] || ""}">${te(`download.state.${item.state}`)}</span></div>
      ${status}
      <div class="cv-item-foot"><span class="meta num">${item.size ? escapeHtml(fmtBytes(item.size)) : ""}${extras}</span><span class="spacer"></span>${actions.join("")}</div>
      ${error}
    </li>`;
}

function dlRenderQueue() {
  const box = $("#dl-queue");
  if (!box) return;
  const items = [...dl.items.values()].sort((a, b) => b.created - a.created);
  const finished = items.filter((i) => DL_TERMINAL.has(i.state)).length;
  const active = items.length - finished;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="dl-queue-title">${te("download.queue")}</h2>
      ${active ? `<span class="dim num">${te("convert.active", { n: active })}</span>` : ""}
      ${active > 1 ? `<button class="btn sm soft" type="button" data-dl="cancel-all">${te("convert.cancelAll")}</button>` : ""}
      ${finished ? `<button class="btn sm soft" type="button" data-dl="clear">${te("convert.clearDone")}</button>` : ""}
      <button class="btn sm" type="button" data-dl="open-folder">${icon("folder", "sm")}${te("download.openFolder")}</button></div>
    ${items.length ? `<ul class="cv-items">${items.map(dlItemHtml).join("")}</ul>` : `<p class="dim cv-empty">${te("download.queueEmpty")}</p>`}`;
}

function dlUpdateItem(item) {
  const previous = dl.items.get(item.id);
  dl.items.set(item.id, item);
  const el = $(`[data-dl-item="${CSS.escape(item.id)}"]`);
  if (!previous || !el || previous.state !== item.state) dlRenderQueue();
  else el.outerHTML = dlItemHtml(item);
  if (previous && !DL_TERMINAL.has(previous.state) && DL_TERMINAL.has(item.state)) {
    const pending = [...dl.items.values()].some((i) => !DL_TERMINAL.has(i.state));
    if (item.state === "failed") say(t("convert.toast.failed", { name: item.title, error: errorText(item.error || "download_failed") }), true);
    else if (!pending) {
      const done = [...dl.items.values()].filter((i) => i.state === "done" && i.finished >= (dl.batchStarted || 0)).length;
      say(t("download.toast.allDone", { n: done }));
    }
  }
}

function dlOnEvent(event) {
  if (!dl.loaded) return;
  if (event.type === "download") dlUpdateItem(event.item);
  else if (event.type === "download_list") dlLoad();
}

async function dlProbe() {
  const links = [...new Set(dl.input.split(/\s+/).map((x) => x.trim()).filter(Boolean))];
  if (!links.length) return;
  dl.probing += 1;
  dlRenderInput();
  const failed = [];
  for (const link of links) {
    try {
      const result = await api("/api/download/probe", "POST", { url: link });
      const key = `d${++dl.seq}`;
      const selected = new Set(result.kind === "playlist" ? result.entries.map((_, i) => i) : []);
      dl.found.push({ key, result, selected });
    } catch (error) {
      failed.push(`${link}: ${error.message}`);
    }
  }
  dl.probing -= 1;
  if (failed.length) {
    say(failed[0], true);
    dl.input = failed.map((f) => f.split(": ")[0]).join("\n");
  } else {
    dl.input = "";
  }
  dlRenderInput();
}

async function dlStart() {
  const entries = dlChosen();
  if (!entries.length) return;
  const button = $('[data-dl="start"]');
  if (button) button.disabled = true;
  if ([...dl.items.values()].every((i) => DL_TERMINAL.has(i.state))) dl.batchStarted = Date.now() / 1000 - 1;
  try {
    const folder = dl.outputMode === "folder" ? dl.folder.trim() : null;
    const { items } = await api("/api/download", "POST", { entries, options: dlOptionsPayload(), folder });
    dl.found = [];
    say(t("download.toast.started", { n: items.length }));
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
    if (!text.trim()) return;
    dl.input = dl.input.trim() ? `${dl.input.trim()}\n${text.trim()}` : text.trim();
    dlRenderInput();
    dlProbe();
  } catch {
    say(t("download.pasteFailed"), true);
    $("#dl-url")?.focus();
  }
}

async function dlBrowse() {
  try {
    const { path } = await api("/api/pick-folder", "POST", { initial: dl.folder || null });
    if (path) { dl.folder = path; dlRemember(); dlRenderOptions(); }
  } catch (error) {
    sayError(error);
  }
}

$("#downloader").addEventListener("click", (event) => {
  const el = event.target.closest("[data-dl],[data-dl-mode],[data-dl-container],[data-dl-audio],[data-dl-remove],[data-dl-all],[data-dl-cancel],[data-dl-retry],[data-dl-reveal]");
  if (!el || el.disabled) return;
  const d = el.dataset;
  if (d.dlMode) { dl.options.mode = d.dlMode; dlRemember(); dlRenderOptions(); dlRenderInput(); return; }
  if (d.dlContainer) { dl.options.container = d.dlContainer; dlRemember(); dlRenderOptions(); return; }
  if (d.dlAudio) { dl.options.audio = d.dlAudio; dlRemember(); dlRenderOptions(); return; }
  if (d.dlRemove) { dl.found = dl.found.filter((f) => f.key !== d.dlRemove); dlRenderInput(); return; }
  if (d.dlAll) {
    const found = dl.found.find((f) => f.key === d.dlAll);
    if (found) found.selected = found.selected.size === found.result.entries.length ? new Set() : new Set(found.result.entries.map((_, i) => i));
    dlRenderInput();
    return;
  }
  if (d.dlCancel) { api(`/api/download/${enc(d.dlCancel)}/cancel`, "POST").catch(sayError); return; }
  if (d.dlRetry) { api(`/api/download/${enc(d.dlRetry)}/retry`, "POST").catch(sayError); return; }
  if (d.dlReveal) { api(`/api/download/${enc(d.dlReveal)}/reveal`, "POST").catch(sayError); return; }
  switch (d.dl) {
    case "probe": dlProbe(); break;
    case "paste": dlPaste(); break;
    case "clear-found": dl.found = []; dlRenderInput(); break;
    case "start": dlStart(); break;
    case "browse": dlBrowse(); break;
    case "clear": api("/api/download/clear", "POST").catch(sayError); break;
    case "cancel-all": api("/api/download/cancel-all", "POST").catch(sayError); break;
    case "open-folder": api("/api/download/open-folder", "POST", { initial: dl.outputMode === "folder" ? dl.folder.trim() : null }).catch(sayError); break;
    default: break;
  }
});

$("#downloader").addEventListener("input", (event) => {
  const el = event.target;
  if (el.id === "dl-url") {
    dl.input = el.value;
    const button = $('[data-dl="probe"]');
    if (button) button.disabled = dl.probing > 0 || !dl.input.trim();
    return;
  }
  if (el.dataset.dlFolder !== undefined) { dl.folder = el.value; dlRemember(); dlRenderStart(); return; }
  if (el.dataset.dlOption === "languages") { dl.options.languages = el.value; dlRemember(); dlRenderStart(); }
});

$("#downloader").addEventListener("change", (event) => {
  const el = event.target;
  if (el.name === "dl-output") { dl.outputMode = el.value; dlRemember(); dlRenderOptions(); return; }
  if (el.dataset.dlPick) {
    const [key, index] = el.dataset.dlPick.split(":");
    const found = dl.found.find((f) => f.key === key);
    if (found) {
      if (el.checked) found.selected.add(Number(index)); else found.selected.delete(Number(index));
      const head = $(`[data-dl-found="${CSS.escape(key)}"] .dl-card-head`);
      if (head) dlRenderInput(); else dlRenderStart();
    }
    return;
  }
  const key = el.dataset.dlOption;
  if (!key || key === "languages") return;
  dl.options[key] = el.type === "checkbox" ? el.checked : key === "height" ? (el.value ? Number(el.value) : null) : el.value;
  dlRemember();
  if (key === "subtitles") dlRenderOptions(); else dlRenderStart();
});

$("#downloader").addEventListener("paste", (event) => {
  if (event.target.id !== "dl-url") return;
  setTimeout(() => {
    dl.input = event.target.value;
    if (dl.input.trim() && !dl.probing) dlProbe();
  }, 0);
});

$("#downloader").addEventListener("keydown", (event) => {
  if (event.target.id === "dl-url" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (dl.input.trim() && !dl.probing) dlProbe();
  }
});

document.addEventListener("error", (event) => {
  const img = event.target;
  if (img instanceof HTMLImageElement && img.dataset.fallback === "thumb") img.remove();
}, true);

dlRestore();

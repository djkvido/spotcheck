"use strict";

const CONVERT_KEY = "spotcheck-convert";
const CV_TERMINAL = new Set(["done", "failed", "cancelled"]);
const CV_STATE_CLASS = { queued: "s-missing", running: "s-run", done: "s-ok", failed: "s-bad", cancelled: "s-missing" };
const HW_NAMES = { nvenc: "NVIDIA NVENC", qsv: "Intel Quick Sync", amf: "AMD AMF", videotoolbox: "Apple VideoToolbox" };
ICONS.film = '<rect x="2" y="3" width="20" height="18" rx="2"/><line x1="7" y1="3" x2="7" y2="21"/><line x1="17" y1="3" x2="17" y2="21"/><line x1="2" y1="9" x2="7" y2="9"/><line x1="2" y1="15" x2="7" y2="15"/><line x1="17" y1="9" x2="22" y2="9"/><line x1="17" y1="15" x2="22" y2="15"/>';
ICONS.convert = '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>';
ICONS.chip = '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>';

const cv = {
  loaded: false,
  data: null,
  formats: null,
  formatsLoading: false,
  files: [],
  items: new Map(),
  mode: "audio",
  preset: { audio: "mp3", video: "mp4_h264" },
  quality: {},
  options: { resolution: "", fps: "", sample_rate: "", channels: "", normalize: false, mute: false, hardware: true, start: "", end: "" },
  custom: { container: "matroska", video: "libx264", audio: "aac", quality: "balanced" },
  outputMode: "source",
  folder: "",
  more: false,
  adjustOpen: false,
  batchStarted: 0,
  uploads: Promise.resolve(),
  seq: 0,
  drag: 0,
};

function cvRestore() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONVERT_KEY) || "{}");
    if (["audio", "video", "custom"].includes(saved.mode)) cv.mode = saved.mode;
    if (saved.preset && typeof saved.preset === "object") Object.assign(cv.preset, saved.preset);
    if (saved.quality && typeof saved.quality === "object") cv.quality = saved.quality;
    if (saved.options && typeof saved.options === "object") Object.assign(cv.options, saved.options, { start: "", end: "" });
    if (saved.custom && typeof saved.custom === "object") Object.assign(cv.custom, saved.custom);
    if (["source", "folder"].includes(saved.outputMode)) cv.outputMode = saved.outputMode;
    if (typeof saved.folder === "string") cv.folder = saved.folder;
  } catch {}
}

function cvRemember() {
  const { mode, preset, quality, options, custom, outputMode, folder } = cv;
  try { localStorage.setItem(CONVERT_KEY, JSON.stringify({ mode, preset, quality, options, custom, outputMode, folder })); } catch {}
}

function fmtBytes(bytes) {
  if (bytes == null) return "–";
  const units = ["B", "kB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) { value /= 1000; unit += 1; }
  return `${fmt(value, unit ? 1 : 0)} ${units[unit]}`;
}

function fmtDuration(seconds) {
  if (seconds == null) return "–";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  return h ? `${h}:${String(Math.floor((total % 3600) / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}` : fmtTime(total);
}

function parseClock(text) {
  const raw = String(text || "").trim().replace(",", ".");
  if (!raw) return null;
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(raw)) return NaN;
  return raw.split(":").reduce((sum, part) => sum * 60 + Number(part), 0);
}

const cvPresets = () => (cv.data ? cv.data.presets : []);
const cvPreset = (id) => cvPresets().find((p) => p.id === id) || null;
const cvCurrentPreset = () => (cv.mode === "custom" ? null : cvPreset(cv.preset[cv.mode]));
const presetName = (p) => p.name || t(`convert.name.${p.id}`);
const hwName = (encoder) => HW_NAMES[String(encoder || "").split("_").pop()] || encoder;

function cvKind() {
  if (cv.mode === "custom") return cv.custom.video && cv.custom.video !== "none" ? "video" : "audio";
  return cvCurrentPreset()?.kind || cv.mode;
}

const cvReady = (entry) => Boolean(entry.info && !entry.error && entry.uploading == null && entry.path);
const cvEligible = (entry) => cvReady(entry) && Boolean(cvKind() === "video" ? entry.info.video : entry.info.audio);

function cvTrim() {
  const start = parseClock(cv.options.start);
  const end = parseClock(cv.options.end);
  let error = "";
  if (Number.isNaN(start) || Number.isNaN(end)) error = "convert.trimFormat";
  else if (start != null && end != null && end <= start) error = "convert.trimOrder";
  return { start, end, error };
}

function cvTarget() {
  const o = cv.options;
  const trim = cvTrim();
  const number = (value) => (value === "" || value == null ? null : Number(value));
  const target = {
    resolution: number(o.resolution),
    fps: number(o.fps),
    sample_rate: number(o.sample_rate),
    channels: number(o.channels),
    normalize: Boolean(o.normalize),
    mute: Boolean(o.mute),
    hardware: Boolean(o.hardware),
    start: trim.start || null,
    end: trim.end,
  };
  if (cv.mode === "custom") return { ...target, container: cv.custom.container, video: cv.custom.video, audio: cv.custom.audio, quality: cv.custom.quality };
  const preset = cvCurrentPreset();
  const quality = preset ? (cv.quality[preset.id] || preset.qualities[0].id) : null;
  return { ...target, preset: preset ? preset.id : null, quality };
}

async function cvLoad() {
  try {
    cv.data = await api("/api/convert");
  } catch (error) {
    cv.data = null;
    $("#converter").innerHTML = `<section class="panel welcome"><div class="dropzone"><h2>${te("convert.loadFailed")}</h2><p>${escapeHtml(error.message)}</p></div></section>`;
    return;
  }
  cv.loaded = true;
  cv.items = new Map(cv.data.items.map((item) => [item.id, item]));
  if (!cv.folder) cv.folder = cv.data.default_folder;
  for (const kind of ["audio", "video"]) {
    const chosen = cvPreset(cv.preset[kind]);
    if (!chosen || !chosen.available) cv.preset[kind] = (cvPresets().find((p) => p.kind === kind && p.available) || {}).id || cv.preset[kind];
  }
  if (cv.mode === "custom") cvLoadFormats();
  cvRender();
}

async function cvLoadFormats() {
  if (cv.formats || cv.formatsLoading) return;
  cv.formatsLoading = true;
  try {
    cv.formats = await api("/api/convert/formats");
  } catch (error) {
    sayError(error);
  }
  cv.formatsLoading = false;
  cvFitCustom();
  if (cv.mode === "custom") cvRenderTarget();
}

function cvFitCustom() {
  if (!cv.formats) return;
  const containers = new Set(cv.formats.containers.map((c) => c.id));
  const encoders = (kind) => new Set(["none", "copy", ...cv.formats.encoders.filter((e) => e.kind === kind).map((e) => e.id)]);
  if (!containers.has(cv.custom.container)) cv.custom.container = containers.has("matroska") ? "matroska" : [...containers][0] || "";
  for (const [kind, fallback] of [["video", "libx264"], ["audio", "aac"]]) {
    const known = encoders(kind);
    if (!known.has(cv.custom[kind])) cv.custom[kind] = known.has(fallback) ? fallback : "none";
  }
}

function cvRender() {
  if (!cv.loaded) {
    $("#converter").innerHTML = `<section class="panel welcome"><div class="dropzone"><p>${te("convert.loading")}</p></div></section>`;
    return;
  }
  $("#converter").innerHTML = `
    <div class="cv-main">
      <div id="cv-codecs"></div>
      <section class="panel cv-files" id="cv-files" aria-labelledby="cv-files-title"></section>
      <section class="panel cv-queue" id="cv-queue" aria-labelledby="cv-queue-title"></section>
    </div>
    <section class="panel cv-target" id="cv-target" aria-labelledby="cv-target-title"></section>`;
  cvRenderCodecs();
  cvRenderFiles();
  cvRenderTarget();
  cvRenderQueue();
}

function cvRenderCodecs() {
  const box = $("#cv-codecs");
  if (!box || !cv.data) return;
  const c = cv.data.codecs;
  const job = c.job || {};
  const missingVideo = !cvPresets().some((p) => p.id === "mp4_h264" && p.available) || !cv.data.tool.ffprobe;
  if (!c.available || (c.installed && job.state !== "running") || (!missingVideo && job.state !== "running")) {
    box.innerHTML = "";
    return;
  }
  let body;
  if (job.state === "running") {
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
    body = `<div class="cv-codecs-text"><b>${te("convert.codecs.downloading")}</b>
        <div class="row"><div class="progress"><i style="width:${pct}%"></i></div><span class="num dim">${pct} % · ${escapeHtml(fmtBytes(job.done))} / ${escapeHtml(fmtBytes(job.total || c.size))}</span></div></div>
      <button class="btn sm" type="button" data-cv="codecs-cancel">${te("app.cancel")}</button>`;
  } else {
    const failed = job.state === "failed" ? `<p class="bad-text">${escapeHtml(errorText(job.error || "codecs_failed"))}</p>` : "";
    body = `<div class="cv-codecs-text"><b>${te("convert.codecs.title")}</b>
        <p>${te("convert.codecs.text", { version: c.version, provider: c.provider, size: fmtBytes(c.size), license: c.license })}</p>${failed}</div>
      <button class="btn primary" type="button" data-cv="codecs-install">${icon("download", "sm")}${te(job.state === "failed" ? "convert.codecs.retry" : "convert.codecs.download")}</button>`;
  }
  box.innerHTML = `<section class="panel cv-codecs">${body}</section>`;
}

function cvFileMeta(entry) {
  if (entry.uploading != null) return te("convert.uploading", { pct: Math.round(entry.uploading * 100) });
  if (entry.error) return `<span class="bad-text">${escapeHtml(errorText(entry.error))}</span>`;
  const info = entry.info || {};
  const parts = [];
  if (info.video) parts.push(`${escapeHtml(String(info.video.codec || "").toUpperCase())} ${info.video.width || "?"}×${info.video.height || "?"}${info.video.fps ? ` · ${fmt(info.video.fps, 2)} fps` : ""}`);
  if (info.audio) parts.push(`${escapeHtml(String(info.audio.codec || "").toUpperCase())}${info.audio.sample_rate ? ` ${fmt(info.audio.sample_rate / 1000, 1)} kHz` : ""}${info.audio.channels ? ` · ${te(info.audio.channels === 1 ? "convert.mono" : info.audio.channels === 2 ? "convert.stereo" : "convert.channels", { n: info.audio.channels })}` : ""}`);
  parts.push(escapeHtml(fmtDuration(info.duration)), escapeHtml(fmtBytes(entry.size)));
  return parts.join(" · ");
}

function cvFileRow(entry) {
  const kind = entry.info?.video ? "film" : "music";
  const skipped = cvReady(entry) && !cvEligible(entry);
  const note = skipped ? `<span class="pill s-review">${te(cvKind() === "video" ? "convert.skipNoVideo" : "convert.skipNoAudio")}</span>` : "";
  const progress = entry.uploading != null ? `<div class="progress"><i style="width:${Math.round(entry.uploading * 100)}%"></i></div>` : "";
  return `<li class="cv-file ${skipped ? "skipped" : ""} ${entry.error ? "failed" : ""}" data-cv-file="${escapeHtml(entry.key)}">
      <span class="cv-kind">${icon(entry.error ? "file" : kind, "sm")}</span>
      <span class="cv-file-text"><span class="name" title="${escapeHtml(entry.path || entry.name)}">${escapeHtml(entry.name)}</span>
        <span class="meta">${cvFileMeta(entry)}</span>${progress}</span>
      ${note}
      <button class="icon-btn" type="button" data-cv-remove="${escapeHtml(entry.key)}" title="${te("convert.removeFile")}" aria-label="${te("convert.removeFile")}" ${entry.uploading != null ? "disabled" : ""}>${icon("close", "sm")}</button>
    </li>`;
}

function cvRenderFiles() {
  const box = $("#cv-files");
  if (!box) return;
  const list = cv.files.length
    ? `<ul class="cv-file-list">${cv.files.map(cvFileRow).join("")}</ul>`
    : `<button class="cv-drop" type="button" data-cv="add"><span class="cv-drop-icon">${icon("convert")}</span>
        <h2>${te("convert.dropTitle")}</h2><p>${te("convert.dropText")}</p><p class="cta">${te("convert.dropCta")}</p></button>`;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="cv-files-title">${te("convert.files")}</h2>
      ${cv.files.length ? `<span class="dim num">${te("convert.fileCount", { n: cv.files.length })}</span>
      <button class="btn sm soft" type="button" data-cv="clear-files">${te("convert.clearFiles")}</button>` : ""}
      <button class="btn sm" type="button" data-cv="add">${icon("plus", "sm")}${te("convert.addFiles")}</button></div>
    ${list}
    <input type="file" id="cv-input" multiple hidden>
    <div class="cv-drop-hint">${icon("download", "sm")}${te("convert.dropHere")}</div>`;
  box.classList.toggle("dragging", cv.drag > 0);
  cvRenderStart();
}

function presetButton(p) {
  const on = cv.preset[p.kind] === p.id;
  const disabled = !p.available;
  const hw = p.hardware ? `<i class="cv-hw" title="${te("convert.hwAvailable")}">GPU</i>` : "";
  return `<button type="button" class="cv-preset ${on ? "on" : ""}" data-cv-preset="${p.id}" aria-pressed="${on}" ${disabled ? `disabled title="${te("convert.needsCodecs")}"` : ""}>
      <b>${escapeHtml(presetName(p))}${hw}</b><span>${te(`convert.preset.${p.id}`)}</span></button>`;
}

function qualityHtml(preset) {
  if (!preset || preset.qualities.length < 2) return preset && preset.qualities[0].detail ? `<p class="hint">${escapeHtml(preset.qualities[0].detail)}</p>` : "";
  const chosen = cv.quality[preset.id] || preset.qualities[0].id;
  const buttons = preset.qualities.map((q) => `<button type="button" class="seg-btn ${q.id === chosen ? "on" : ""}" data-cv-quality="${q.id}" aria-pressed="${q.id === chosen}">
      ${te(`convert.q.${q.id}`)}${q.detail ? `<small>${escapeHtml(q.detail)}</small>` : ""}</button>`).join("");
  return `<div class="cv-field"><span class="f-label">${te("convert.quality")}</span><div class="seg-switch cv-quality" role="group" aria-label="${te("convert.quality")}">${buttons}</div></div>`;
}

function selectHtml(id, label, value, options, disabled = false) {
  const opts = options.map(([v, text]) => `<option value="${escapeHtml(v)}" ${String(v) === String(value) ? "selected" : ""}>${escapeHtml(text)}</option>`).join("");
  return `<label class="cv-field"><span class="f-label">${te(label)}</span><select data-cv-option="${id}" ${disabled ? "disabled" : ""}>${opts}</select></label>`;
}

function checkHtml(id, label, hint = "", disabled = false) {
  return `<label class="check-row"><input type="checkbox" data-cv-option="${id}" ${cv.options[id] ? "checked" : ""} ${disabled ? "disabled" : ""}><span>${te(label)}${hint ? `<span class="hint">${hint}</span>` : ""}</span></label>`;
}

function adjustHtml(preset) {
  const kind = cvKind();
  const copy = Boolean(preset && preset.copy);
  const gif = preset && preset.id === "gif";
  const o = cv.options;
  const opts = cv.data.options;
  const original = [["", t("convert.original")]];
  let html = "";
  if (kind === "video" && !copy) {
    html += `<div class="cv-grid">${selectHtml("resolution", "convert.resolution", o.resolution, original.concat(opts.resolutions.map((r) => [r, `${r}p`])), gif)}
      ${selectHtml("fps", "convert.fps", o.fps, original.concat(opts.fps.map((f) => [f, `${f} fps`])), gif)}</div>`;
    if (preset && preset.hardware) {
      html += checkHtml("hardware", "convert.hardware", te("convert.hardwareHint", { name: hwName(cv.data.hardware[preset.family]) }));
    }
    if (!gif) html += checkHtml("mute", "convert.mute");
  }
  if (!copy && !gif && !(kind === "video" && o.mute)) {
    const rates = preset ? preset.rates : opts.sample_rates;
    if (o.sample_rate !== "" && !rates.includes(Number(o.sample_rate))) o.sample_rate = "";
    html += `<div class="cv-grid">${selectHtml("sample_rate", "convert.sampleRate", o.sample_rate, original.concat(rates.map((r) => [r, `${fmt(r / 1000, 1)} kHz`])))}
      ${selectHtml("channels", "convert.channelsLabel", o.channels, original.concat([[2, t("convert.stereo")], [1, t("convert.mono")]]))}</div>`;
    html += checkHtml("normalize", "convert.normalize", te("convert.normalizeHint"));
  }
  const trim = cvTrim();
  html += `<div class="cv-grid">
      <label class="cv-field"><span class="f-label">${te("convert.trimStart")}</span><input type="text" inputmode="decimal" data-cv-option="start" value="${escapeHtml(o.start)}" placeholder="0:00" autocomplete="off"></label>
      <label class="cv-field"><span class="f-label">${te("convert.trimEnd")}</span><input type="text" inputmode="decimal" data-cv-option="end" value="${escapeHtml(o.end)}" placeholder="${te("convert.endPlaceholder")}" autocomplete="off"></label></div>
    <p class="hint" id="cv-trim-hint">${trim.error ? `<span class="bad-text">${te(trim.error)}</span>` : te(copy ? "convert.trimCopyHint" : "convert.trimHint")}</p>`;
  const open = cv.adjustOpen ? "open" : "";
  return `<details class="cv-adjust" ${open}><summary>${te("convert.adjust")}</summary><div class="cv-adjust-body">${html}</div></details>`;
}

function customHtml() {
  if (!cv.formats) return `<p class="dim">${te(cv.formatsLoading ? "convert.formatsLoading" : "convert.loading")}</p>`;
  const c = cv.custom;
  const containers = cv.formats.containers.map((f) => [f.id, `.${f.ext} · ${f.name}`]);
  const encoders = (kind) => cv.formats.encoders.filter((e) => e.kind === kind).map((e) => [e.id, `${e.id} · ${e.description}${e.experimental ? ` (${t("convert.experimental")})` : ""}`]);
  const none = (kind) => [["none", t(kind === "video" ? "convert.noVideo" : "convert.noAudio")], ["copy", t("convert.copyStream")]];
  const select = (key, label, value, options) => `<label class="cv-field"><span class="f-label">${te(label)}</span><select data-cv-custom="${key}">${options.map(([v, text]) => `<option value="${escapeHtml(v)}" ${v === value ? "selected" : ""}>${escapeHtml(text)}</option>`).join("")}</select></label>`;
  const qualities = ["high", "balanced", "small"].map((q) => `<button type="button" class="seg-btn ${c.quality === q ? "on" : ""}" data-cv-custom-quality="${q}" aria-pressed="${c.quality === q}">${te(`convert.q.${q}`)}</button>`).join("");
  return `<p class="hint cv-intro">${te("convert.customIntro", { containers: cv.formats.containers.length, encoders: cv.formats.encoders.length })}</p>
    ${select("container", "convert.container", c.container, containers)}
    ${select("video", "convert.videoCodec", c.video, none("video").concat(encoders("video")))}
    ${select("audio", "convert.audioCodec", c.audio, none("audio").concat(encoders("audio")))}
    <div class="cv-field"><span class="f-label">${te("convert.quality")}</span><div class="seg-switch cv-quality" role="group">${qualities}</div></div>
    <p class="hint">${te("convert.customHint")}</p>`;
}

function cvRenderTarget() {
  const box = $("#cv-target");
  if (!box || !cv.data) return;
  const modes = ["audio", "video", "custom"].map((m) => `<button type="button" role="tab" class="seg-btn ${cv.mode === m ? "on" : ""}" data-cv-mode="${m}" aria-selected="${cv.mode === m}">${te(`convert.mode.${m}`)}</button>`).join("");
  let body = "";
  const preset = cvCurrentPreset();
  if (cv.mode === "custom") {
    body = customHtml();
  } else {
    const list = cvPresets().filter((p) => p.kind === cv.mode);
    const common = list.filter((p) => p.group === "common");
    const more = list.filter((p) => p.group !== "common");
    const showMore = cv.more || more.some((p) => p.id === cv.preset[cv.mode]);
    body = `<div class="cv-presets">${common.map(presetButton).join("")}</div>
      ${more.length ? `<button class="btn sm soft cv-more" type="button" data-cv="more" aria-expanded="${showMore}">${te(showMore ? "convert.lessFormats" : "convert.moreFormats", { n: more.length })}</button>
      ${showMore ? `<div class="cv-presets">${more.map(presetButton).join("")}</div>` : ""}` : ""}
      ${preset && !preset.available ? `<p class="hint bad-text">${te("convert.needsCodecs")}</p>` : ""}
      ${qualityHtml(preset)}`;
  }
  const folderInput = `<div class="cv-folder"><input type="text" data-cv-folder value="${escapeHtml(cv.folder)}" spellcheck="false" aria-label="${te("convert.folder")}" ${cv.outputMode === "folder" ? "" : "disabled"}>
      ${cv.data.picker ? `<button class="btn sm" type="button" data-cv="browse" ${cv.outputMode === "folder" ? "" : "disabled"}>${te("project.browse")}</button>` : ""}</div>`;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="cv-target-title">${te("convert.target")}</h2></div>
    <div class="seg-switch cv-modes" role="tablist" aria-label="${te("convert.target")}">${modes}</div>
    <div class="cv-target-body">${body}
      ${cv.data.tool.ffmpeg ? adjustHtml(preset) : ""}
      <div class="cv-field cv-output"><span class="f-label">${te("convert.output")}</span>
        <label class="check-row radio"><input type="radio" name="cv-output" value="source" ${cv.outputMode === "source" ? "checked" : ""}><span>${te("convert.outputSource")}<span class="hint">${te("convert.outputSourceHint")}</span></span></label>
        <label class="check-row radio"><input type="radio" name="cv-output" value="folder" ${cv.outputMode === "folder" ? "checked" : ""}><span>${te("convert.outputFolder")}</span></label>
        ${folderInput}
      </div>
    </div>
    <div class="cv-start" id="cv-start"></div>`;
  cvRenderStart();
}

function cvWarnings(files) {
  const preset = cvCurrentPreset();
  const lossless = cv.mode === "custom" ? ["flac", "alac", "wavpack", "tta"].includes(cv.custom.audio) || cv.custom.audio.startsWith("pcm_") : Boolean(preset && preset.lossless);
  const lossy = new Set(["mp3", "aac", "vorbis", "opus", "wmav2", "ac3", "eac3", "mp2"]);
  if (lossless && files.some((f) => lossy.has(f.info.audio?.codec))) return [t("convert.warn.lossy_to_lossless")];
  return [];
}

function cvRenderStart() {
  const box = $("#cv-start");
  if (!box || !cv.data) return;
  const files = cv.files.filter(cvEligible);
  const preset = cvCurrentPreset();
  const trim = cvTrim();
  let blocker = "";
  if (!cv.data.tool.ffmpeg) blocker = t("convert.noFfmpeg");
  else if (!cv.data.tool.ffprobe) blocker = t("error.ffprobe_missing");
  else if (cv.mode !== "custom" && (!preset || !preset.available)) blocker = t("convert.pickFormat");
  else if (cv.mode === "custom" && !cv.formats) blocker = t("convert.formatsLoading");
  else if (cv.mode === "custom" && cv.custom.video === "none" && cv.custom.audio === "none") blocker = t("error.nothing_to_convert");
  else if (trim.error) blocker = t(trim.error);
  else if (cv.outputMode === "folder" && !cv.folder.trim()) blocker = t("error.folder_required");
  else if (!cv.files.length) blocker = t("convert.addFirst");
  else if (!files.length) blocker = t(cv.files.some((f) => f.uploading != null) ? "convert.waitUpload" : "convert.nothingEligible");
  const warnings = files.length ? cvWarnings(files) : [];
  const skipped = cv.files.filter((f) => cvReady(f) && !cvEligible(f)).length;
  box.innerHTML = `${warnings.map((w) => `<p class="cv-warning">${escapeHtml(w)}</p>`).join("")}
    ${skipped && files.length ? `<p class="hint">${te("convert.skipped", { n: skipped })}</p>` : ""}
    <button class="run-btn" type="button" data-cv="start" ${blocker ? "disabled" : ""}>
      <span class="disc">${icon("play")}</span><span class="txt"><span>${te("convert.run", { n: files.length })}</span>
      <small>${escapeHtml(blocker || cvTargetLabel(cvTarget()))}</small></span></button>`;
}

function cvTargetLabel(target) {
  if (!target) return "";
  if (target.preset) {
    const preset = cvPreset(target.preset);
    if (!preset) return target.preset;
    const quality = preset.qualities.find((q) => q.id === target.quality) || preset.qualities[0];
    const detail = quality && quality.id !== "original" ? ` · ${t(`convert.q.${quality.id}`)}${quality.detail ? ` (${quality.detail})` : ""}` : "";
    return `${presetName(preset)}${preset.copy ? ` · ${t("convert.noReencode")}` : ""}${detail}`;
  }
  const parts = [target.video !== "none" ? target.video : "", target.audio !== "none" ? target.audio : ""].filter(Boolean).join(" + ");
  return `${target.container} · ${parts}`;
}

function cvItemHtml(item) {
  const pct = Math.round((item.progress || 0) * 100);
  const name = item.output ? item.output.split(/[\\/]/).pop() : `${item.name.replace(/\.[^.]+$/, "")}.${item.ext}`;
  let status = "";
  if (item.state === "running") {
    const eta = item.eta != null ? ` · ${te("convert.eta", { time: fmtDuration(item.eta) })}` : "";
    const speed = item.speed ? ` · ${fmt(item.speed, 1)}×` : "";
    status = `<div class="row"><div class="progress"><i style="width:${Math.max(pct, 2)}%"></i></div><span class="num dim">${pct} %${eta}${speed}</span></div>`;
  }
  const sizes = item.state === "done" ? `${escapeHtml(fmtBytes(item.size_in))} → ${escapeHtml(fmtBytes(item.size_out))}` : escapeHtml(fmtBytes(item.size_in));
  const encoder = item.encoder ? `<span class="pill s-partial" title="${te("convert.hwUsed")}">${icon("chip", "sm")}${escapeHtml(hwName(item.encoder))}</span>` : "";
  const warnings = (item.warnings || []).map((w) => `<p class="cv-warning">${te(`convert.warn.${w}`)}</p>`).join("");
  const error = item.state === "failed"
    ? `<p class="bad-text">${escapeHtml(errorText(item.error || "convert_failed"))}</p>${item.detail ? `<details class="cv-detail"><summary>${te("convert.details")}</summary><pre>${escapeHtml(item.detail)}</pre></details>` : ""}`
    : "";
  const actions = [];
  if (!CV_TERMINAL.has(item.state)) actions.push(`<button class="btn sm" type="button" data-cv-cancel="${item.id}">${te("app.cancel")}</button>`);
  if (item.state === "failed" || item.state === "cancelled") actions.push(`<button class="btn sm" type="button" data-cv-retry="${item.id}">${icon("refresh", "sm")}${te("convert.retry")}</button>`);
  if (item.state === "done") actions.push(`<button class="btn sm" type="button" data-cv-reveal="${item.id}">${icon("folder", "sm")}${te("convert.reveal")}</button>`);
  return `<li class="cv-item" data-cv-item="${item.id}">
      <div class="cv-item-head"><span class="cv-kind">${icon(item.kind === "video" ? "film" : "music", "sm")}</span>
        <span class="cv-file-text"><span class="name" title="${escapeHtml(item.source)}">${escapeHtml(item.name)}</span>
          <span class="meta">→ ${escapeHtml(name)} · ${escapeHtml(cvTargetLabel(item.target))}</span></span>
        <span class="pill ${CV_STATE_CLASS[item.state] || ""}">${te(`convert.state.${item.state}`)}</span></div>
      ${status}
      <div class="cv-item-foot"><span class="meta num">${sizes}</span>${encoder}<span class="spacer"></span>${actions.join("")}</div>
      ${warnings}${error}
    </li>`;
}

function cvRenderQueue() {
  const box = $("#cv-queue");
  if (!box) return;
  const items = [...cv.items.values()].sort((a, b) => b.created - a.created);
  const finished = items.filter((i) => CV_TERMINAL.has(i.state)).length;
  const active = items.length - finished;
  box.innerHTML = `<div class="panel-head"><h2 class="label" id="cv-queue-title">${te("convert.queue")}</h2>
      ${active ? `<span class="dim num">${te("convert.active", { n: active })}</span>` : ""}
      ${active > 1 ? `<button class="btn sm soft" type="button" data-cv="cancel-all">${te("convert.cancelAll")}</button>` : ""}
      ${finished ? `<button class="btn sm soft" type="button" data-cv="clear">${te("convert.clearDone")}</button>` : ""}</div>
    ${items.length ? `<ul class="cv-items">${items.map(cvItemHtml).join("")}</ul>` : `<p class="dim cv-empty">${te("convert.queueEmpty")}</p>`}`;
}

function cvUpdateItem(item) {
  const previous = cv.items.get(item.id);
  cv.items.set(item.id, item);
  const el = $(`[data-cv-item="${CSS.escape(item.id)}"]`);
  if (!previous || !el || previous.state !== item.state) cvRenderQueue();
  else el.outerHTML = cvItemHtml(item);
  if (previous && !CV_TERMINAL.has(previous.state) && CV_TERMINAL.has(item.state)) {
    const pending = [...cv.items.values()].some((i) => !CV_TERMINAL.has(i.state));
    if (item.state === "failed") say(t("convert.toast.failed", { name: item.name, error: errorText(item.error || "convert_failed") }), true);
    else if (!pending) {
      const done = [...cv.items.values()].filter((i) => i.state === "done" && i.finished >= (cv.batchStarted || 0)).length;
      say(t("convert.toast.allDone", { n: done }));
    }
  }
}

function cvOnEvent(event) {
  if (!cv.loaded) return;
  if (event.type === "convert") cvUpdateItem(event.item);
  else if (event.type === "convert_list") cvLoad();
  else if (event.type === "codecs") {
    const wasRunning = cv.data.codecs.job?.state === "running";
    cv.data.codecs = { ...event.status, job: event.job };
    if (wasRunning && event.job.state !== "running") {
      cv.formats = null;
      if (event.job.state === "done") say(t("convert.codecs.done"));
      else if (event.job.state === "failed") say(errorText(event.job.error || "codecs_failed"), true);
      cvLoad();
    } else cvRenderCodecs();
  }
}

function cvAddEntries(entries) {
  const known = new Set(cv.files.map((f) => f.path));
  for (const entry of entries) {
    if (entry.path && known.has(entry.path)) continue;
    cv.files.push({ ...entry, key: `f${++cv.seq}`, uploading: null });
    known.add(entry.path);
  }
  cvRenderFiles();
}

async function cvPick() {
  if (!cv.data.picker) { $("#cv-input").click(); return; }
  try {
    const { paths } = await api("/api/convert/pick", "POST", { initial: null });
    if (!paths.length) return;
    say(t("convert.reading", { n: paths.length }));
    const { files } = await api("/api/convert/probe", "POST", { paths });
    cvAddEntries(files);
  } catch (error) {
    if (error.code === "folder_picker_unavailable") $("#cv-input").click();
    else sayError(error);
  }
}

function cvUpload(file) {
  const entry = { key: `f${++cv.seq}`, name: file.name, size: file.size, uploading: 0, info: null, error: null, path: null };
  cv.files.push(entry);
  cvRenderFiles();
  cv.uploads = cv.uploads.then(() => new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/convert/upload?name=${enc(file.name)}`);
    xhr.setRequestHeader("X-SpotCheck", "1");
    let last = 0;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable || performance.now() - last < 200) return;
      last = performance.now();
      entry.uploading = e.loaded / e.total;
      const row = $(`[data-cv-file="${CSS.escape(entry.key)}"]`);
      if (row) row.outerHTML = cvFileRow(entry);
    };
    const finish = (error, data) => {
      if (!cv.files.includes(entry)) { resolve(); return; }
      entry.uploading = null;
      if (error) entry.error = error;
      else Object.assign(entry, data, { key: entry.key, uploaded: true });
      cvRenderFiles();
      resolve();
    };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300 && data) finish(null, data);
      else finish((data && typeof data.detail === "string" && data.detail) || "unexpected");
    };
    xhr.onerror = () => finish("server_unreachable");
    xhr.send(file);
  }));
}

async function cvStart() {
  const files = cv.files.filter(cvEligible);
  if (!files.length) return;
  const folder = cv.outputMode === "folder" ? cv.folder.trim() : null;
  const button = $('[data-cv="start"]');
  if (button) button.disabled = true;
  if ([...cv.items.values()].every((i) => CV_TERMINAL.has(i.state))) cv.batchStarted = Date.now() / 1000 - 1;
  try {
    const { items } = await api("/api/convert", "POST", { files: files.map((f) => f.path), target: cvTarget(), folder });
    const sent = new Set(files);
    cv.files = cv.files.filter((f) => !sent.has(f));
    say(t("convert.toast.started", { n: items.length }));
    cvRemember();
    cvRenderFiles();
  } catch (error) {
    sayError(error);
    cvRenderStart();
  }
}

async function cvBrowse() {
  try {
    const { path } = await api("/api/pick-folder", "POST", { initial: cv.folder || null });
    if (path) { cv.folder = path; cvRemember(); cvRenderTarget(); }
  } catch (error) {
    sayError(error);
  }
}

async function cvInstallCodecs() {
  try {
    await api("/api/codecs/install", "POST");
  } catch (error) {
    sayError(error);
  }
}

async function cvRemoveCodecs() {
  if (!await confirmDialog(t("convert.codecs.removeTitle"), para(te("convert.codecs.removeText")), t("convert.codecs.remove"), true)) return;
  try {
    await api("/api/codecs", "DELETE");
    say(t("convert.codecs.removed"));
    if (cv.loaded) { cv.formats = null; await cvLoad(); }
  } catch (error) {
    sayError(error);
  }
}

function cvDiscard(entries) {
  const gone = new Set(entries);
  cv.files = cv.files.filter((f) => !gone.has(f));
  cvRenderFiles();
  const uploaded = entries.filter((f) => f.uploaded && f.path).map((f) => f.path);
  if (uploaded.length) api("/api/convert/discard", "POST", { paths: uploaded }).catch(() => {});
}

function setAppTab(tab) {
  state.app = tab === "convert" ? "convert" : "library";
  document.body.dataset.app = state.app;
  $$("[data-app-tab]").forEach((b) => {
    const on = b.dataset.appTab === state.app;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
  });
  $("#converter").hidden = state.app !== "convert";
  $(".shell").hidden = state.app === "convert";
  if (state.app === "convert") {
    if (!cv.loaded) { cvRender(); cvLoad(); }
    if (state.openKey) closeDrawer();
  }
  rememberView();
}

$(".app-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-app-tab]");
  if (tab) setAppTab(tab.dataset.appTab);
});
$(".app-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  const next = state.app === "convert" ? "library" : "convert";
  setAppTab(next);
  $(`[data-app-tab="${next}"]`).focus();
});

$("#converter").addEventListener("click", (event) => {
  const el = event.target.closest("[data-cv],[data-cv-preset],[data-cv-quality],[data-cv-mode],[data-cv-remove],[data-cv-cancel],[data-cv-retry],[data-cv-reveal],[data-cv-custom-quality]");
  if (!el || el.disabled) return;
  const d = el.dataset;
  if (d.cvMode) {
    cv.mode = d.cvMode;
    if (cv.mode === "custom") cvLoadFormats();
    cvRemember();
    cvRenderTarget();
    cvRenderFiles();
    return;
  }
  if (d.cvPreset) { cv.preset[cvPreset(d.cvPreset).kind] = d.cvPreset; cvRemember(); cvRenderTarget(); cvRenderFiles(); return; }
  if (d.cvQuality) { cv.quality[cvCurrentPreset().id] = d.cvQuality; cvRemember(); cvRenderTarget(); return; }
  if (d.cvCustomQuality) { cv.custom.quality = d.cvCustomQuality; cvRemember(); cvRenderTarget(); return; }
  if (d.cvRemove) { cvDiscard(cv.files.filter((f) => f.key === d.cvRemove)); return; }
  if (d.cvCancel) { api(`/api/convert/${enc(d.cvCancel)}/cancel`, "POST").catch(sayError); return; }
  if (d.cvRetry) { api(`/api/convert/${enc(d.cvRetry)}/retry`, "POST").catch(sayError); return; }
  if (d.cvReveal) { api(`/api/convert/${enc(d.cvReveal)}/reveal`, "POST").catch(sayError); return; }
  switch (d.cv) {
    case "add": cvPick(); break;
    case "clear-files": cvDiscard(cv.files.filter((f) => f.uploading == null)); break;
    case "more": cv.more = !cv.more; cvRenderTarget(); break;
    case "start": cvStart(); break;
    case "browse": cvBrowse(); break;
    case "clear": api("/api/convert/clear", "POST").catch(sayError); break;
    case "cancel-all": [...cv.items.values()].filter((i) => !CV_TERMINAL.has(i.state)).forEach((i) => api(`/api/convert/${enc(i.id)}/cancel`, "POST").catch(() => {})); break;
    case "codecs-install": cvInstallCodecs(); break;
    case "codecs-cancel": api("/api/codecs/cancel", "POST").catch(sayError); break;
    default: break;
  }
});

$("#converter").addEventListener("change", (event) => {
  const el = event.target;
  if (el.id === "cv-input") {
    [...el.files].forEach(cvUpload);
    el.value = "";
    return;
  }
  if (el.name === "cv-output") { cv.outputMode = el.value; cvRemember(); cvRenderTarget(); return; }
  if (el.dataset.cvCustom) { cv.custom[el.dataset.cvCustom] = el.value; cvRemember(); cvRenderFiles(); return; }
  if (el.dataset.cvOption && el.type !== "text") {
    cv.options[el.dataset.cvOption] = el.type === "checkbox" ? el.checked : el.value;
    cvRemember();
    if (el.dataset.cvOption === "mute") cvRenderTarget(); else cvRenderStart();
  }
});

$("#converter").addEventListener("input", (event) => {
  const el = event.target;
  if (el.dataset.cvFolder !== undefined) { cv.folder = el.value; cvRemember(); cvRenderStart(); return; }
  if (el.dataset.cvOption === "start" || el.dataset.cvOption === "end") {
    cv.options[el.dataset.cvOption] = el.value;
    const trim = cvTrim();
    const copy = Boolean(cvCurrentPreset()?.copy);
    $("#cv-trim-hint").innerHTML = trim.error ? `<span class="bad-text">${te(trim.error)}</span>` : te(copy ? "convert.trimCopyHint" : "convert.trimHint");
    cvRenderStart();
  }
});

$("#converter").addEventListener("toggle", (event) => {
  if (event.target.classList?.contains("cv-adjust")) cv.adjustOpen = event.target.open;
}, true);

$("#converter").addEventListener("dragenter", (event) => {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  cv.drag += 1;
  $("#cv-files")?.classList.add("dragging");
});
$("#converter").addEventListener("dragover", (event) => {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
});
$("#converter").addEventListener("dragleave", () => {
  cv.drag = Math.max(0, cv.drag - 1);
  if (!cv.drag) $("#cv-files")?.classList.remove("dragging");
});
$("#converter").addEventListener("drop", (event) => {
  event.preventDefault();
  cv.drag = 0;
  $("#cv-files")?.classList.remove("dragging");
  if (!cv.loaded) return;
  [...(event.dataTransfer?.files || [])].forEach(cvUpload);
});
window.addEventListener("dragover", (event) => { if (state.app === "convert") event.preventDefault(); });
window.addEventListener("drop", (event) => { if (state.app === "convert") event.preventDefault(); });

cvRestore();
setAppTab(state.app);

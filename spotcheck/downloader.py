from __future__ import annotations

import ipaddress
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import analysis, convert, deps, fileops, origins, sources, ytdlp
from .config import paths
from .errors import JobError

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 120
STALL_TIMEOUT = 600
POST_TIMEOUT = 4 * 3600
MAX_HEIGHT = 4320
EMIT_INTERVAL = 0.4
HISTORY = 500
WORKERS = 3
PLAYLIST_LIMIT = 1000
PROBE_CACHE = 50
NAME_LIMIT = 150
THUMB_LIMIT = 5_000_000
CONTAINERS = ("mp4", "mkv", "webm")
HEIGHTS = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)
AUDIO_FORMATS = {"mp3": "mp3", "m4a": "m4a", "opus": "opus", "ogg": "vorbis", "flac": "flac", "wav": "wav"}
AUDIO_EXT = {"mp3": "mp3", "m4a": "m4a", "opus": "opus", "ogg": "ogg", "flac": "flac", "wav": "wav"}
BITRATES = ("320", "256", "192", "128")
LOSSY_BITRATE = ("mp3", "m4a")
AUDIO_ENCODERS = {
    "mp3": ("libmp3lame",),
    "m4a": ("aac",),
    "opus": ("libopus",),
    "ogg": ("libvorbis",),
    "flac": ("flac",),
    "wav": ("pcm_s16le",),
}
THUMBNAIL_AUDIO = ("mp3", "m4a", "flac", "opus")
SUBTITLE_MODES = ("none", "embed", "files")
SUBTITLE_EXT = (".srt", ".vtt", ".ass", ".lrc", ".ttml", ".srv3", ".json3")
MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".mov", ".m4v"}
PROGRESS = re.compile(r"^SPOTCHECK (\S+) (\S+) (\S+) (\S+) (\S+) (\S+)$")
FORMATS_LINE = re.compile(r"Downloading \d+ format\(s\): (\S+)")
PHASES = {
    "[Merger]": "merging",
    "[ExtractAudio]": "converting",
    "[VideoRemuxer]": "converting",
    "[VideoConvertor]": "converting",
    "[EmbedSubtitle]": "finishing",
    "[EmbedThumbnail]": "finishing",
    "[Metadata]": "finishing",
    "[FFmpegMetadata]": "finishing",
    "[SubtitlesConvertor]": "finishing",
}
LANG = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
RATE_LIMITS = ("500K", "1M", "2M", "5M", "10M", "20M")
FAILURES = (
    (
        "download_unavailable",
        re.compile(
            r"video unavailable|this video is unavailable|has been removed|no longer available|has been terminated",
            re.I,
        ),
    ),
    ("download_page_missing", re.compile(r"HTTP Error 404|404: Not Found", re.I)),
    (
        "download_geo",
        re.compile(
            r"available in your country|geo.?restrict|blocked it in your country|not available from your location", re.I
        ),
    ),
    (
        "download_live",
        re.compile(r"live event will begin|premieres in|this live event|is a live stream|is currently live", re.I),
    ),
    ("download_format", re.compile(r"requested format is not available", re.I)),
    ("download_unsupported", re.compile(r"unsupported url", re.I)),
)
COOKIE_ADVICE = re.compile(r"\.?\s*(?:Use|Also see|See)\s[^\n]*cookies[^\n]*", re.I)
UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Options:
    mode: str = "video"
    container: str = "mp4"
    height: int | None = None
    hdr: bool = False
    audio: str = "mp3"
    bitrate: str = "320"
    subtitles: str = "none"
    languages: list[str] = field(default_factory=list)
    auto_subtitles: bool = False
    thumbnail: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Options:
        if not isinstance(raw, dict):
            raise JobError("invalid_option")
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw and raw[k] is not None}
        for key in ("mode", "container", "audio", "bitrate", "subtitles"):
            if key in known and not isinstance(known[key], str):
                raise JobError("invalid_option")
        for key in ("hdr", "auto_subtitles", "thumbnail"):
            if key in known and not isinstance(known[key], bool):
                raise JobError("invalid_option")
        if "height" in known and (isinstance(known["height"], bool) or not isinstance(known["height"], int)):
            raise JobError("invalid_option")
        if "languages" in known and (
            not isinstance(known["languages"], list) or not all(isinstance(x, str) for x in known["languages"])
        ):
            raise JobError("invalid_option")
        options = cls(**known)
        options.validate()
        return options

    def validate(self) -> None:
        if self.mode not in ("video", "audio"):
            raise JobError("invalid_option")
        if self.container not in CONTAINERS or self.audio not in AUDIO_FORMATS or self.bitrate not in BITRATES:
            raise JobError("invalid_option")
        if self.height is not None and not 1 <= self.height <= MAX_HEIGHT:
            raise JobError("invalid_option")
        if self.subtitles not in SUBTITLE_MODES or len(self.languages) > 20:
            raise JobError("invalid_option")
        if any(not LANG.match(lang) for lang in self.languages):
            raise JobError("invalid_option")

    def public(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def tool() -> str | None:
    return convert.capabilities()["ffmpeg"]


def containers() -> list[str]:
    encoders = convert.capabilities()["encoders"]
    webm = "libvpx-vp9" in encoders and "libopus" in encoders
    return [c for c in CONTAINERS if c != "webm" or webm]


def audio_formats() -> list[str]:
    encoders = convert.capabilities()["encoders"]
    return [fmt for fmt, needs in AUDIO_ENCODERS.items() if any(name in encoders for name in needs)]


def failure(text: str, result: subprocess.CompletedProcess | None = None) -> str:
    if ytdlp.login_required(text):
        return "login_required"
    known = next((code for code, pattern in FAILURES if pattern.search(text)), None)
    if known:
        return known
    if result is not None:
        code = origins.refusal(result, text)
        if code:
            return code
    return "download_failed"


def clean_detail(text: str) -> str:
    lines = [COOKIE_ADVICE.sub("", line).rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip() and "cookies" not in line.lower())[-1200:]


def ytdlp_base() -> list[str]:
    cmd = [*deps.YTDLP_CMD, "--ignore-config", *ytdlp.NETWORK]
    deno = deps.deno_path()
    if deno:
        cmd += ["--js-runtimes", f"deno:{deno}"]
    return cmd


def format_sort(options: Options) -> str:
    keys = [f"res:{options.height}" if options.height else "res", "fps", "hdr" if options.hdr else "hdr:sdr"]
    if options.container == "mp4":
        keys += ["vcodec:h264", "acodec:aac"]
    elif options.container == "webm":
        keys += ["vcodec:vp9", "acodec:opus"]
    return ",".join(keys)


def output_ext(options: Options) -> str:
    return options.container if options.mode == "video" else AUDIO_EXT[options.audio]


def build_command(
    base: list[str], options: Options, url: str, workdir: Path, ffmpeg: str, limit: str | None = None
) -> list[str]:
    template = f"%(title).{NAME_LIMIT}B [%(id)s].%(ext)s"
    cmd = [
        *base,
        "--no-playlist",
        "--newline",
        "--no-colors",
        "--no-mtime",
        "--no-part",
        "--progress-template",
        "download:SPOTCHECK %(progress.status)s %(progress.downloaded_bytes)s %(progress.total_bytes)s "
        "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s",
        "--ffmpeg-location",
        ffmpeg,
        "--embed-metadata",
        "-P",
        str(workdir),
        "-o",
        template,
    ]
    if limit in RATE_LIMITS:
        cmd += ["--limit-rate", limit]
    if options.mode == "video":
        merge = "webm/mkv" if options.container == "webm" else options.container
        cmd += ["-f", "bv*+ba/b", "-S", format_sort(options), "--merge-output-format", merge]
        cmd += ["--recode-video" if options.container == "webm" else "--remux-video", options.container]
        if options.thumbnail and options.container != "webm":
            cmd += ["--embed-thumbnail"]
        if options.subtitles != "none":
            cmd += ["--write-subs", "--sub-langs", ",".join(options.languages) or "all,-live_chat"]
            if options.auto_subtitles:
                cmd += ["--write-auto-subs"]
            if options.subtitles == "embed":
                cmd += ["--embed-subs"]
            else:
                cmd += ["--convert-subs", "srt"]
    else:
        cmd += ["-f", "ba/b", "-x", "--audio-format", AUDIO_FORMATS[options.audio]]
        cmd += ["--audio-quality", f"{options.bitrate}K" if options.audio in LOSSY_BITRATE else "0"]
        if options.thumbnail and options.audio in THUMBNAIL_AUDIO:
            cmd += ["--embed-thumbnail"]
    cmd += ["--", url]
    return cmd


def _number(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def parse_progress(line: str) -> dict[str, Any] | None:
    m = PROGRESS.match(line.strip())
    if not m:
        return None
    status, done, total, estimate, speed, eta = m.groups()
    return {
        "status": status,
        "done": _number(done),
        "total": _number(total) or _number(estimate),
        "speed": _number(speed),
        "eta": _number(eta),
    }


def phase_of(line: str) -> str | None:
    stripped = line.strip()
    return next((phase for prefix, phase in PHASES.items() if stripped.startswith(prefix)), None)


_probes: dict[str, dict[str, Any]] = {}
_probes_lock = threading.Lock()


def _resolution(item: dict[str, Any]) -> int | None:
    sides = [int(item[k]) for k in ("width", "height") if isinstance(item.get(k), (int, float)) and item[k] > 0]
    return min(sides) if sides else None


def _unknown_video(info: dict[str, Any]) -> bool:
    formats = info.get("formats") or [info]
    return any(f.get("vcodec") is None and not _resolution(f) and f.get("url") for f in formats)


def _heights(info: dict[str, Any]) -> list[int]:
    found = {
        _resolution(f) for f in info.get("formats") or [] if f.get("vcodec") not in (None, "none") and _resolution(f)
    }
    if not found and _resolution(info):
        found.add(_resolution(info))
    return sorted(found, reverse=True)


def _size(f: dict[str, Any], duration: float | None) -> tuple[float | None, bool]:
    if f.get("filesize"):
        return float(f["filesize"]), True
    if f.get("filesize_approx"):
        return float(f["filesize_approx"]), False
    if f.get("tbr") and duration:
        return float(f["tbr"]) * 125 * duration, False
    return None, False


def _pick(formats: list[dict[str, Any]], tiers: tuple[tuple[str, ...], ...], key: str) -> dict[str, Any] | None:
    direct = [f for f in formats if not str(f.get("protocol") or "").startswith(("m3u8", "http_dash_segments"))]
    pool = direct or formats
    if not pool:
        return None
    for prefer in tiers:
        preferred = [f for f in pool if str(f.get(key) or "").startswith(prefer)]
        if preferred:
            pool = preferred
            break
    return max(pool, key=lambda f: (bool(f.get("filesize")), f.get("tbr") or f.get("abr") or 0))


def variants(info: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    duration = info.get("duration")
    formats = info.get("formats") or []
    video = [f for f in formats if f.get("vcodec") not in (None, "none") and _resolution(f)]
    audio = [f for f in formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    audio_sizes = {}
    for name, tiers in (("aac", (("mp4a",),)), ("opus", (("opus",),)), ("best", ())):
        picked = _pick(audio, tiers, "acodec")
        size = _size(picked, duration)[0] if picked else None
        if size:
            audio_sizes[name] = size
    result = []
    for height in _heights(info):
        fitting = [f for f in video if _resolution(f) <= height]
        top = max(_resolution(f) for f in fitting)
        level = [f for f in fitting if _resolution(f) == top]
        best_fps = max(f.get("fps") or 0 for f in level)
        level = [f for f in level if (f.get("fps") or 0) == best_fps]
        sdr = [f for f in level if str(f.get("dynamic_range") or "SDR") == "SDR"] or level
        sizes: dict[str, float | None] = {}
        exact = True
        for container, tiers, sound in (
            ("mp4", (("avc1", "h264"),), "aac"),
            ("webm", (("vp9", "vp09"),), "opus"),
            ("mkv", (("av01",), ("vp9", "vp09")), "best"),
        ):
            picked = _pick(sdr, tiers, "vcodec")
            size, known = _size(picked, duration) if picked else (None, False)
            exact = exact and known
            if size is not None and picked.get("acodec") in (None, "none"):
                extra = audio_sizes.get(sound) or audio_sizes.get("best")
                size = size + extra if extra else size
            sizes[container] = round(size) if size else None
        result.append({"height": height, "bytes": sizes, "exact": exact})
    return result, {k: round(v) for k, v in audio_sizes.items()}


def _has_audio(info: dict[str, Any]) -> bool:
    formats = info.get("formats") or []
    if not formats:
        return info.get("acodec") != "none"
    return any(f.get("acodec") != "none" for f in formats)


def _title(info: dict[str, Any], url: str) -> str:
    origin = (origins.classify_track(url) or ("",))[0]
    key = str(info.get("extractor_key") or info.get("ie_key") or "").lower()
    social = origin if origin in origins.SOCIAL else key if key in origins.SOCIAL else None
    if social:
        return origins.social_title(info, social)[1]
    return str(info.get("title") or info.get("id") or "")


def _entry(info: dict[str, Any], url: str) -> dict[str, Any]:
    return {
        "url": url,
        "id": str(info.get("id") or ""),
        "title": _title(info, url) or url,
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "duration": info.get("duration"),
    }


def _thumbnail(info: dict[str, Any]) -> str | None:
    thumb = info.get("thumbnail")
    if not thumb:
        thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
        thumb = thumbs[-1]["url"] if thumbs else None
    return thumb if isinstance(thumb, str) and thumb.lower().startswith("https://") else None


def _remember(result: dict[str, Any], thumbs: dict[str, str]) -> None:
    with _probes_lock:
        _probes[result["probe"]] = {"thumbs": thumbs, "t": time.time()}
        for key in sorted(_probes, key=lambda k: _probes[k]["t"])[:-PROBE_CACHE]:
            _probes.pop(key, None)


def thumbnail_url(probe_id: str, index: int) -> str | None:
    with _probes_lock:
        entry = _probes.get(probe_id)
    return entry["thumbs"].get(str(index)) if entry else None


def probe(url: str) -> dict[str, Any]:
    url = origins.normalize(url.strip())
    if not url.lower().startswith("https://"):
        raise JobError("download_invalid_url")
    if origins.SPOTIFY.match(url) or origins.SPOTIFY_TRACK.match(url):
        raise JobError("download_spotify")
    cmd = [*ytdlp_base(), "-J", "--flat-playlist", "--no-warnings", "--playlist-end", str(PLAYLIST_LIMIT), "--", url]
    try:
        r = ytdlp.run(cmd, PROBE_TIMEOUT, lambda res: res.stdout.strip() not in ("", "null"), shared=False)
    except subprocess.TimeoutExpired as exc:
        raise JobError("ytdlp_timeout") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise JobError("ytdlp_error") from exc
    try:
        info = json.loads(r.stdout) if r.stdout.strip() not in ("", "null") else None
    except ValueError:
        info = None
    if not isinstance(info, dict):
        stderr = r.stderr or ""
        raise JobError(failure(stderr, r), clean_detail(stderr)[-300:])
    probe_id = uuid.uuid4().hex[:12]
    thumbs: dict[str, str] = {}
    if info.get("_type") == "playlist" or info.get("entries") is not None:
        entries = []
        raw = [e for e in info.get("entries") or [] if isinstance(e, dict)]
        for n, item in enumerate(raw):
            link = item.get("webpage_url") or item.get("url")
            if not isinstance(link, str) or not link.startswith("https://"):
                continue
            if item.get("title") in ("[Private video]", "[Deleted video]"):
                continue
            entries.append({**_entry(item, link), "thumb": n})
            if _thumbnail(item):
                thumbs[str(n)] = _thumbnail(item)
        if not entries:
            raise JobError("download_empty")
        result = {
            "probe": probe_id,
            "kind": "playlist",
            "url": url,
            "title": str(info.get("title") or url),
            "uploader": info.get("uploader") or info.get("channel"),
            "entries": entries,
            "truncated": len(raw) >= PLAYLIST_LIMIT,
        }
    else:
        if not info.get("formats") and not info.get("url"):
            raise JobError("download_no_media")
        heights = _heights(info)
        sizes, audio_sizes = variants(info)
        result = {
            "probe": probe_id,
            "kind": "single",
            "url": url,
            **{k: v for k, v in _entry(info, url).items() if k != "url"},
            "heights": heights,
            "variants": sizes,
            "audio_bytes": audio_sizes,
            "live": bool(info.get("is_live")),
            "video": bool(heights) or _unknown_video(info),
            "audio": _has_audio(info),
            "subtitles": sorted(k for k in (info.get("subtitles") or {}) if LANG.match(k) and k != "live_chat"),
            "auto_subtitles": sorted(k for k in (info.get("automatic_captions") or {}) if LANG.match(k))[:200],
            "site": info.get("extractor_key") or info.get("extractor"),
            "thumb": 0,
        }
        if _thumbnail(info):
            thumbs["0"] = _thumbnail(info)
    _remember(result, thumbs)
    return result


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_https(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect_refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_https(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname:
        return False
    try:
        found = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    for *_, address in found:
        ip = ipaddress.ip_address(address[0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(found)


def fetch_thumbnail(url: str, timeout: float = 15) -> bytes:
    if not public_https(url):
        raise ValueError("thumbnail_refused")
    opener = urllib.request.build_opener(_SafeRedirect)
    request = urllib.request.Request(url, headers=sources.USER_AGENT)
    with opener.open(request, timeout=timeout) as response:
        data = response.read(THUMB_LIMIT + 1)
    if len(data) > THUMB_LIMIT:
        raise ValueError("thumbnail_too_large")
    return data


def safe_stem(name: str) -> str:
    return UNSAFE_CHARS.sub("_", name).strip(" .")[:NAME_LIMIT] or "download"


class Downloader:
    def __init__(self, bus) -> None:
        self.bus = bus
        self.cond = threading.Condition()
        self.items: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.threads: list[threading.Thread] = []
        self.workers = WORKERS
        self.paused = False
        self.limit: str | None = None
        self._closing = False

    @staticmethod
    def public_item(item: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in item.items() if not k.startswith("_")}

    def public(self) -> list[dict[str, Any]]:
        with self.cond:
            return [self.public_item(self.items[i]) for i in self.order]

    def busy(self) -> bool:
        with self.cond:
            return any(i["state"] in ("queued", "running") for i in self.items.values())

    def find(self, item_id: str) -> dict[str, Any] | None:
        with self.cond:
            return self.items.get(item_id)

    def _emit(self, item: dict[str, Any], force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - item["_emitted"] < EMIT_INTERVAL:
            return
        item["_emitted"] = now
        self.bus.emit({"type": "download", "item": self.public_item(item)})

    def _ensure_workers(self) -> None:
        self.threads = [t for t in self.threads if t.is_alive()]
        while len(self.threads) < self.workers:
            thread = threading.Thread(target=self._worker, name=f"spotcheck-download-{len(self.threads)}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def settings(self) -> dict[str, Any]:
        return {"paused": self.paused, "limit": self.limit}

    def configure(self, paused: bool | None = None, limit: str | None = None) -> dict[str, Any]:
        with self.cond:
            if paused is not None:
                self.paused = paused
            if limit is not None:
                if limit != "none" and limit not in RATE_LIMITS:
                    raise JobError("invalid_option")
                self.limit = None if limit == "none" else limit
            self.cond.notify_all()
        self.bus.emit({"type": "download_settings", **self.settings()})
        return self.settings()

    def add(self, entries: list[dict[str, Any]], raw_options: dict[str, Any], folder: Path) -> list[str]:
        options = Options.from_dict(raw_options)
        if not tool():
            raise JobError("ffmpeg_missing")
        if options.mode == "audio" and options.audio not in audio_formats():
            raise JobError("preset_unavailable")
        checked = []
        for entry in entries:
            url = str(entry.get("url") or "")
            if not url.startswith("https://"):
                raise JobError("download_invalid_url")
            raw = entry.get("options")
            own = Options.from_dict(raw) if isinstance(raw, dict) else options
            if own.mode == "audio" and own.audio not in audio_formats():
                raise JobError("preset_unavailable")
            if own.mode == "video" and own.container not in containers():
                raise JobError("preset_unavailable")
            checked.append((entry, url, own))
        ids = []
        with self.cond:
            for entry, url, own in checked:
                pending = next(
                    (
                        i
                        for i in self.items.values()
                        if i["url"] == url and i["options"] == own.public() and i["state"] in ("queued", "running")
                    ),
                    None,
                )
                if pending:
                    ids.append(pending["id"])
                    continue
                item = {
                    "id": uuid.uuid4().hex[:10],
                    "url": url,
                    "title": str(entry.get("title") or url)[:300],
                    "uploader": entry.get("uploader"),
                    "duration": entry.get("duration"),
                    "probe": entry.get("probe"),
                    "thumb": entry.get("thumb"),
                    "options": own.public(),
                    "ext": output_ext(own),
                    "folder": str(folder),
                    "state": "queued",
                    "phase": None,
                    "progress": 0.0,
                    "speed": None,
                    "eta": None,
                    "size": None,
                    "output": None,
                    "extra_files": [],
                    "actual": None,
                    "notes": [],
                    "error": None,
                    "detail": None,
                    "created": time.time(),
                    "started": None,
                    "finished": None,
                    "_options": own,
                    "_cancel": False,
                    "_proc": None,
                    "_emitted": 0.0,
                }
                self.items[item["id"]] = item
                self.order.append(item["id"])
                ids.append(item["id"])
            self._prune()
            self._ensure_workers()
            self.cond.notify_all()
        for item_id in dict.fromkeys(ids):
            self._emit(self.items[item_id])
        return ids

    def _prune(self) -> None:
        excess = len(self.order) - HISTORY
        for item_id in list(self.order):
            if excess <= 0:
                break
            if self.items[item_id]["state"] in ("done", "failed", "cancelled"):
                self.items.pop(item_id)
                self.order.remove(item_id)
                excess -= 1

    def _next(self) -> dict[str, Any] | None:
        if self.paused:
            return None
        return next((self.items[i] for i in self.order if self.items[i]["state"] == "queued"), None)

    def _worker(self) -> None:
        while True:
            with self.cond:
                item = self._next()
                while item is None and not self._closing:
                    self.cond.wait(30)
                    item = self._next()
                if self._closing:
                    return
                item["state"], item["started"], item["phase"] = "running", time.time(), "preparing"
            self._emit(item)
            try:
                self._download(item)
            except Exception as exc:
                log.exception("Download of %s failed", item["url"])
                item["state"], item["error"], item["detail"] = "failed", "download_failed", str(exc)[:300]
            item["finished"] = time.time()
            item["_proc"] = None
            self._emit(item)
            with self.cond:
                self.cond.notify_all()

    def _download(self, item: dict[str, Any]) -> None:
        options: Options = item["_options"]
        workdir = paths().home / "download-work" / item["id"]
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        outcome = "failed"
        placed: list[Path] = []
        try:
            outcome = self._run(item, options, workdir)
            if outcome == "done":
                placed = self._place(item, workdir)
                if not placed:
                    outcome, item["error"] = "failed", "download_empty_output"
                else:
                    self._describe(item, options, placed[0])
        except OSError as exc:
            outcome, item["error"], item["detail"] = "failed", "file_locked", str(exc)[:300]
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if outcome == "done":
                item["progress"], item["eta"], item["phase"] = 1.0, 0, None
                self.bus.log("info", "download_done", file=placed[0].name)
            item["state"] = outcome

    def _run(self, item: dict[str, Any], options: Options, workdir: Path) -> str:
        cmd = build_command(ytdlp_base(), options, item["url"], workdir, tool() or analysis.ffmpeg(), self.limit)
        lines: list[str] = []
        state = {"streams": 1, "index": -1, "last": time.monotonic(), "limit": STALL_TIMEOUT}
        stalled = threading.Event()
        finished = threading.Event()

        def on_start(proc: subprocess.Popen) -> None:
            item["_proc"] = proc
            if item["_cancel"]:
                analysis.kill_tree(proc)

        def on_line(line: str) -> None:
            state["last"] = time.monotonic()
            formats = FORMATS_LINE.search(line)
            if formats:
                state["streams"] = formats.group(1).count("+") + 1
                return
            if line.startswith("[download] Destination:"):
                state["index"] = min(state["index"] + 1, state["streams"] - 1)
                item["phase"] = "downloading"
                return
            progress = parse_progress(line)
            if progress:
                if state["index"] < 0:
                    state["index"] = 0
                    item["phase"] = "downloading"
                fraction = (progress["done"] / progress["total"]) if progress["done"] and progress["total"] else 0.0
                overall = (state["index"] + min(fraction, 1.0)) / state["streams"]
                item["progress"] = round(min(max(overall, item["progress"]), 0.99), 4)
                item["speed"] = progress["speed"]
                item["eta"] = progress["eta"]
                self._emit(item, force=False)
                return
            phase = phase_of(line)
            if phase:
                state["limit"] = POST_TIMEOUT
                item["phase"], item["speed"], item["eta"] = phase, None, None
                self._emit(item)
                return
            if line.strip():
                lines.append(line.strip()[:400])
                del lines[:-15]

        def watchdog() -> None:
            while not finished.wait(15):
                if time.monotonic() - state["last"] > state["limit"] and item.get("_proc") is not None:
                    stalled.set()
                    analysis.kill_tree(item["_proc"])
                    return

        guard = threading.Thread(target=watchdog, name="spotcheck-download-watch", daemon=True)
        guard.start()
        result = None
        try:
            for delay in (*ytdlp.RETRIES, None):
                result = analysis.run_streaming(cmd, on_line, on_start=on_start)
                done = result.returncode == 0 and not item["_cancel"]
                text = "\n".join(lines)
                if done or item["_cancel"] or delay is None or not ytdlp.blocked(text) or ytdlp.cooling():
                    break
                item["phase"] = "waiting"
                self._emit(item)
                analysis.pause(delay)
        except (OSError, subprocess.SubprocessError) as exc:
            item["error"], item["detail"] = "download_failed", str(exc)[:300]
            return "failed"
        finally:
            finished.set()
            item["_proc"] = None
        if item["_cancel"]:
            return "cancelled"
        if stalled.is_set():
            item["error"] = "download_stalled"
            return "failed"
        if result is None or result.returncode != 0:
            detail = "\n".join(lines[-6:])
            item["error"] = failure(detail, result)
            item["detail"] = clean_detail(detail) or None
            return "failed"
        return "done"

    def _describe(self, item: dict[str, Any], options: Options, path: Path) -> None:
        try:
            info = convert.probe(path)
        except JobError:
            return
        video = info.get("video") or {}
        sides = [v for v in (video.get("width"), video.get("height")) if isinstance(v, int) and v > 0]
        item["actual"] = {
            "height": min(sides) if sides else None,
            "vcodec": video.get("codec"),
            "acodec": (info.get("audio") or {}).get("codec"),
            "duration": info.get("duration"),
        }
        wanted = options.height
        if options.mode == "video" and wanted and item["actual"]["height"] and item["actual"]["height"] < wanted:
            item["notes"].append("lower_quality")
        if options.mode == "video" and options.subtitles == "embed" and not info.get("subtitles"):
            item["notes"].append("no_subtitles")
        if options.mode == "video" and options.subtitles == "files" and not item["extra_files"]:
            item["notes"].append("no_subtitles")

    def _place(self, item: dict[str, Any], workdir: Path) -> list[Path]:
        folder = Path(item["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        produced = sorted((p for p in workdir.iterdir() if p.is_file()), key=lambda p: p.stat().st_size, reverse=True)
        media = [p for p in produced if p.suffix.lower() in MEDIA_EXT]
        keep_subtitles = item["options"].get("subtitles") == "files"
        extras = [p for p in produced if keep_subtitles and p.suffix.lower() in SUBTITLE_EXT]
        if not media:
            return []
        placed = []
        main = media[0]
        stem = safe_stem(item["title"] if item["title"] != item["url"] else main.stem)
        target = fileops.reserve(folder / f"{stem}{main.suffix.lower()}")
        try:
            fileops.install(main, target)
        except OSError:
            fileops.release(target)
            raise
        placed.append(target)
        item["output"], item["size"] = str(target), target.stat().st_size
        for extra in extras:
            suffix = "".join(extra.suffixes[-2:]) if len(extra.suffixes) > 1 else extra.suffix
            extra_target = fileops.reserve(folder / f"{target.stem}{suffix}")
            try:
                fileops.install(extra, extra_target)
            except OSError:
                fileops.release(extra_target)
                continue
            placed.append(extra_target)
            item["extra_files"].append(str(extra_target))
        return placed

    def cancel(self, item_id: str) -> bool:
        with self.cond:
            item = self.items.get(item_id)
            if not item or item["state"] not in ("queued", "running"):
                return False
            item["_cancel"] = True
            if item["state"] == "queued":
                item["state"], item["finished"] = "cancelled", time.time()
            proc = item.get("_proc")
        if proc is not None:
            analysis.kill_tree(proc)
        self._emit(item)
        return True

    def cancel_all(self) -> int:
        with self.cond:
            ids = [i for i, item in self.items.items() if item["state"] in ("queued", "running")]
        return sum(self.cancel(i) for i in ids)

    def retry(self, item_id: str) -> str:
        with self.cond:
            item = self.items.get(item_id)
            if not item or item["state"] not in ("failed", "cancelled"):
                raise JobError("download_not_found")
        entry = {k: item[k] for k in ("url", "title", "uploader", "duration", "probe", "thumb", "options")}
        new = self.add([entry], item["options"], Path(item["folder"]))
        with self.cond:
            if item_id in self.items:
                self.items.pop(item_id)
                self.order.remove(item_id)
        self.bus.emit({"type": "download_list"})
        return new[0]

    def clear(self) -> int:
        with self.cond:
            ids = [i for i in self.order if self.items[i]["state"] in ("done", "failed", "cancelled")]
            for item_id in ids:
                self.items.pop(item_id)
                self.order.remove(item_id)
        self.bus.emit({"type": "download_list"})
        return len(ids)

    def close(self, timeout: float = 10) -> None:
        with self.cond:
            self._closing = True
            self.cond.notify_all()
        self.cancel_all()
        deadline = time.monotonic() + timeout
        with self.cond:
            while any(i["state"] == "running" for i in self.items.values()) and time.monotonic() < deadline:
                self.cond.wait(0.2)


def clean_workspace() -> None:
    shutil.rmtree(paths().home / "download-work", ignore_errors=True)

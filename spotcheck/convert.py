from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import analysis, codecs
from .config import paths
from .errors import JobError

log = logging.getLogger(__name__)


def names(text: str) -> frozenset[str]:
    return frozenset(text.split())


PROBE_TIMEOUT = 60
HW_TIMEOUT = 20
STALL_TIMEOUT = 600
EMIT_INTERVAL = 0.4
HISTORY = 300
NAME_LIMIT = 150
QUALITIES = ("high", "balanced", "small")
TRIM_LIMIT = 7 * 24 * 3600

ENCODER_LINE = re.compile(r"^ ([VAS])[F.][S.]([X.])[B.][D.] (\S+)\s+(.*)$")
MUXER_LINE = re.compile(r"^ +[D.]?E[d.]? +(\S+) +(.*)$")
PROGRESS_LINE = re.compile(r"^([a-z_0-9]+)=(\S*)$")
CODEC_NOTE = re.compile(r"\(codec (\S+)\)")
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,40}$")
UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

LOSSY_AUDIO = names(
    "mp3 mp2 aac vorbis opus wmav1 wmav2 wmapro ac3 eac3 dts amr_nb amr_wb atrac3 atrac3p cook ra_144 "
    "ra_288 speex gsm gsm_ms nellymoser qdm2 adpcm_ms adpcm_ima_wav mp1 aac_latm ac4 usac xma2"
)
LOSSLESS_AUDIO = names("flac alac wavpack tta ape mlp truehd shorten tak wmalossless ffwavesynth")
COPY_AUDIO = {
    "aac": ("m4a", "mp4"),
    "alac": ("m4a", "mp4"),
    "mp3": ("mp3", "mp3"),
    "opus": ("opus", "opus"),
    "vorbis": ("ogg", "ogg"),
    "flac": ("flac", "flac"),
    "ac3": ("ac3", "ac3"),
    "eac3": ("eac3", "eac3"),
    "pcm_s16le": ("wav", "wav"),
    "pcm_s24le": ("wav", "wav"),
    "pcm_f32le": ("wav", "wav"),
    "wmav2": ("wma", "asf"),
}
COVER_CODECS = {"mjpeg", "png"}
HARDWARE = {
    "h264": ("h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"),
    "hevc": ("hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_videotoolbox"),
    "av1": ("av1_nvenc", "av1_qsv", "av1_amf"),
}
HW_QUALITY = {
    "h264": {"high": 19, "balanced": 23, "small": 28},
    "hevc": {"high": 21, "balanced": 26, "small": 31},
    "av1": {"high": 28, "balanced": 34, "small": 40},
}
VT_QUALITY = {"high": 72, "balanced": 60, "small": 46}
CRF_ENCODERS = {
    "libx264": (18, 23, 28),
    "libx264rgb": (18, 23, 28),
    "libx265": (20, 26, 31),
    "libsvtav1": (28, 35, 42),
    "libaom-av1": (28, 35, 42),
    "libvpx-vp9": (24, 32, 38),
    "libvpx": (8, 12, 20),
    "libvvenc": (28, 32, 38),
}
QSCALE_ENCODERS = names(
    "mpeg4 msmpeg4 msmpeg4v2 mpeg2video mpeg1video mjpeg flv h263 h263p wmv1 wmv2 libxvid libtheora"
)
BITRATE_VIDEO = ("8M", "4M", "1500k")
BITRATE_AUDIO = ("320k", "192k", "128k")
LOSSLESS_ENCODERS = names(
    "flac alac wavpack tta mlp truehd s302m ffv1 huffyuv ffvhuff utvideo png apng qtrle libx264rgb"
)
SKIP_MUXERS = names(
    "null tee fifo segment stream_segment ssegment hls dash smoothstreaming rtp rtp_mpegts rtsp sap "
    "image2 image2pipe framecrc framehash framemd5 crc hash md5 streamhash uncodedframecrc chromaprint "
    "ffmetadata webm_chunk webm_dash_manifest sdl sdl2 fbdev alsa oss opengl pulse xv audiotoolbox caca "
    "openal data whip"
)
RESOLUTIONS = (2160, 1440, 1080, 720, 480, 360)
FRAME_RATES = (60, 50, 30, 25, 24)
SAMPLE_RATES = (32000, 44100, 48000, 88200, 96000, 192000)
RATE_LIMITS = {
    "libopus": (48000, 24000, 16000, 12000, 8000),
    "libmp3lame": (32000, 44100, 48000),
    "ac3": (32000, 44100, 48000),
    "wmav2": (32000, 44100, 48000),
    "aac": (32000, 44100, 48000, 88200, 96000),
}
TEXT_FIELDS = ("preset", "quality", "container", "video", "audio")
NUMBER_FIELDS = ("resolution", "fps", "sample_rate", "channels", "start", "end")
FLAG_FIELDS = ("normalize", "mute", "hardware")


@dataclass(frozen=True)
class Quality:
    id: str
    detail: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    kind: str
    ext: str
    muxer: str
    needs: tuple[str, ...]
    qualities: tuple[Quality, ...]
    audio: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    family: str | None = None
    cover: bool = False
    copy: bool = False
    lossless: bool = False
    group: str = "common"
    pix_fmt: str = "yuv420p"


def q(qid: str, detail: str, args: str) -> Quality:
    return Quality(qid, detail, tuple(args.split()))


def ladder(base: str, flag: str, values: tuple[str, ...], label: str, ids: tuple[str, ...] = QUALITIES):
    return tuple(q(i, label.format(v), f"{base} {flag} {v}") for i, v in zip(ids, values, strict=True))


def sound(pid: str, name: str, ext: str, muxer: str, encoder: str, qualities, extra: str = "", **kw) -> Preset:
    return Preset(pid, name, "audio", ext, muxer, (encoder,), qualities, extra=tuple(extra.split()), **kw)


def film(pid: str, name: str, ext: str, muxer: str, needs: str, qualities, audio: str = "", extra: str = "", **kw):
    return Preset(
        pid,
        name,
        "video",
        ext,
        muxer,
        tuple(needs.split()),
        qualities,
        tuple(audio.split()),
        tuple(extra.split()),
        **kw,
    )


AAC = "-c:a aac -b:a 192k"
OPUS = "-c:a libopus -b:a 128k"
FASTSTART = "-movflags +faststart"
X264 = ladder("-c:v libx264 -preset medium", "-crf", ("18", "23", "28"), "CRF {}")
X265 = ladder("-c:v libx265 -preset medium", "-crf", ("20", "26", "31"), "CRF {}")
SVT_AV1 = ladder("-c:v libsvtav1 -preset 8", "-crf", ("28", "35", "42"), "CRF {}")
VP9 = ladder("-c:v libvpx-vp9 -b:v 0 -row-mt 1 -deadline good -cpu-used 3", "-crf", ("24", "32", "38"), "CRF {}")
PCM = ("16bit", "24bit", "32float")

PRESETS: tuple[Preset, ...] = (
    sound(
        "mp3",
        "MP3",
        "mp3",
        "mp3",
        "libmp3lame",
        (
            q("high", "320 kbps", "-c:a libmp3lame -b:a 320k"),
            q("balanced", "VBR V0", "-c:a libmp3lame -q:a 0"),
            q("small", "VBR V4", "-c:a libmp3lame -q:a 4"),
        ),
        "-id3v2_version 3 -write_id3v1 1",
        cover=True,
    ),
    sound(
        "m4a",
        "M4A (AAC)",
        "m4a",
        "ipod",
        "aac",
        ladder("-c:a aac", "-b:a", ("320k", "256k", "128k"), "{}bps"),
        FASTSTART,
        cover=True,
    ),
    sound("opus", "Opus", "opus", "opus", "libopus", ladder("-c:a libopus", "-b:a", ("256k", "160k", "96k"), "{}bps")),
    sound("ogg", "OGG Vorbis", "ogg", "ogg", "libvorbis", ladder("-c:a libvorbis", "-q:a", ("8", "6", "3"), "Q{}")),
    sound(
        "flac",
        "FLAC",
        "flac",
        "flac",
        "flac",
        (
            q("lossless", "16/24-bit", "-c:a flac -compression_level 8"),
            q("cd", "16-bit", "-c:a flac -compression_level 8 -sample_fmt s16"),
        ),
        cover=True,
        lossless=True,
    ),
    sound(
        "alac",
        "ALAC",
        "m4a",
        "ipod",
        "alac",
        (q("lossless", "16/24-bit", "-c:a alac"),),
        FASTSTART,
        cover=True,
        lossless=True,
    ),
    sound(
        "wav",
        "WAV",
        "wav",
        "wav",
        "pcm_s16le",
        ladder("", "-c:a", ("pcm_s16le", "pcm_s24le", "pcm_f32le"), "{}", PCM),
        lossless=True,
    ),
    sound(
        "aiff",
        "AIFF",
        "aiff",
        "aiff",
        "pcm_s16be",
        ladder("", "-c:a", ("pcm_s16be", "pcm_s24be"), "{}", PCM[:2]),
        lossless=True,
    ),
    sound(
        "wma",
        "WMA",
        "wma",
        "asf",
        "wmav2",
        ladder("-c:a wmav2", "-b:a", ("192k", "128k"), "{}bps", QUALITIES[::2]),
        group="more",
    ),
    sound(
        "ac3",
        "AC-3",
        "ac3",
        "ac3",
        "ac3",
        ladder("-c:a ac3", "-b:a", ("640k", "448k"), "{}bps", QUALITIES[:2]),
        group="more",
    ),
    Preset("audio_copy", "", "audio", "", "", (), (q("original", "", "-c:a copy"),), copy=True),
    film("mp4_h264", "MP4 · H.264", "mp4", "mp4", "libx264 aac", X264, AAC, FASTSTART, family="h264"),
    film(
        "mp4_h265",
        "MP4 · H.265 (HEVC)",
        "mp4",
        "mp4",
        "libx265 aac",
        X265,
        AAC,
        "-tag:v hvc1 " + FASTSTART,
        family="hevc",
    ),
    film("mp4_av1", "MP4 · AV1", "mp4", "mp4", "libsvtav1 aac", SVT_AV1, AAC, FASTSTART, family="av1"),
    film("webm_vp9", "WebM · VP9", "webm", "webm", "libvpx-vp9 libopus", VP9, OPUS),
    film("webm_av1", "WebM · AV1", "webm", "webm", "libsvtav1 libopus", SVT_AV1, OPUS, family="av1"),
    film("mkv_copy", "MKV", "mkv", "matroska", "", (q("original", "", "-c copy"),), copy=True),
    film("mp4_copy", "MP4", "mp4", "mp4", "", (q("original", "", "-c copy"),), extra=FASTSTART, copy=True),
    film("mkv_h265", "MKV · H.265 (HEVC)", "mkv", "matroska", "libx265 aac", X265, AAC, family="hevc", group="more"),
    film("mov_h264", "MOV · H.264", "mov", "mov", "libx264 aac", X264, AAC, FASTSTART, family="h264", group="more"),
    film(
        "mov_prores",
        "MOV · ProRes",
        "mov",
        "mov",
        "prores_ks pcm_s16le",
        ladder("-c:v prores_ks -vendor apl0", "-profile:v", ("3", "2", "0"), "{}", ("hq", "standard", "proxy")),
        "-c:a pcm_s16le",
        group="more",
        pix_fmt="yuv422p10le",
    ),
    film(
        "avi",
        "AVI · MPEG-4",
        "avi",
        "avi",
        "mpeg4 libmp3lame",
        ladder("-c:v mpeg4 -tag:v XVID", "-q:v", ("3", "5", "8"), "Q {}"),
        "-c:a libmp3lame -b:a 192k",
        group="more",
    ),
    film(
        "wmv",
        "WMV",
        "wmv",
        "asf",
        "wmv2 wmav2",
        ladder("-c:v wmv2", "-b:v", ("8M", "4M", "1500k"), "{}bit/s"),
        "-c:a wmav2 -b:a 192k",
        group="more",
    ),
    film(
        "gif",
        "GIF",
        "gif",
        "gif",
        "gif",
        (
            q("high", "640 px · 15 fps", "640 15"),
            q("balanced", "480 px · 12 fps", "480 12"),
            q("small", "320 px · 10 fps", "320 10"),
        ),
        group="more",
    ),
)
PRESET_BY_ID = {p.id: p for p in PRESETS}


def _sibling(ffmpeg: str, name: str) -> str | None:
    path = Path(ffmpeg)
    candidate = path.with_name(name + path.suffix)
    if candidate.is_file():
        return str(candidate)
    return shutil.which(name)


def tools() -> dict[str, Any]:
    full = codecs.ffmpeg_path()
    ffmpeg = full or analysis.ffmpeg_path()
    if not ffmpeg:
        return {"ffmpeg": None, "ffprobe": None, "extended": False}
    return {"ffmpeg": ffmpeg, "ffprobe": _sibling(ffmpeg, "ffprobe"), "extended": bool(full)}


def parse_encoders(text: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        m = ENCODER_LINE.match(line)
        if not m or m.group(3) == "=":
            continue
        kind = {"V": "video", "A": "audio", "S": "subtitle"}[m.group(1)]
        note = CODEC_NOTE.search(m.group(4))
        description = CODEC_NOTE.sub("", m.group(4)).strip()
        found[m.group(3)] = {
            "kind": kind,
            "codec": note.group(1) if note else m.group(3),
            "experimental": m.group(2) == "X",
            "description": description,
        }
    return found


def parse_muxers(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    started = False
    for line in text.splitlines():
        if line.strip().startswith("--"):
            started = True
            continue
        m = MUXER_LINE.match(line) if started else None
        if m:
            for name in m.group(1).split(","):
                found[name] = m.group(2).strip()
    return found


def parse_muxer_help(text: str) -> dict[str, Any]:
    info: dict[str, Any] = {"ext": None, "video": None, "audio": None}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Common extensions:"):
            exts = [e.strip(" .") for e in line.split(":", 1)[1].split(",")]
            info["ext"] = next((e for e in exts if e), None)
        elif line.startswith("Default video codec:"):
            info["video"] = line.split(":", 1)[1].strip(" .")
        elif line.startswith("Default audio codec:"):
            info["audio"] = line.split(":", 1)[1].strip(" .")
    return info


_caps: dict[str, Any] = {}
_caps_lock = threading.Lock()


def _hardware_works(ffmpeg: str, encoder: str) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:r=30:d=0.2"]
    cmd += ["-frames:v", "5", "-c:v", encoder, "-f", "null", "-"]
    try:
        return analysis.run_text(cmd, timeout=HW_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def capabilities(refresh: bool = False) -> dict[str, Any]:
    t = tools()
    ffmpeg = t["ffmpeg"]
    key = f"{ffmpeg}|{_mtime(ffmpeg)}"
    with _caps_lock:
        if not refresh and _caps.get("key") == key:
            return _caps["value"]
        value: dict[str, Any] = {"encoders": {}, "muxers": {}, "hardware": {}, "version": None, **t}
        if ffmpeg:
            try:
                value["encoders"] = parse_encoders(
                    analysis.run_text([ffmpeg, "-hide_banner", "-encoders"], timeout=30).stdout
                )
                value["muxers"] = parse_muxers(
                    analysis.run_text([ffmpeg, "-hide_banner", "-muxers"], timeout=30).stdout
                )
                first = analysis.run_text([ffmpeg, "-hide_banner", "-version"], timeout=30).stdout.splitlines()[:1]
                m = re.match(r"ffmpeg version (\S+)", first[0]) if first else None
                value["version"] = m.group(1) if m else None
            except (OSError, subprocess.SubprocessError):
                log.exception("Reading the FFmpeg capabilities failed")
            candidates = [e for names in HARDWARE.values() for e in names if e in value["encoders"]]
            with cf.ThreadPoolExecutor(max_workers=4) as pool:
                works = dict(zip(candidates, pool.map(lambda e: _hardware_works(ffmpeg, e), candidates), strict=True))
            value["hardware"] = {
                family: next((e for e in names if works.get(e)), None) for family, names in HARDWARE.items()
            }
        _caps.update(key=key, value=value, formats=None)
        return value


def _mtime(path: str | None) -> float:
    try:
        return os.path.getmtime(path) if path else 0.0
    except OSError:
        return 0.0


def formats() -> dict[str, Any]:
    caps = capabilities()
    with _caps_lock:
        cached = _caps.get("formats")
    if cached is not None:
        return cached
    ffmpeg = caps["ffmpeg"]
    names = sorted(n for n in caps["muxers"] if n not in SKIP_MUXERS)

    def details(name: str) -> tuple[str, dict[str, Any]]:
        try:
            out = analysis.run_text([ffmpeg, "-hide_banner", "-h", f"muxer={name}"], timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        return name, parse_muxer_help(out)

    containers = []
    if ffmpeg:
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            for name, info in pool.map(details, names):
                if info["ext"] and SAFE_NAME.match(info["ext"]):
                    containers.append({"id": name, "name": caps["muxers"][name], **info})
    hardware_only = {e for names in HARDWARE.values() for e in names} - set(caps["hardware"].values())
    encoders = [
        {"id": name, **info}
        for name, info in sorted(caps["encoders"].items())
        if info["kind"] in ("video", "audio")
        and name not in hardware_only
        and not name.endswith(("_mf", "_vaapi", "_vulkan", "_d3d12va"))
    ]
    result = {"containers": containers, "encoders": encoders}
    with _caps_lock:
        _caps["formats"] = result
    return result


def preset_available(preset: Preset, caps: dict[str, Any]) -> bool:
    if preset.copy:
        return bool(caps["ffmpeg"])
    if preset.muxer not in caps["muxers"]:
        return False
    return all(name in caps["encoders"] for name in preset.needs)


def _codec_in(args: tuple[str, ...]) -> str | None:
    return args[args.index("-c:a") + 1] if "-c:a" in args else None


def audio_encoder(preset: Preset) -> str | None:
    if preset.copy:
        return None
    if preset.kind == "audio":
        return preset.needs[0] if preset.needs else None
    return _codec_in(preset.audio)


def allowed_rates(preset: Preset) -> tuple[int, ...]:
    limits = RATE_LIMITS.get(audio_encoder(preset) or "")
    return tuple(r for r in SAMPLE_RATES if limits is None or r in limits)


def public_presets(caps: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "kind": p.kind,
            "ext": p.ext,
            "group": p.group,
            "copy": p.copy,
            "lossless": p.lossless,
            "available": preset_available(p, caps),
            "hardware": bool(p.family and caps["hardware"].get(p.family)),
            "family": p.family,
            "rates": list(allowed_rates(p)),
            "qualities": [{"id": x.id, "detail": x.detail} for x in p.qualities],
        }
        for p in PRESETS
    ]


def _rate(text: str | None) -> float | None:
    if not text or text in ("0/0", "0"):
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(text)
    except ValueError:
        return None


def number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def parse_probe(data: dict[str, Any]) -> dict[str, Any]:
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = audio = None
    cover = None
    audio_count = 0
    subtitles = []
    for stream in streams:
        kind = stream.get("codec_type")
        attached = (stream.get("disposition") or {}).get("attached_pic") == 1
        if kind == "video" and attached:
            if cover is None:
                cover = {"index": stream.get("index"), "codec": stream.get("codec_name")}
        elif kind == "video" and video is None:
            video = {
                "codec": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "fps": _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate")),
                "pix_fmt": stream.get("pix_fmt"),
            }
        elif kind == "subtitle":
            subtitles.append(stream.get("codec_name"))
        elif kind == "audio":
            audio_count += 1
            if audio is None:
                audio = {
                    "codec": stream.get("codec_name"),
                    "sample_rate": int(stream.get("sample_rate") or 0) or None,
                    "channels": stream.get("channels"),
                    "bitrate": int(number(stream.get("bit_rate")) or 0) or None,
                }
    if video and video["codec"] in ("mjpeg", "png", "bmp", "webp", "gif") and (video["fps"] or 0) < 1 and cover is None:
        cover, video = {"index": None, "codec": video["codec"]}, None
    duration = number(fmt.get("duration"))
    return {
        "duration": duration,
        "format": fmt.get("format_name"),
        "bitrate": int(number(fmt.get("bit_rate")) or 0) or None,
        "video": video,
        "audio": audio,
        "audio_streams": audio_count,
        "subtitles": subtitles,
        "cover": cover,
        "tags": {k.lower(): v for k, v in (fmt.get("tags") or {}).items() if isinstance(v, str)},
    }


def probe(path: Path) -> dict[str, Any]:
    t = tools()
    if not t["ffprobe"]:
        raise JobError("ffprobe_missing")
    cmd = [t["ffprobe"], "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        result = analysis.run_text(cmd, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise JobError("probe_failed") from exc
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    if result.returncode != 0 or not data.get("streams"):
        raise JobError("not_media")
    info = parse_probe(data)
    if not info["video"] and not info["audio"]:
        raise JobError("not_media")
    return info


def describe(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": str(path), "name": path.name, "size": None, "info": None, "error": None}
    try:
        if not path.is_absolute():
            raise JobError("path_not_absolute")
        if not path.is_file():
            raise JobError("file_not_found")
        entry["size"] = path.stat().st_size
        entry["info"] = probe(path)
    except JobError as exc:
        entry["error"] = exc.code
    except OSError:
        entry["error"] = "file_not_found"
    return entry


@dataclass
class Target:
    preset: str | None = None
    quality: str | None = None
    container: str | None = None
    video: str | None = None
    audio: str | None = None
    resolution: int | None = None
    fps: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    start: float | None = None
    end: float | None = None
    normalize: bool = False
    mute: bool = False
    hardware: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Target:
        known = {k: raw.get(k) for k in cls.__dataclass_fields__ if k in raw}
        for key, value in known.items():
            if value is None:
                continue
            if key in TEXT_FIELDS and not isinstance(value, str):
                raise JobError("invalid_option")
            if key in NUMBER_FIELDS and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise JobError("invalid_option")
            if key in FLAG_FIELDS and not isinstance(value, bool):
                raise JobError("invalid_option")
        return cls(**{k: v for k, v in known.items() if v is not None or k not in FLAG_FIELDS})


def validate_target(target: Target, caps: dict[str, Any], containers: set[str] | None = None) -> None:
    if target.preset:
        preset = PRESET_BY_ID.get(target.preset)
        if preset is None:
            raise JobError("unknown_preset")
        if not preset_available(preset, caps):
            raise JobError("preset_unavailable")
        if target.quality and target.quality not in {x.id for x in preset.qualities}:
            raise JobError("unknown_quality")
        if target.sample_rate is not None and target.sample_rate not in allowed_rates(preset):
            raise JobError("invalid_option")
    else:
        container = target.container
        if not container or container not in caps["muxers"] or not SAFE_NAME.match(container):
            raise JobError("unknown_container")
        if container in SKIP_MUXERS or (containers is not None and container not in containers):
            raise JobError("unknown_container")
        for name in (target.video, target.audio):
            if name in (None, "copy", "none"):
                continue
            if not SAFE_NAME.match(name) or name not in caps["encoders"]:
                raise JobError("encoder_unavailable")
        if target.video in (None, "none") and target.audio in (None, "none"):
            raise JobError("nothing_to_convert")
        if target.quality and target.quality not in QUALITIES:
            raise JobError("unknown_quality")
    if target.resolution is not None and target.resolution not in RESOLUTIONS:
        raise JobError("invalid_option")
    if target.fps is not None and target.fps not in FRAME_RATES:
        raise JobError("invalid_option")
    if target.sample_rate is not None and target.sample_rate not in SAMPLE_RATES:
        raise JobError("invalid_option")
    if target.channels is not None and target.channels not in (1, 2):
        raise JobError("invalid_option")
    for value in (target.start, target.end):
        if value is not None and not (isinstance(value, (int, float)) and 0 <= value <= TRIM_LIMIT):
            raise JobError("invalid_trim")
    if target.start is not None and target.end is not None and target.end <= target.start:
        raise JobError("invalid_trim")


def target_kind(target: Target) -> str:
    if target.preset:
        return PRESET_BY_ID[target.preset].kind
    return "video" if target.video not in (None, "none") else "audio"


def check_source(info: dict[str, Any], target: Target) -> None:
    kind = target_kind(target)
    if kind == "video" and not info["video"]:
        raise JobError("no_video")
    if kind == "audio" and not info["audio"]:
        raise JobError("no_audio")


def output_format(target: Target, info: dict[str, Any]) -> tuple[str, str]:
    if target.preset == "audio_copy":
        return COPY_AUDIO.get((info.get("audio") or {}).get("codec"), ("mka", "matroska"))
    if target.preset:
        preset = PRESET_BY_ID[target.preset]
        return preset.ext, preset.muxer
    fmt = next((c for c in formats()["containers"] if c["id"] == target.container), None)
    ext = fmt["ext"] if fmt else target.container
    return ext, target.container


def warnings(info: dict[str, Any], target: Target) -> list[str]:
    found = []
    source = (info.get("audio") or {}).get("codec")
    lossless_target = (target.preset and PRESET_BY_ID[target.preset].lossless) or (
        not target.preset and target.audio in LOSSLESS_ENCODERS | {"pcm_s16le", "pcm_s24le", "pcm_f32le"}
    )
    if lossless_target and source in LOSSY_AUDIO:
        found.append("lossy_to_lossless")
    video = info.get("video") or {}
    if target.resolution and video.get("height") and target.resolution > video["height"]:
        found.append("no_upscale")
    return found


def _audio_filters(target: Target) -> list[str]:
    return ["loudnorm=I=-16:TP=-1.5:LRA=11"] if target.normalize else []


def _output_rate(target: Target, info: dict[str, Any], encoder: str | None) -> int | None:
    source = (info.get("audio") or {}).get("sample_rate")
    rate = target.sample_rate or (source if target.normalize else None)
    limits = RATE_LIMITS.get(encoder or "")
    current = rate or source
    if limits and current and current not in limits:
        rate = 44100 if current % 44100 == 0 and 44100 in limits else 48000 if 48000 in limits else limits[0]
    return rate or (48000 if target.normalize else None)


def _audio_options(target: Target, info: dict[str, Any], encoder: str | None) -> list[str]:
    args = []
    rate = _output_rate(target, info, encoder)
    if rate:
        args += ["-ar", str(rate)]
    if target.channels:
        args += ["-ac", str(target.channels)]
    filters = _audio_filters(target)
    if filters:
        args += ["-af", ",".join(filters)]
    return args


def _video_filters(target: Target, pix_fmt: str | None) -> list[str]:
    chain = []
    if target.resolution:
        chain.append(f"scale=-2:'trunc(min({target.resolution},ih)/2)*2'")
    else:
        chain.append("scale='trunc(iw/2)*2':'trunc(ih/2)*2'")
    if target.fps:
        chain.append(f"fps={target.fps}")
    if pix_fmt:
        chain.append(f"format={pix_fmt}")
    return chain


def _hardware_args(encoder: str, family: str, quality: str) -> tuple[list[str], str]:
    level = HW_QUALITY[family].get(quality, HW_QUALITY[family]["balanced"])
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", str(level), "-b:v", "0"], "yuv420p"
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-preset", "medium", "-global_quality", str(level)], "nv12"
    if encoder.endswith("_amf"):
        return ["-c:v", encoder, "-quality", "balanced", "-rc", "cqp", "-qp_i", str(level), "-qp_p", str(level)], "nv12"
    vt = VT_QUALITY.get(quality, VT_QUALITY["balanced"])
    return ["-c:v", encoder, "-q:v", str(vt)], "nv12"


def _custom_video(encoder: str, quality: str) -> list[str]:
    index = QUALITIES.index(quality) if quality in QUALITIES else 1
    args = ["-c:v", encoder]
    if encoder in CRF_ENCODERS:
        args += ["-crf", str(CRF_ENCODERS[encoder][index])]
        if encoder.startswith("libvpx"):
            args += ["-b:v", "0", "-row-mt", "1"]
        if encoder == "libaom-av1":
            args += ["-cpu-used", "6", "-row-mt", "1"]
    elif encoder in QSCALE_ENCODERS:
        args += ["-q:v", ("3", "5", "8")[index]]
    elif encoder not in LOSSLESS_ENCODERS:
        args += ["-b:v", BITRATE_VIDEO[index]]
    return args


def _custom_audio(encoder: str, quality: str) -> list[str]:
    index = QUALITIES.index(quality) if quality in QUALITIES else 1
    args = ["-c:a", encoder]
    if encoder == "libvorbis":
        args += ["-q:a", ("8", "6", "3")[index]]
    elif encoder not in LOSSLESS_ENCODERS and not encoder.startswith("pcm_"):
        args += ["-b:a", BITRATE_AUDIO[index]]
    return args


def _cover_args(info: dict[str, Any]) -> list[str]:
    cover = info.get("cover")
    if not cover or cover.get("index") is None:
        return ["-vn"]
    codec = "copy" if cover.get("codec") in COVER_CODECS else "mjpeg"
    return ["-map", f"0:{cover['index']}", "-c:v", codec, "-disposition:v:0", "attached_pic"]


def build_command(
    ffmpeg: str,
    src: Path,
    dst: Path,
    target: Target,
    info: dict[str, Any],
    hardware: dict[str, str | None] | None = None,
    muxer: str | None = None,
) -> tuple[list[str], str | None]:
    hardware = hardware or {}
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if target.start:
        cmd += ["-ss", f"{target.start:.3f}"]
    if target.end:
        cmd += ["-to", f"{target.end:.3f}"]
    cmd += ["-i", str(src), "-map_metadata", "0"]
    used_hw = None
    preset = PRESET_BY_ID.get(target.preset or "")
    quality_id = target.quality
    if preset:
        quality = next((x for x in preset.qualities if x.id == quality_id), preset.qualities[0])
        quality_id = quality.id
    if preset and preset.kind == "audio":
        cmd += ["-map", "0:a:0"]
        if preset.copy:
            cmd += _cover_args(info) if dst.suffix.lower() in (".mp3", ".m4a", ".flac") else ["-vn"]
            cmd += ["-c:a", "copy"]
        else:
            cmd += _cover_args(info) if preset.cover else ["-vn"]
            cmd += list(quality.args) + _audio_options(target, info, _codec_in(quality.args))
        cmd += ["-sn", "-dn", *preset.extra]
    elif preset and preset.id == "gif":
        width, fps = quality.args
        graph = (
            f"[0:V:0]fps={fps},scale='min({width},iw)':-1:flags=lanczos,split[a][b];"
            "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4[g]"
        )
        cmd += ["-filter_complex", graph, "-map", "[g]", "-an", "-loop", "0"]
    elif preset and preset.copy:
        cmd += ["-map", "0:v?", "-map", "0:a?"]
        if preset.muxer == "matroska":
            cmd += ["-map", "0:s?", "-map", "0:t?"]
        cmd += ["-c", "copy"]
        if preset.muxer == "matroska" and "mov_text" in (info.get("subtitles") or []):
            cmd += ["-c:s", "srt"]
        cmd += list(preset.extra)
    elif preset:
        video_args = list(quality.args)
        pix_fmt = preset.pix_fmt
        encoder = hardware.get(preset.family) if preset.family and target.hardware else None
        if encoder:
            video_args, pix_fmt = _hardware_args(encoder, preset.family, quality_id)
            used_hw = encoder
        cmd += ["-map", "0:V:0", "-map", "0:a?"] if not target.mute else ["-map", "0:V:0"]
        cmd += [*video_args, "-vf", ",".join(_video_filters(target, pix_fmt))]
        cmd += ["-an"] if target.mute else list(preset.audio) + _audio_options(target, info, audio_encoder(preset))
        cmd += ["-sn", "-dn", "-map_chapters", "0", *preset.extra]
    else:
        video = target.video if target.video not in (None, "none") else None
        audio = target.audio if target.audio not in (None, "none") and not target.mute else None
        qid = quality_id or "balanced"
        if video and info.get("video"):
            cmd += ["-map", "0:V:0"]
        if audio and info.get("audio"):
            cmd += ["-map", "0:a?"]
        if video == "copy":
            cmd += ["-c:v", "copy"]
        elif video:
            cmd += [*_custom_video(video, qid), "-vf", ",".join(_video_filters(target, None))]
        else:
            cmd += ["-vn"]
        if audio == "copy":
            cmd += ["-c:a", "copy"]
        elif audio:
            cmd += _custom_audio(audio, qid) + _audio_options(target, info, audio)
        else:
            cmd += ["-an"]
        cmd += ["-sn", "-dn", "-strict", "-2"]
    cmd += ["-f", muxer or (preset.muxer if preset else target.container), str(dst)]
    return cmd, used_hw


def progress_seconds(values: dict[str, str]) -> float | None:
    for key in ("out_time_us", "out_time_ms"):
        raw = values.get(key)
        if raw and raw.lstrip("-").isdigit() and int(raw) >= 0:
            return int(raw) / 1_000_000
    return None


def safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = UNSAFE_CHARS.sub("_", base).strip(" .")
    if not base:
        base = "file"
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = stem[:NAME_LIMIT]
    return f"{stem}.{ext[:12]}" if ext else stem


def output_folder(raw: str) -> Path:
    if not raw.strip():
        raise JobError("folder_required")
    folder = Path(raw.strip()).expanduser()
    if not folder.is_absolute():
        raise JobError("folder_not_absolute")
    if folder.exists() and not folder.is_dir():
        raise JobError("folder_not_directory")
    existing = next((p for p in (folder, *folder.parents) if p.exists()), None)
    if existing is None or not os.access(existing, os.W_OK):
        raise JobError("folder_not_writable")
    return folder


def default_folder() -> Path:
    return Path.home() / "Downloads" / "SpotCheck"


def inbox() -> Path:
    return paths().home / "convert-inbox"


def in_inbox(path: Path) -> bool:
    try:
        return inbox().resolve() in path.resolve().parents
    except OSError:
        return False


def clean_inbox(keep: set[str] | None = None) -> None:
    folder = inbox()
    if not folder.is_dir():
        return
    keep = keep or set()
    for entry in folder.iterdir():
        if entry.is_dir() and not any(k.startswith(str(entry)) for k in keep):
            shutil.rmtree(entry, ignore_errors=True)

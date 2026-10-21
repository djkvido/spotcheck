from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import struct
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import fileops, tags
from .config import paths
from .errors import Cancelled

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0
PROCESS_TIMEOUT = 900
JOB_THREADS = ("spotcheck-worker", "spotcheck-job")
FFMPEG_EXE = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

FP_ITEMS_PER_SECOND = 11025 / 1365

SUBFRAME_SHIFTS = (0.0, 0.031, 0.062, 0.093)

CUTOFF_BANDS = tuple(range(11000, 21001, 500))
CUTOFF_MIN_DROP = 12.0
CUTOFF_EMPTY = 55.0

_cache: dict[str, Any] = {}
_active: set[subprocess.Popen] = set()
_stopped: set[subprocess.Popen] = set()
_active_lock = threading.Lock()


class ProcessStopped(Cancelled):
    pass


def _ffmpeg_candidates():
    yield os.environ.get("SPOTCHECK_FFMPEG")
    yield str(paths().tools / "ffmpeg" / "bin" / FFMPEG_EXE)
    yield str(paths().tools / "ffmpeg" / FFMPEG_EXE)
    yield shutil.which("ffmpeg")
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if os.name == "nt" and packages.is_dir():
        yield from (str(p) for p in sorted(packages.glob(f"Gyan.FFmpeg_*/*/bin/{FFMPEG_EXE}"), reverse=True))
    yield str(Path.home() / ".spotdl" / FFMPEG_EXE)


def ffmpeg_path(refresh: bool = False) -> str | None:
    if refresh or "path" not in _cache:
        _cache["path"] = next((p for p in _ffmpeg_candidates() if p and Path(p).is_file()), None)
        _cache.pop("info", None)
    return _cache["path"]


def ffmpeg() -> str:
    return ffmpeg_path() or "ffmpeg"


def ffmpeg_info(refresh: bool = False) -> dict[str, Any]:
    if refresh or "info" not in _cache:
        path = ffmpeg_path(refresh)
        info: dict[str, Any] = {"found": bool(path), "path": path, "version": None, "chromaprint": False}
        if path:
            try:
                first = run_text([path, "-version"], timeout=15).stdout.splitlines()[:1]
                m = re.match(r"ffmpeg version (\S+)", first[0]) if first else None
                info["version"] = m.group(1) if m else None
                muxers = run_text([path, "-hide_banner", "-muxers"], timeout=15).stdout
                info["chromaprint"] = "chromaprint" in muxers.lower()
            except (OSError, subprocess.SubprocessError):
                pass
        _cache["info"] = info
    return _cache["info"]


def _spawn(cmd: list[str], cwd: str | None, text: bool, merge: bool, env: dict | None = None) -> subprocess.Popen:
    options: dict[str, Any] = {"text": True, "encoding": "utf-8", "errors": "replace"} if text else {}
    if os.name == "nt":
        options["creationflags"] = CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge else subprocess.PIPE,
        cwd=cwd,
        env=env,
        **options,
    )
    proc.job = threading.current_thread().name.startswith(JOB_THREADS)
    with _active_lock:
        _active.add(proc)
    return proc


def _release(proc: subprocess.Popen) -> bool:
    with _active_lock:
        _active.discard(proc)
        stopped = proc in _stopped
        _stopped.discard(proc)
    return stopped


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                timeout=15,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def stop_all(jobs_only: bool = False) -> int:
    with _active_lock:
        running = [p for p in _active if p.poll() is None and (p.job or not jobs_only)]
        _stopped.update(running)
    for proc in running:
        kill_tree(proc)
    return len(running)


def _run(cmd: list[str], timeout: float | None, cwd: str | None, text: bool) -> subprocess.CompletedProcess:
    proc = _spawn(cmd, cwd, text, merge=False)
    stopped = False
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=10)
            raise
    finally:
        stopped = _release(proc)
    if stopped:
        raise ProcessStopped(cmd[0])
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run_streaming(
    cmd: list[str], on_line: Callable[[str], None], timeout: float | None = None, cwd: str | None = None
) -> subprocess.CompletedProcess:
    proc = _spawn(cmd, cwd, text=True, merge=True, env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    expired = threading.Event()

    def stop() -> None:
        expired.set()
        kill_tree(proc)

    timer = threading.Timer(timeout, stop) if timeout else None
    if timer:
        timer.start()
    lines: list[str] = []
    try:
        for line in proc.stdout:
            lines.append(line)
            on_line(line)
        proc.wait()
    finally:
        if timer:
            timer.cancel()
        if proc.poll() is None:
            kill_tree(proc)
        stopped = _release(proc)
    if stopped:
        raise ProcessStopped(cmd[0])
    if expired.is_set():
        raise subprocess.TimeoutExpired(cmd, timeout or 0)
    return subprocess.CompletedProcess(cmd, proc.returncode, "".join(lines), "")


def run_text(
    cmd: list[str], timeout: float | None = PROCESS_TIMEOUT, cwd: str | None = None
) -> subprocess.CompletedProcess:
    return _run(cmd, timeout, cwd, text=True)


def run_bin(cmd: list[str], timeout: float | None = PROCESS_TIMEOUT) -> subprocess.CompletedProcess:
    return _run(cmd, timeout, None, text=False)


def duration(path: Path) -> float:
    try:
        import mutagen

        f = mutagen.File(str(path))
        if f is not None and f.info and f.info.length:
            return float(f.info.length)
    except Exception:
        pass
    try:
        r = run_text([ffmpeg(), "-hide_banner", "-i", str(path)], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def tag_text(path: Path) -> str:
    try:
        import mutagen

        f = mutagen.File(str(path))
        return " ".join(str(v) for v in f.tags.values()) if f is not None and f.tags else ""
    except Exception:
        return ""


def bitrate_kbps(path: Path) -> int | None:
    try:
        import mutagen

        f = mutagen.File(str(path))
        if f is None:
            return None
        bitrate = getattr(f.info, "bitrate", 0)
        if not bitrate and f.info.length:
            bitrate = path.stat().st_size * 8 / f.info.length
        return round(bitrate / 1000) if bitrate else None
    except Exception:
        return None


def codec_name(path: Path) -> str | None:
    try:
        import mutagen

        f = mutagen.File(str(path))
    except Exception:
        return None
    if f is None:
        return None
    names = {"MP3": "MP3", "MP4": "AAC", "OggOpus": "Opus", "OggVorbis": "Vorbis", "FLAC": "FLAC", "WAVE": "WAV"}
    return names.get(type(f).__name__, type(f).__name__)


def decode_check(path: Path) -> tuple[int, float]:
    r = run_text(
        [
            ffmpeg(),
            "-v",
            "error",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
            "-progress",
            "pipe:1",
        ]
    )
    errors = sum(1 for line in r.stderr.splitlines() if line.strip())
    times = re.findall(r"out_time_us=(\d+)", r.stdout)
    return errors, int(times[-1]) / 1_000_000 if times else 0.0


def cover_jpeg(path: Path) -> bytes:
    data = tags.cover(path)
    if data:
        return data
    cmd = [ffmpeg(), "-v", "error", "-i", str(path), "-an", "-map", "0:v:0", "-c:v", "copy", "-f", "image2pipe"]
    try:
        return run_bin([*cmd, "pipe:1"], timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return b""


def _silences(stderr: str) -> tuple[list[float], list[float]]:
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", stderr)]
    return starts, ends


def silence_edges(
    path: Path, length: float, head: float = 30, tail: float = 20, noise: str = "-50dB"
) -> tuple[float, float]:
    r = run_text(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostats",
            "-t",
            str(head),
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise}:d=0.3",
            "-f",
            "null",
            "-",
        ]
    )
    starts, ends = _silences(r.stderr)
    lead = (ends[0] if ends else float(head)) if starts and starts[0] <= 0.1 else 0.0

    window = min(float(tail), length) if length else float(tail)
    r = run_text(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostats",
            "-sseof",
            f"-{window}",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise}:d=0.3",
            "-f",
            "null",
            "-",
        ]
    )
    starts, ends = _silences(r.stderr)
    ended_before_eof = len(ends) >= len(starts) and bool(ends) and ends[-1] < window - 0.3
    trailing = max(0.0, window - max(starts[-1], 0.0)) if starts and not ended_before_eof else 0.0
    return lead, trailing


def band_levels(path: Path, length: float, seconds: float = 30) -> list[float] | None:
    start = max(0.0, min(length * 0.3, length - seconds)) if length else 0.0
    count = len(CUTOFF_BANDS) + 1
    graph = [
        "[0:a]aformat=sample_fmts=flt:sample_rates=44100:channel_layouts=mono,"
        f"asplit={count}" + "".join(f"[s{i}]" for i in range(count)),
        "[s0]astats=measure_perchannel=none:measure_overall=RMS_level[o0]",
    ]
    for i, freq in enumerate(CUTOFF_BANDS, 1):
        graph.append(
            f"[s{i}]firequalizer=gain='if(lt(f,{freq}),-120,0)':delay=0.1:wfunc=nuttall:fixed=on,"
            f"astats=measure_perchannel=none:measure_overall=RMS_level[o{i}]"
        )
    cmd = [ffmpeg(), "-hide_banner", "-nostats", "-ss", f"{start:.1f}", "-t", str(seconds)]
    cmd += ["-i", str(path), "-filter_complex", ";".join(graph)]
    for i in range(count):
        cmd += ["-map", f"[o{i}]", "-f", "null", "-"]
    try:
        r = run_text(cmd, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.findall(r"\[Parsed_astats_(\d+) @ [^\]]+\] RMS level dB: (\S+)", r.stderr)
    if len(found) != count:
        return None
    ordered = sorted((int(index), value) for index, value in found)
    return [float(v) if v not in ("-inf", "inf", "nan") else -200.0 for _, v in ordered]


def find_cutoff(levels: list[float], full: float) -> float | None:
    if levels[0] < full - CUTOFF_EMPTY:
        return CUTOFF_BANDS[0] / 1000
    drop, end = max((levels[i - 2] - levels[i], i) for i in range(2, len(levels)))
    if drop < CUTOFF_MIN_DROP:
        return None
    edge = end if levels[end - 1] - levels[end] >= levels[end - 2] - levels[end - 1] else end - 1
    return round((CUTOFF_BANDS[edge - 1] + CUTOFF_BANDS[edge]) / 2000, 2)


def cutoff_khz(path: Path, length: float) -> tuple[bool, float | None]:
    levels = band_levels(path, length)
    if levels is None:
        return False, None
    return True, find_cutoff(levels[1:], levels[0])


def head_volume(path: Path, seconds: float = 30) -> float | None:
    r = run_text(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostats",
            "-t",
            str(seconds),
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else None


def head_gap(path: Path, lead: float, window: float = 30) -> float | None:
    r = run_text(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostats",
            "-t",
            str(window),
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-45dB:d=0.4",
            "-f",
            "null",
            "-",
        ]
    )
    starts, _ = _silences(r.stderr)
    return next((s for s in starts if max(1.0, lead + 1.0) <= s <= window - 5), None)


def loudness(path: Path) -> dict[str, float | None] | None:
    cmd = [ffmpeg(), "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0"]
    cmd += ["-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]
    try:
        r = run_text(cmd)
    except (OSError, subprocess.SubprocessError):
        return None
    if "Summary:" not in r.stderr:
        return None
    summary = r.stderr.rsplit("Summary:", 1)[1]

    def value(pattern: str) -> float | None:
        m = re.search(pattern, summary, re.S)
        return float(m.group(1)) if m and m.group(1) not in ("-inf", "inf", "nan") else None

    integrated = value(r"I:\s+(-?[\d.]+|-inf) LUFS")
    if integrated is None:
        return None
    return {
        "lufs": integrated,
        "lra": value(r"LRA:\s+(-?[\d.]+) LU"),
        "peak": value(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS"),
    }


def raw_fingerprint(path: Path, offset: float = 0.0) -> tuple[int, ...]:
    cmd = [ffmpeg(), "-v", "error"]
    if offset > 0:
        cmd += ["-ss", f"{offset:.4f}"]
    cmd += ["-i", str(path), "-map", "0:a:0", "-f", "chromaprint", "-fp_format", "raw", "-"]
    data = run_bin(cmd).stdout
    count = len(data) // 4
    return struct.unpack(f"<{count}I", data[: count * 4])


def ber_curve(long_fp: tuple[int, ...], short_fp: tuple[int, ...]) -> list[float]:
    if len(short_fp) > len(long_fp):
        long_fp, short_fp = short_fp, long_fp
    width = len(short_fp)
    offsets = len(long_fp) - width + 1
    if width == 0 or offsets <= 0:
        return []
    packed = struct.pack(f"<{len(long_fp)}I", *long_fp)
    needle = int.from_bytes(struct.pack(f"<{width}I", *short_fp), "little")
    bits = 32.0 * width
    span = 4 * width
    return [
        (int.from_bytes(packed[4 * o : 4 * o + span], "little") ^ needle).bit_count() / bits for o in range(offsets)
    ]


def best_alignment(
    path: Path, preview_fp: tuple[int, ...], shifts: tuple[float, ...] = SUBFRAME_SHIFTS
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for shift in shifts:
        fp = raw_fingerprint(path, shift)
        curve = ber_curve(fp, preview_fp)
        if not curve:
            continue
        ber = min(curve)
        if best is None or ber < best["ber"]:
            best = {
                "ber": ber,
                "at": curve.index(ber) / FP_ITEMS_PER_SECOND + shift,
                "curve": curve,
                "file_len": len(fp) / FP_ITEMS_PER_SECOND + shift,
                "window": len(preview_fp) / FP_ITEMS_PER_SECOND,
            }
        if ber <= 0.02:
            break
    return best


def downsample_min(curve: list[float], points: int = 160) -> list[float]:
    if len(curve) <= points:
        return [round(x, 3) for x in curve]
    step = len(curve) / points
    return [round(min(curve[int(i * step) : max(int((i + 1) * step), int(i * step) + 1)]), 3) for i in range(points)]


def trim(src: Path, start: float, length: float) -> bool:
    tmp = fileops.temp_path(src, "trim")
    is_mp3 = src.suffix.lower() == ".mp3"
    cmd = [ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}"]
    cmd += ["-map", "0" if is_mp3 else "0:a", "-c", "copy", "-map_metadata", "0"]
    if is_mp3:
        cmd += ["-id3v2_version", "3"]
    r = run_text([*cmd, str(tmp)])
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 10_000:
        tmp.unlink(missing_ok=True)
        return False
    if not is_mp3:
        with contextlib.suppress(Exception):
            tags.copy(src, tmp)
    try:
        fileops.replace(tmp, src)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return True


def segment_mp3(path: Path, start: float, length: float) -> bytes:
    r = run_bin(
        [
            ffmpeg(),
            "-v",
            "error",
            "-ss",
            f"{max(start, 0):.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(path),
            "-vn",
            "-map",
            "0:a:0",
            "-f",
            "mp3",
            "-b:a",
            "160k",
            "pipe:1",
        ]
    )
    return r.stdout

from __future__ import annotations

import importlib.metadata as metadata
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import analysis, runtime

LogFn = Callable[..., None]

YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]
PYTHON_PACKAGES = ("spotdl", "yt-dlp", "mutagen", "rapidfuzz")
UPDATE_SPECS = ("yt-dlp[default]", "mutagen", "rapidfuzz")
PACKAGED_UPDATES = ("yt-dlp", "yt-dlp-ejs")
REQUIRED = ("spotdl", "yt-dlp", "mutagen", "rapidfuzz", "ffmpeg")

LINKS = [
    {"id": "spotdl", "name": "spotDL", "url": "https://github.com/spotDL/spotify-downloader"},
    {"id": "spotdl_docs", "name": "spotDL docs", "url": "https://spotdl.readthedocs.io"},
    {"id": "ytdlp", "name": "yt-dlp", "url": "https://github.com/yt-dlp/yt-dlp"},
    {"id": "ffmpeg", "name": "FFmpeg", "url": "https://ffmpeg.org/download.html"},
    {"id": "ffmpeg_windows", "name": "FFmpeg for Windows", "url": "https://www.gyan.dev/ffmpeg/builds/"},
    {"id": "chromaprint", "name": "Chromaprint", "url": "https://acoustid.org/chromaprint"},
    {"id": "deezer", "name": "Deezer API", "url": "https://developers.deezer.com/api"},
    {
        "id": "itunes",
        "name": "iTunes Search API",
        "url": "https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/",
    },
    {"id": "deno", "name": "Deno", "url": "https://deno.com"},
]

_latest: dict[str, tuple[str | None, float]] = {}
_latest_lock = threading.Lock()


def _installed_version(package: str) -> str | None:
    if package == "spotdl":
        found = next(iter(metadata.distributions(name="spotdl", path=[str(runtime.spotdl_dir())])), None)
        return found.version if found else None
    if runtime.packaged():
        found = next(iter(metadata.distributions(name=package, path=[str(runtime.packages_dir())])), None)
        if found:
            return found.version
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _pypi_latest(package: str) -> str | None:
    with _latest_lock:
        cached = _latest.get(package)
        if cached and time.time() - cached[1] < 3600:
            return cached[0]
    try:
        req = urllib.request.Request(f"https://pypi.org/pypi/{package}/json", headers={"User-Agent": "SpotCheck"})
        with urllib.request.urlopen(req, timeout=6) as r:
            version = json.loads(r.read().decode())["info"]["version"]
    except (OSError, ValueError, KeyError):
        version = None
    with _latest_lock:
        _latest[package] = (version, time.time())
    return version


def deno_path() -> str | None:
    exe = "deno.exe" if os.name == "nt" else "deno"
    for candidate in (shutil.which("deno"), str(Path.home() / ".spotdl" / exe)):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _deno_version(path: str) -> str | None:
    try:
        out = analysis.run_text([path, "--version"], timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"deno (\S+)", out)
    return m.group(1) if m else None


def _item(key: str, name: str, installed: bool, version: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "installed": installed,
        "version": version,
        "latest": None,
        "update": False,
        **extra,
    }


def status(check_updates: bool = True) -> dict[str, Any]:
    ff = analysis.ffmpeg_info(refresh=True)
    items: list[dict[str, Any]] = []
    for package in PYTHON_PACKAGES:
        current = _installed_version(package)
        updatable = package != "spotdl" and (not runtime.packaged() or package in PACKAGED_UPDATES)
        latest = _pypi_latest(package) if check_updates and current and updatable else None
        item = _item(package, "spotDL" if package == "spotdl" else package, current is not None, current)
        item.update(latest=latest, update=bool(current and latest and current != latest))
        items.append(item)
    items.append(_item("ffmpeg", "FFmpeg", ff["found"], ff["version"], path=ff["path"]))
    items.append(_item("chromaprint", "Chromaprint", ff["chromaprint"], None))
    deno = deno_path()
    items.append(_item("deno", "Deno", bool(deno), _deno_version(deno) if deno else None, path=deno))
    ready = all(i["installed"] for i in items if i["key"] in REQUIRED)
    return {
        "items": items,
        "links": LINKS,
        "ready": ready,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "packaged": runtime.packaged(),
    }


def _run(cmd: list[str], log: LogFn, timeout: float = 1800) -> bool:
    log("info", "command", text=" ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=analysis.CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.strip():
                log("info", "output", text=line.rstrip()[:300])
        return proc.wait(timeout=timeout) == 0
    except (OSError, subprocess.SubprocessError) as exc:
        log("error", "command_failed", error=str(exc))
        return False


def ensure_spotdl(log: LogFn) -> bool:
    if _installed_version("spotdl") == runtime.SPOTDL_REQUIREMENT.split("==")[1]:
        return True
    if any(True for _ in metadata.distributions(name="spotdl")):
        _run([sys.executable, "-m", "pip", "uninstall", "--yes", "--quiet", "spotdl"], log)
    target = str(runtime.spotdl_dir())
    pip = [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--upgrade", "--target", target]
    return _run([*pip, runtime.SPOTDL_REQUIREMENT], log)


def install(action: str, log: LogFn) -> bool:
    ok = True
    if action in ("update-python", "all"):
        if runtime.packaged():
            target = str(runtime.packages_dir())
            pip = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-deps", "--target", target]
            ok &= _run([*pip, *PACKAGED_UPDATES], log)
        else:
            ok &= _run([sys.executable, "-m", "pip", "install", "-U", *UPDATE_SPECS], log)
            ok &= ensure_spotdl(log)
    if runtime.packaged() and action in ("ffmpeg", "deno"):
        return ok
    if action in ("ffmpeg", "all"):
        info = analysis.ffmpeg_info(refresh=True)
        if info["found"] and info["chromaprint"]:
            log("info", "ffmpeg_ready")
        elif sys.platform == "win32" and shutil.which("winget"):
            winget = [
                "winget",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ]
            ok &= _run(winget, log)
            if not analysis.ffmpeg_info(refresh=True)["found"]:
                log("warn", "ffmpeg_not_on_path")
        else:
            ok &= _run([sys.executable, "-m", "spotdl", "--download-ffmpeg"], log)
            if not analysis.ffmpeg_info(refresh=True)["chromaprint"]:
                log("warn", "ffmpeg_no_chromaprint")
    if action in ("deno", "all"):
        ok &= _run([sys.executable, "-m", "spotdl", "--download-deno"], log)
    return ok

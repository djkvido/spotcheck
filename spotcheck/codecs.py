from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import analysis
from .config import paths
from .errors import Cancelled, JobError

CHUNK = 1 << 20
DOWNLOAD_TIMEOUT = 60
EXE = ".exe" if os.name == "nt" else ""
TOOLS = ("ffmpeg", "ffprobe")

BUILDS: dict[str, dict[str, Any]] = {
    "win32": {
        "version": "9.0.2",
        "provider": "gyan.dev",
        "home": "https://www.gyan.dev/ffmpeg/builds/",
        "license": "GPL-3.0-or-later",
        "archives": [
            {
                "url": "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-full_build-shared.zip",
                "sha256": "8d31e162f1616e37aab3fa2db991b97e1b8dbeb1c8465fd81a81be16ff91b328",
                "size": 99899672,
            }
        ],
        "skip": ("ffplay.exe",),
    },
    "darwin": {
        "version": "9.0.2",
        "provider": "martin-riedl.de",
        "home": "https://ffmpeg.martin-riedl.de/",
        "license": "GPL-3.0-or-later",
        "archives": [
            {
                "url": "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip",
                "sha256": "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
                "size": 28395699,
            },
            {
                "url": "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffprobe.zip",
                "sha256": "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
                "size": 28317701,
            },
        ],
        "skip": (),
    },
}

Progress = Callable[[int, int], None]
_install_lock = threading.Lock()


def build() -> dict[str, Any] | None:
    if sys.platform == "darwin" and os.uname().machine != "arm64":
        return None
    return BUILDS.get(sys.platform)


def root() -> Path:
    return paths().home / "codecs"


def target_dir(spec: dict[str, Any]) -> Path:
    return root() / f"ffmpeg-{spec['version']}"


def _marker(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def installed() -> dict[str, Any] | None:
    spec = build()
    if spec is None:
        return None
    folder = target_dir(spec)
    marker = _marker(folder)
    if not marker or marker.get("version") != spec["version"]:
        return None
    if not all((folder / "bin" / f"{name}{EXE}").is_file() for name in TOOLS):
        return None
    return {**marker, "folder": str(folder)}


def ffmpeg_path() -> str | None:
    found = installed()
    return str(Path(found["folder"]) / "bin" / f"ffmpeg{EXE}") if found else None


def status() -> dict[str, Any]:
    spec = build()
    found = installed()
    return {
        "available": spec is not None,
        "installed": found is not None,
        "version": spec["version"] if spec else None,
        "provider": spec["provider"] if spec else None,
        "home": spec["home"] if spec else None,
        "license": spec["license"] if spec else None,
        "size": sum(a["size"] for a in spec["archives"]) if spec else 0,
        "folder": found["folder"] if found else None,
    }


def _download(archive: dict[str, Any], dest: Path, progress: Progress, done: int, total: int, cancelled) -> None:
    req = urllib.request.Request(archive["url"], headers={"User-Agent": "SpotCheck"})
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response, open(dest, "wb") as out:
        if not response.geturl().lower().startswith("https://"):
            raise JobError("codecs_insecure")
        while True:
            if cancelled():
                raise Cancelled
            chunk = response.read(CHUNK)
            if not chunk:
                break
            received += len(chunk)
            if received > archive["size"]:
                raise JobError("codecs_checksum")
            digest.update(chunk)
            out.write(chunk)
            progress(done + received, total)
    if received != archive["size"] or digest.hexdigest() != archive["sha256"]:
        raise JobError("codecs_checksum")


def _extract(archive: Path, bin_dir: Path, docs: Path, skip: tuple[str, ...]) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = Path(info.filename).name
            if info.is_dir() or not name or name in skip:
                continue
            parts = Path(info.filename).parts
            if "bin" in parts or len(parts) == 1:
                target = bin_dir / name
            elif name.upper().startswith(("LICENSE", "README")):
                target = docs / name
            else:
                continue
            with bundle.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            if os.name != "nt":
                target.chmod(0o755)


def _verify(bin_dir: Path, version: str) -> None:
    try:
        out = analysis.run_text([str(bin_dir / f"ffmpeg{EXE}"), "-hide_banner", "-version"], timeout=30)
        probe = analysis.run_text([str(bin_dir / f"ffprobe{EXE}"), "-hide_banner", "-version"], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise JobError("codecs_broken") from exc
    if out.returncode != 0 or probe.returncode != 0 or f"version {version}" not in out.stdout:
        raise JobError("codecs_broken")


def install(progress: Progress, cancelled: Callable[[], bool]) -> dict[str, Any]:
    spec = build()
    if spec is None:
        raise JobError("codecs_unsupported")
    if not _install_lock.acquire(blocking=False):
        raise JobError("codecs_busy")
    try:
        base = root()
        base.mkdir(parents=True, exist_ok=True)
        total = sum(a["size"] for a in spec["archives"])
        if shutil.disk_usage(base).free < total * 4:
            raise JobError("codecs_no_space")
        staging = base / f".staging-{uuid.uuid4().hex[:8]}"
        bin_dir, docs = staging / "bin", staging / "docs"
        bin_dir.mkdir(parents=True)
        docs.mkdir()
        try:
            done = 0
            for n, archive in enumerate(spec["archives"]):
                file = staging / f"download-{n}.zip"
                _download(archive, file, progress, done, total, cancelled)
                done += archive["size"]
                _extract(file, bin_dir, docs, spec["skip"])
                file.unlink()
            _verify(bin_dir, spec["version"])
            (docs / "SOURCE.txt").write_text(
                f"FFmpeg {spec['version']} build by {spec['provider']} ({spec['home']}).\n"
                f"License: {spec['license']}. Source code: https://ffmpeg.org/download.html\n",
                encoding="utf-8",
            )
            marker = {"version": spec["version"], "provider": spec["provider"], "installed": time.time()}
            (staging / "build.json").write_text(json.dumps(marker), encoding="utf-8")
            final = target_dir(spec)
            if final.exists():
                _set_aside(final)
            os.replace(staging, final)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        _remove_others(final)
        return status()
    finally:
        _install_lock.release()


def _set_aside(folder: Path) -> None:
    aside = folder.with_name(f".removed-{uuid.uuid4().hex[:8]}")
    try:
        os.replace(folder, aside)
    except OSError as exc:
        raise JobError("codecs_busy") from exc
    shutil.rmtree(aside, ignore_errors=True)


def _remove_others(keep: Path | None) -> None:
    base = root()
    if not base.is_dir():
        return
    for folder in base.iterdir():
        if folder.is_dir() and folder != keep and folder.name.startswith(("ffmpeg-", ".staging-", ".removed-")):
            shutil.rmtree(folder, ignore_errors=True)


def remove() -> bool:
    if _install_lock.locked():
        raise JobError("codecs_busy")
    spec = build()
    found = installed()
    if spec is not None and target_dir(spec).exists():
        _set_aside(target_dir(spec))
    _remove_others(None)
    return found is not None

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import logging
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__, deps, runtime
from .config import paths
from .errors import JobError

log = logging.getLogger(__name__)

REPO = "djkvido/spotcheck"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
SUMS = "SHA256SUMS.txt"
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
CHECK_EVERY = 12 * 3600
CHECK_CACHE = 600
NOTES_LIMIT = 8000
MAX_SIZE = 800_000_000
CHUNK = 1 << 16
EMIT_INTERVAL = 0.3
TIMEOUT = 30


def suffix() -> str | None:
    if runtime.WINDOWS:
        return "-windows-x64-setup.exe"
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "-macos-arm64.dmg"
    return None


def folder() -> Path:
    return paths().home / "updates"


def _open(url: str, timeout: float = TIMEOUT):
    headers = {"User-Agent": f"SpotCheck/{__version__}", "Accept": "application/vnd.github+json"}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def _notes(body: str) -> str:
    cut = body.find("\n## Download")
    text = body[:cut] if cut >= 0 else body
    return text.strip()[:NOTES_LIMIT]


def parse_release(data: dict[str, Any], current: str = __version__) -> dict[str, Any] | None:
    tag = str(data.get("tag_name") or "").removeprefix("v")
    if data.get("draft") or data.get("prerelease") or not VERSION.match(tag):
        return None
    if deps.version_key(tag) <= deps.version_key(current):
        return None
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    wanted = suffix()
    installer = assets.get(f"SpotCheck-{tag}{wanted}") if wanted else None
    sums = assets.get(SUMS)
    asset = None
    if installer and sums:
        link, sums_link = installer.get("browser_download_url"), sums.get("browser_download_url")
        size = installer.get("size")
        if (
            link == f"{DOWNLOADS}v{tag}/{installer['name']}"
            and sums_link == f"{DOWNLOADS}v{tag}/{SUMS}"
            and isinstance(size, int)
            and 0 < size <= MAX_SIZE
        ):
            asset = {"name": installer["name"], "url": link, "size": size, "sums": sums_link}
    page = data.get("html_url")
    return {
        "version": tag,
        "current": current,
        "notes": _notes(str(data.get("body") or "")),
        "page": page if isinstance(page, str) and page.startswith(f"https://github.com/{REPO}/") else RELEASES,
        "published": data.get("published_at"),
        "asset": asset,
    }


def expected_hash(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def file_hash(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


class Updater:
    def __init__(self, bus) -> None:
        self.bus = bus
        self.lock = threading.Lock()
        self.release: dict[str, Any] | None = None
        self.checked = 0.0
        self.phase = "idle"
        self.progress = 0.0
        self.error: str | None = None
        self.file: Path | None = None
        self.digest: str | None = None
        self._emitted = 0.0
        self._stop = threading.Event()

    def public(self) -> dict[str, Any] | None:
        with self.lock:
            if not self.release:
                return None
            release = self.release
            asset = release["asset"]
            return {
                "version": release["version"],
                "current": release["current"],
                "notes": release["notes"],
                "page": release["page"],
                "size": asset["size"] if asset else None,
                "installable": bool(asset) and runtime.packaged(),
                "platform": "windows" if runtime.WINDOWS else "macos" if sys.platform == "darwin" else "other",
                "packaged": runtime.packaged(),
                "phase": self.phase,
                "progress": round(self.progress, 4),
                "error": self.error,
            }

    def _emit(self, force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self._emitted < EMIT_INTERVAL:
            return
        self._emitted = now
        self.bus.emit({"type": "app_update", "update": self.public()})

    def check(self, force: bool = False) -> dict[str, Any] | None:
        with self.lock:
            fresh = time.monotonic() - self.checked < CHECK_CACHE if self.checked else False
            busy = self.phase in ("downloading", "verifying", "ready", "installing")
        if (fresh and not force) or busy:
            return self.public()
        try:
            with _open(API) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8"))
            release = parse_release(data) if isinstance(data, dict) else None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            log.info("Update check failed: %s", exc)
            return self.public()
        with self.lock:
            self.checked = time.monotonic()
            working = self.phase in ("downloading", "verifying", "ready", "installing")
            changed = not working and (self.release or {}).get("version") != (release or {}).get("version")
            if not working:
                self.release = release
            if changed:
                self.phase, self.progress, self.error, self.file = "idle", 0.0, None, None
        if working:
            return self.public()
        self.clean()
        if changed:
            self._emit()
        return self.public()

    def watch(self) -> None:
        while not self._stop.is_set():
            try:
                self.check(force=True)
            except Exception:
                log.exception("Update check failed")
            if self._stop.wait(CHECK_EVERY):
                return

    def start_watch(self) -> None:
        threading.Thread(target=self.watch, name="spotcheck-app-update", daemon=True).start()

    def clean(self) -> None:
        keep = (self.release or {}).get("asset") or {}
        base = folder()
        if not base.is_dir():
            return
        for path in base.iterdir():
            if path.name not in (keep.get("name"), f"{keep.get('name')}.part"):
                with contextlib.suppress(OSError):
                    path.unlink()

    def download(self) -> dict[str, Any] | None:
        with self.lock:
            release = self.release
            if not release or not release["asset"] or not runtime.packaged():
                raise JobError("update_unavailable")
            if self.phase in ("downloading", "verifying", "installing"):
                return None
            if self.phase == "ready" and self.file and self.file.is_file():
                return None
            self.phase, self.progress, self.error = "downloading", 0.0, None
        self._emit()
        worker = threading.Thread(target=self._fetch, args=(release["asset"],), name="spotcheck-app-download")
        worker.daemon = True
        worker.start()
        return self.public()

    def _fail(self, code: str) -> None:
        with self.lock:
            self.phase, self.error = "failed", code
        self._emit()

    def _fetch(self, asset: dict[str, Any]) -> None:
        base = folder()
        target = base / asset["name"]
        part = base / f"{asset['name']}.part"
        digest = hashlib.sha256()
        try:
            base.mkdir(parents=True, exist_ok=True)
            with _open(asset["sums"]) as response:
                expected = expected_hash(response.read(200_000).decode("utf-8", "replace"), asset["name"])
            if not expected:
                self._fail("update_checksum")
                return
            done = 0
            with _open(asset["url"], timeout=60) as response, part.open("wb") as out:
                if not response.geturl().startswith("https://"):
                    raise OSError("insecure redirect")
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > asset["size"]:
                        raise OSError("larger than announced")
                    digest.update(chunk)
                    out.write(chunk)
                    with self.lock:
                        self.progress = done / asset["size"]
                    self._emit(force=False)
            with self.lock:
                self.phase = "verifying"
            self._emit()
            if done != asset["size"] or digest.hexdigest() != expected:
                part.unlink(missing_ok=True)
                self._fail("update_checksum")
                return
            part.replace(target)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            log.warning("Update download failed: %s", exc)
            part.unlink(missing_ok=True)
            self._fail("update_download_failed")
            return
        with self.lock:
            self.file, self.phase, self.progress, self.digest = target, "ready", 1.0, expected
        self._emit()

    def install(self, shutdown) -> str:
        with self.lock:
            if self.phase != "ready" or not self.file or not self.file.is_file():
                raise JobError("update_not_ready")
            path, digest = self.file, self.digest
        if file_hash(path) != digest:
            with contextlib.suppress(OSError):
                path.unlink()
            self._fail("update_checksum")
            raise JobError("update_checksum")
        try:
            if runtime.WINDOWS:
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(
                    [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"],
                    creationflags=flags,
                    close_fds=True,
                )
                action = "restart"
            else:
                subprocess.Popen(["open", str(path)], close_fds=True)
                action = "opened"
        except OSError as exc:
            log.warning("Update install failed: %s", exc)
            raise JobError("update_install_failed") from exc
        with self.lock:
            self.phase = "installing" if action == "restart" else "ready"
        self._emit()
        if action == "restart":
            shutdown()
        return action

    def close(self) -> None:
        self._stop.set()

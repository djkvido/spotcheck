from __future__ import annotations

import getpass
import platform as host
import re
import shutil
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import __version__, deps, runtime
from .config import paths

PROBES = (
    ("Spotify", "https://open.spotify.com/"),
    ("Spotify API", "https://accounts.spotify.com/api/token"),
    ("YouTube", "https://www.youtube.com/"),
    ("YouTube Music", "https://music.youtube.com/"),
    ("SoundCloud", "https://soundcloud.com/"),
    ("Bandcamp", "https://bandcamp.com/"),
    ("Deezer", "https://api.deezer.com/chart"),
    ("iTunes", "https://itunes.apple.com/search?term=a&limit=1"),
    ("GitHub", "https://github.com/"),
)
PROBE_TIMEOUT = 8.0
LOG_TAIL = 60


def sanitize(text: Any) -> str:
    out = str(text)
    home = str(Path.home())
    for variant in {home, home.replace("\\", "/")}:
        if len(variant) > 3:
            out = out.replace(variant, "~")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) > 2:
        out = re.sub(re.escape(user), "<user>", out, flags=re.IGNORECASE)
    return out


def short(text: Any, limit: int = 300) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    joined = sanitize(" | ".join(lines[-4:]))
    return joined if len(joined) <= limit else "..." + joined[-limit:]


def format_event(event: dict, stamp: bool = False) -> str:
    params = " ".join(f"{key}={short(value, 200)}" for key, value in (event.get("params") or {}).items())
    track = f" track={event['track']}" if event.get("track") else ""
    head = time.strftime("%H:%M:%S ", time.localtime(event["t"])) if stamp and event.get("t") else ""
    return f"{head}{event.get('level', 'info')} {event.get('code', '')}{track} {params}".rstrip()


def probe(item: tuple[str, str]) -> str:
    name, url = item
    start = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": f"SpotCheck/{__version__} diagnostics"})
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT) as response:
            status = f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        status = f"HTTP {exc.code}"
    except Exception as exc:
        status = f"FAILED {type(exc).__name__}: {short(exc, 120)}"
    elapsed = round((time.perf_counter() - start) * 1000)
    return f"  {name:<14} {status} ({elapsed} ms)"


def free_space(folder: Path) -> str:
    probe_dir = folder
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    try:
        return f"{shutil.disk_usage(probe_dir).free / 1024**3:.1f} GB free"
    except OSError:
        return "free space unknown"


def report(network: bool = False, events: list[str] | None = None) -> str:
    state = deps.status(check_updates=False)
    lines = [
        f"SpotCheck {__version__}",
        time.strftime("Report time: %Y-%m-%d %H:%M:%S %z"),
        f"System: {host.platform()} ({host.machine()})",
        f"Python: {state['python']}  platform: {state['platform']}  packaged: {state['packaged']}",
    ]
    try:
        workspace = paths()
        lines.append(f"Music folder: {sanitize(workspace.downloads)} ({free_space(workspace.downloads)})")
        lines.append(f"Data folder: {sanitize(workspace.home)}")
    except Exception as exc:
        lines.append(f"Folders: unavailable ({short(exc)})")
    log_file = runtime.log_path()
    lines.append(f"Log file: {sanitize(log_file) if log_file else 'not written in this mode'}")
    lines.append("")
    lines.append("Components:")
    for item in state["items"]:
        mark = "ok" if item["installed"] else "MISSING"
        extra = " ".join(part for part in (str(item.get("version") or ""), sanitize(item.get("path") or "")) if part)
        lines.append(f"  {item['name']:<14} {mark:<8} {extra}".rstrip())
    missing = [item["name"] for item in state["items"] if not item["installed"] and item["key"] in deps.REQUIRED]
    lines.append(f"Ready: {'yes' if state['ready'] else 'NO, missing: ' + ', '.join(missing)}")
    if network:
        lines.append("")
        lines.append("Network:")
        with ThreadPoolExecutor(max_workers=len(PROBES)) as pool:
            lines.extend(pool.map(probe, PROBES))
    if events:
        lines.append("")
        lines.append("Recent events:")
        lines.extend(f"  {line}" for line in events)
    tail = runtime.log_tail(LOG_TAIL)
    if tail:
        lines.append("")
        lines.append(f"Log file, last {len(tail)} lines:")
        lines.extend(f"  {sanitize(line)}" for line in tail)
    return "\n".join(lines) + "\n"

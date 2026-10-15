from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "SpotCheck"
SPOTDL_REQUIREMENT = "spotdl==4.5.2"

_packaged = False
_packages: Path | None = None


def packaged() -> bool:
    return _packaged


def spotdl_dir() -> Path:
    return Path(sys.prefix) / "spotdl"


def user_home() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def user_music() -> Path:
    return Path.home() / "Music" / APP_NAME


def packages_dir() -> Path:
    return _packages or user_home() / "packages"


def bundled_tools() -> Path | None:
    executable = Path(sys.executable).resolve()
    for parent in executable.parents[:3]:
        if (parent / "tools").is_dir():
            return parent / "tools"
    return None


def _prepend_env(name: str, entries: list[Path]) -> None:
    current = [p for p in os.environ.get(name, "").split(os.pathsep) if p]
    os.environ[name] = os.pathsep.join([str(e) for e in entries if str(e) not in current] + current)


def prepare(packaged_mode: bool, home: Path | None = None) -> None:
    global _packaged, _packages
    _packaged = packaged_mode
    _packages = home / "packages" if packaged_mode and home else None
    paths = [spotdl_dir()]
    if packaged_mode:
        packages = packages_dir()
        packages.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(packages))
        paths.insert(0, packages)
        tools = bundled_tools()
        if tools is not None:
            _prepend_env("PATH", [tools])
    _prepend_env("PYTHONPATH", paths)


def log_to_file(home: Path) -> None:
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "spotcheck.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

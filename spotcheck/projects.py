from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import fileops
from .config import first_project_music, paths, use_project

NAME_MAX = 60
DEFAULT_NAME = "My library"

_lock = threading.RLock()


class ProjectError(Exception):
    def __init__(self, field: str, code: str, **params: Any) -> None:
        super().__init__(code)
        self.field, self.code, self.params = field, code, params


def _file() -> Path:
    return paths().projects_file


def _save(registry: dict[str, Any]) -> None:
    fileops.write_json(_file(), registry, keep_previous=True)


def _valid(registry: Any) -> bool:
    return (
        isinstance(registry, dict)
        and isinstance(registry.get("projects"), list)
        and bool(registry["projects"])
        and all(isinstance(p, dict) and {"id", "name", "music", "data"} <= p.keys() for p in registry["projects"])
    )


def _move_legacy_caches(home: Path) -> None:
    for name in ("previews", "candidates"):
        old, new = home / "data" / name, home / "cache" / name
        if old.is_dir() and not (new.exists() and any(new.iterdir())):
            new.parent.mkdir(parents=True, exist_ok=True)
            if new.exists():
                new.rmdir()
            shutil.move(str(old), str(new))


def load() -> dict[str, Any]:
    with _lock:
        file = _file()
        registry, used = fileops.read_json(file, _valid)
        if used != file and file.exists():
            fileops.quarantine(file)
        if registry is not None:
            if used != file:
                _save(registry)
            return registry
        home = paths().home
        _move_legacy_caches(home)
        project = {
            "id": "default",
            "name": DEFAULT_NAME,
            "music": str(first_project_music()),
            "data": str(home / "data"),
            "created": time.time(),
        }
        registry = {"current": project["id"], "projects": [project]}
        _save(registry)
        return registry


def get(project_id: str) -> dict[str, Any] | None:
    return next((p for p in load()["projects"] if p["id"] == project_id), None)


def current() -> dict[str, Any]:
    registry = load()
    return next((p for p in registry["projects"] if p["id"] == registry["current"]), registry["projects"][0])


def activate(project: dict[str, Any]) -> None:
    with _lock:
        use_project(Path(project["music"]), Path(project["data"]))
        registry = load()
        if registry["current"] != project["id"]:
            registry["current"] = project["id"]
            _save(registry)


def _clean_name(name: str, exclude_id: str | None = None) -> str:
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name:
        raise ProjectError("name", "required")
    if len(name) > NAME_MAX:
        raise ProjectError("name", "too_long", max=NAME_MAX)
    for p in load()["projects"]:
        if p["id"] != exclude_id and p["name"].casefold() == name.casefold():
            raise ProjectError("name", "name_taken")
    return name


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _clean_music(value: str, exclude_id: str | None = None) -> Path:
    if not value or not value.strip():
        raise ProjectError("music", "required")
    folder = Path(value.strip()).expanduser()
    if not folder.is_absolute():
        raise ProjectError("music", "folder_not_absolute")
    folder = folder.resolve()
    if folder.exists() and not folder.is_dir():
        raise ProjectError("music", "folder_not_directory")
    home = paths().home
    reserved = [home / "cache", home / "projects", home / "data", home / "tools"]
    if any(folder == r or r in folder.parents for r in reserved) or folder in home.parents or folder == home:
        raise ProjectError("music", "folder_reserved")
    for p in load()["projects"]:
        if p["id"] == exclude_id:
            continue
        if _overlaps(folder, Path(p["music"]).resolve()):
            raise ProjectError("music", "folder_in_use", name=p["name"])
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".spotcheck-write-test-{uuid.uuid4().hex[:6]}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise ProjectError("music", "folder_not_writable") from exc
    return folder


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "project"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def default_music(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*]', "", name or "").strip() or "SpotCheck"
    return str(Path.home() / "Music" / "SpotCheck" / clean)


def create(name: str, music: str) -> dict[str, Any]:
    with _lock:
        name = _clean_name(name)
        folder = _clean_music(music)
        project_id = _slug(name)
        project = {
            "id": project_id,
            "name": name,
            "music": str(folder),
            "data": str(paths().home / "projects" / project_id),
            "created": time.time(),
        }
        registry = load()
        registry["projects"].append(project)
        _save(registry)
        return project


def update(project_id: str, name: str | None = None, music: str | None = None) -> dict[str, Any]:
    with _lock:
        registry = load()
        project = next((p for p in registry["projects"] if p["id"] == project_id), None)
        if project is None:
            raise ProjectError("", "project_not_found")
        if name is not None:
            project["name"] = _clean_name(name, exclude_id=project_id)
        if music is not None:
            project["music"] = str(_clean_music(music, exclude_id=project_id))
        _save(registry)
        return project


def remove(project_id: str) -> None:
    with _lock:
        registry = load()
        if registry["current"] == project_id:
            raise ProjectError("", "project_active")
        registry["projects"] = [p for p in registry["projects"] if p["id"] != project_id]
        _save(registry)


def summary(project: dict[str, Any]) -> dict[str, Any]:
    playlists = tracks = 0
    try:
        data = json.loads((Path(project["data"]) / "library.json").read_text(encoding="utf-8"))
        playlists = len(data.get("playlists", []))
        tracks = sum(len(p.get("tracks", [])) for p in data.get("playlists", []))
    except (OSError, ValueError):
        pass
    return {**project, "playlists": playlists, "tracks": tracks}

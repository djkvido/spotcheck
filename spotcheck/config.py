from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"


@dataclass(frozen=True)
class Paths:
    home: Path
    music: Path
    project_data: Path

    @property
    def downloads(self) -> Path:
        return self.music

    @property
    def data(self) -> Path:
        return self.project_data

    @property
    def cache(self) -> Path:
        return self.home / "cache"

    @property
    def previews(self) -> Path:
        return self.cache / "previews"

    @property
    def candidates(self) -> Path:
        return self.cache / "candidates"

    @property
    def tmp(self) -> Path:
        return self.data / "tmp"

    @property
    def backups(self) -> Path:
        return self.data / "backups"

    @property
    def covers(self) -> Path:
        return self.data / "covers"

    @property
    def library_file(self) -> Path:
        return self.data / "library.json"

    @property
    def projects_file(self) -> Path:
        return self.home / "projects.json"

    @property
    def tools(self) -> Path:
        return self.home / "tools"

    def ensure(self) -> None:
        for d in (self.downloads, self.data, self.previews, self.candidates, self.tmp, self.backups, self.covers):
            d.mkdir(parents=True, exist_ok=True)


_paths: Paths | None = None
_first_music: Path | None = None


def configure(home: str | os.PathLike[str] | None = None, first_music: Path | None = None) -> Paths:
    global _first_music
    root = Path(home or os.environ.get("SPOTCHECK_HOME") or Path.cwd()).expanduser().resolve()
    _first_music = first_music
    return use_project(first_music or root / "downloads", root / "data", home=root)


def first_project_music() -> Path:
    return _first_music or paths().home / "downloads"


def use_project(music: Path, data: Path, home: Path | None = None) -> Paths:
    global _paths
    root = home or paths().home
    _paths = Paths(root, Path(music), Path(data))
    _paths.ensure()
    return _paths


def paths() -> Paths:
    return _paths if _paths is not None else configure()

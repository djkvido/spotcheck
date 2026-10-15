from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from . import analysis, fileops, origins
from . import settings as settings_mod
from .config import paths
from .sources import file_source, norm

log = logging.getLogger(__name__)

AUDIO_SUFFIXES = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}
NUMBER_PREFIX = re.compile(r"^\d{3,4} - ")

TRACK_DEFAULTS: dict[str, Any] = {
    "file": None,
    "source": None,
    "check": None,
    "verify": None,
    "status": "missing",
    "notes": [],
    "candidates": [],
    "updated": None,
    "step": None,
    "trimmed": None,
    "proposal": None,
    "rejected_ids": [],
    "backups": [],
    "numbered": False,
}


def playlist_id(url: str) -> str:
    found = origins.classify(url)
    return found[1] if found else re.sub(r"\W+", "_", url)[:40]


def base_name(tr: dict, name: str) -> str:
    return NUMBER_PREFIX.sub("", name, count=1) if tr.get("numbered") else name


def folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", name or "playlist")
    return re.sub(r"\s+", " ", cleaned).strip().rstrip(". ") or "playlist"


def track_key(pl: dict, tr: dict) -> str:
    return f"{pl['id']}:{tr['id']}"


def compute_status(tr: dict) -> str:
    if not tr.get("file"):
        return "missing"
    check = (tr.get("check") or {}).get("verdict")
    verify = (tr.get("verify") or {}).get("verdict")
    if verify == "MISMATCH":
        return "bad"
    if check == "FAIL":
        return "review" if verify == "MATCH" else "bad"
    if verify == "UNSURE":
        return "review"
    if verify == "MATCH":
        return "ok"
    if verify == "NO_PREVIEW":
        return "partial" if check else "unchecked"
    return "unchecked"


class Library:
    def __init__(self, on_save: Callable[[], None] | None = None) -> None:
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {"settings": settings_mod.defaults(), "playlists": []}
        self.saved_at: float | None = None
        self.on_save = on_save
        self._load()

    def _load(self) -> None:
        file = paths().library_file
        if not file.exists():
            return
        try:
            loaded = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            backup = file.with_name(f"library.corrupt-{int(time.time())}.json")
            file.replace(backup)
            log.error("Library file could not be read (%s); moved to %s", exc, backup.name)
            return
        self.data["settings"] = settings_mod.merge(settings_mod.defaults(), loaded.get("settings", {}))
        self.data["playlists"] = loaded.get("playlists", [])
        for pl in self.data["playlists"]:
            if not pl.get("folder"):
                pl["folder"] = self._existing_folder(pl["name"]).name
            for tr in pl["tracks"]:
                for key, value in TRACK_DEFAULTS.items():
                    tr.setdefault(key, copy.deepcopy(value))
                tr["step"] = None

    def save(self) -> None:
        with self.lock:
            file = paths().library_file
            tmp = file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, file)
            self.saved_at = time.time()
        if self.on_save:
            self.on_save()

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.data["settings"] = settings_mod.merge(self.settings, changes)
            self.save()
            return self.settings

    def reset_settings(self) -> dict[str, Any]:
        with self.lock:
            self.data["settings"] = settings_mod.defaults()
            self.save()
            return self.settings

    def playlist(self, pid: str) -> dict | None:
        return next((p for p in self.data["playlists"] if p["id"] == pid), None)

    def find_track(self, key: str) -> tuple[dict | None, dict | None]:
        pid, _, tid = key.rpartition(":")
        for pl in self.data["playlists"]:
            if pid and pl["id"] != pid:
                continue
            for tr in pl["tracks"]:
                if tr["id"] == tid:
                    return pl, tr
        return None, None

    @staticmethod
    def _existing_folder(name: str) -> Path:
        want = folder_name(name)
        exact = paths().downloads / want
        if exact.exists():
            return exact
        for d in paths().downloads.iterdir():
            if d.is_dir() and norm(d.name) == norm(want):
                return d
        return exact

    @staticmethod
    def folder(pl: dict) -> Path:
        return paths().downloads / pl["folder"]

    @staticmethod
    def abs_file(tr: dict) -> Path | None:
        if not tr.get("file"):
            return None
        p = paths().downloads / tr["file"]
        return p if p.exists() else None

    @staticmethod
    def make_track(song: dict, list_name: str) -> dict:
        return {
            "id": song.get("song_id") or song["url"].rsplit("/", 1)[-1],
            "url": song["url"],
            "name": song["name"],
            "artist": song["artist"],
            "artists": song.get("artists") or [song["artist"]],
            "album": song.get("album_name", ""),
            "duration": song.get("duration", 0),
            "cover_url": song.get("cover_url"),
            "year": song.get("year"),
            "number": song.get("list_position"),
            "list_name": list_name,
            "origin": song.get("origin", "spotify"),
            "official": song.get("official"),
            "source_duration": song.get("source_duration"),
            "raw": song,
            **copy.deepcopy(TRACK_DEFAULTS),
        }

    def upsert_playlist(self, url: str, name: str, songs: list[dict]) -> dict[str, Any]:
        with self.lock:
            pid = playlist_id(url)
            pl = self.playlist(pid)
            tracks = []
            seen: set[str] = set()
            for song in songs:
                track = self.make_track(song, name)
                if track["id"] not in seen:
                    seen.add(track["id"])
                    tracks.append(track)
            if pl is None:
                folder = self._existing_folder(name).name
                pl = {
                    "id": pid,
                    "url": url,
                    "origin": (origins.classify(url) or ("spotify",))[0],
                    "name": name,
                    "folder": folder,
                    "tracks": tracks,
                    "added": time.time(),
                }
                self.data["playlists"].append(pl)
                change = {"playlist": pl, "new": True, "added": tracks, "removed": [], "old_name": name}
            else:
                previous = {t["id"]: t for t in pl["tracks"]}
                for t in tracks:
                    if t["id"] in previous:
                        t.update({k: previous[t["id"]].get(k, t[k]) for k in TRACK_DEFAULTS})
                added = [t for t in tracks if t["id"] not in previous]
                removed = [t for t in pl["tracks"] if t["id"] not in seen]
                change = {"playlist": pl, "new": False, "added": added, "removed": removed, "old_name": pl["name"]}
                pl["name"], pl["tracks"], pl["url"] = name, tracks, url
            pl["synced"] = time.time()
            self.save()
            return change

    def remove_playlist(self, pid: str) -> None:
        with self.lock:
            self.data["playlists"] = [p for p in self.data["playlists"] if p["id"] != pid]
            self.save()

    def find_file(self, pl: dict, tr: dict, exclude: set[str] | None = None) -> Path | None:
        folder = self.folder(pl)
        if not folder.is_dir():
            return None
        exclude = exclude or set()
        downloads = paths().downloads
        files = [
            p
            for p in folder.iterdir()
            if p.suffix.lower() in AUDIO_SUFFIXES
            and not fileops.is_temp(p)
            and p.relative_to(downloads).as_posix() not in exclude
        ]
        title, artist = norm(tr["name"]), norm(tr["artist"])
        full = norm(", ".join(tr["artists"]) + " - " + tr["name"])
        scored = []
        for f in files:
            name = norm(f.stem)
            score = max(fuzz.ratio(full, name), fuzz.ratio(full, norm(NUMBER_PREFIX.sub("", f.stem, count=1))))
            if title and artist and title in name and artist in name:
                score = max(score, 100)
            if score >= 85:
                scored.append((score, f))
        if not scored:
            return None
        top = max(s for s, _ in scored)
        ties = [f for s, f in scored if s >= top - 0.5]
        if len(ties) == 1:
            return ties[0]
        return min(ties, key=lambda f: abs(analysis.duration(f) - tr["duration"]))

    def link_files(self) -> None:
        downloads = paths().downloads
        with self.lock:
            for pl in self.data["playlists"]:
                taken: set[str] = set()
                for tr in pl["tracks"]:
                    if tr.get("file") and (downloads / tr["file"]).exists() and tr["file"] not in taken:
                        taken.add(tr["file"])
                    else:
                        tr["file"] = None
                for tr in pl["tracks"]:
                    if tr["file"] is None:
                        found = self.find_file(pl, tr, exclude=taken)
                        if found is not None:
                            tr["file"] = found.relative_to(downloads).as_posix()
                            taken.add(tr["file"])
                    if tr["file"] and not (tr.get("source") or {}).get("url"):
                        source = file_source(downloads / tr["file"])
                        if source:
                            via = "spotdl" if tr.get("origin", "spotify") == "spotify" else "origin"
                            tr["source"] = {**source, "via": via}
                    tr["status"] = compute_status(tr)
            self.save()

    @staticmethod
    def signature(path: Path) -> list[int]:
        st = path.stat()
        return [st.st_size, int(st.st_mtime)]

    def changed_tracks(self) -> list[str]:
        changed = []
        with self.lock:
            for pl in self.data["playlists"]:
                for tr in pl["tracks"]:
                    path = self.abs_file(tr)
                    if path is None:
                        continue
                    sig = self.signature(path)
                    check = tr.get("check")
                    if check is None or "cutoff_measured" not in check:
                        changed.append(track_key(pl, tr))
                    elif "sig" not in check:
                        check["sig"] = sig
                    elif check["sig"] != sig:
                        changed.append(track_key(pl, tr))
        return changed

    def due_for_sync(self, hours: float) -> list[str]:
        if hours <= 0:
            return []
        limit = time.time() - hours * 3600
        with self.lock:
            return [p["id"] for p in self.data["playlists"] if (p.get("synced") or p.get("added") or 0) < limit]

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "saved_at": self.saved_at,
                "settings": copy.deepcopy(self.settings),
                "playlists": [
                    {
                        **{k: v for k, v in p.items() if k != "tracks"},
                        "tracks": [self.public_track(t) for t in p["tracks"]],
                    }
                    for p in self.data["playlists"]
                ],
            }

    @staticmethod
    def public_track(tr: dict) -> dict:
        return {k: v for k, v in tr.items() if k != "raw"}

from __future__ import annotations

import copy
import logging
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


SINGLES_ID = "singles"
SYNC_RETRY = 900


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


def valid_library(data: Any) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("settings", {}), dict):
        return False
    playlists = data.get("playlists", [])
    return isinstance(playlists, list) and all(
        isinstance(p, dict)
        and isinstance(p.get("id"), str)
        and isinstance(p.get("tracks"), list)
        and all(isinstance(t, dict) and "id" in t for t in p["tracks"])
        for p in playlists
    )


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
        loaded, used = fileops.read_json(file, valid_library)
        if used != file and file.exists():
            kept = fileops.quarantine(file)
            log.error("Library file could not be read; a copy was kept as %s", kept.name if kept else "-")
        if loaded is None:
            return
        if used != file:
            log.warning("Library restored from %s", used.name)
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
            for attempt in range(5):
                try:
                    fileops.write_json(paths().library_file, self.data, keep_previous=True)
                    break
                except RuntimeError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
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

    def track_index(self) -> dict[str, tuple[dict, dict]]:
        with self.lock:
            return {track_key(pl, tr): (pl, tr) for pl in self.data["playlists"] for tr in pl["tracks"]}

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

    def _new_folder(self, name: str) -> str:
        used = {p["folder"].casefold() for p in self.data["playlists"] if p.get("folder")}
        folder = self._existing_folder(name).name
        if folder.casefold() not in used:
            return folder
        base, n = folder_name(name), 2
        while f"{base} ({n})".casefold() in used or (paths().downloads / f"{base} ({n})").exists():
            n += 1
        return f"{base} ({n})"

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
                folder = self._new_folder(name)
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

    def add_singles(self, songs: list[dict]) -> dict[str, Any]:
        with self.lock:
            pl = self.playlist(SINGLES_ID)
            new = pl is None
            if pl is None:
                pl = {
                    "id": SINGLES_ID,
                    "url": SINGLES_ID,
                    "origin": SINGLES_ID,
                    "name": origins.SINGLES_NAME,
                    "folder": self._new_folder(origins.SINGLES_NAME),
                    "tracks": [],
                    "added": time.time(),
                }
                self.data["playlists"].append(pl)
            known = {t["id"] for t in pl["tracks"]}
            added = []
            for song in songs:
                track = self.make_track(song, pl["name"])
                if track["id"] in known:
                    continue
                known.add(track["id"])
                track["number"] = len(pl["tracks"]) + 1
                pl["tracks"].append(track)
                added.append(track)
            pl["synced"] = time.time()
            self.save()
            return {"playlist": pl, "new": new, "added": added, "removed": [], "old_name": pl["name"]}

    def remove_single(self, key: str) -> bool:
        with self.lock:
            pl, tr = self.find_track(key)
            if tr is None or pl.get("origin") != SINGLES_ID:
                return False
            pl["tracks"] = [t for t in pl["tracks"] if t["id"] != tr["id"]]
            for number, track in enumerate(pl["tracks"], 1):
                track["number"] = number
            self.save()
            return True

    def remove_playlist(self, pid: str) -> None:
        with self.lock:
            self.data["playlists"] = [p for p in self.data["playlists"] if p["id"] != pid]
            self.save()

    def folder_index(self, pl: dict) -> list[tuple[Path, str, str, str]]:
        folder = self.folder(pl)
        if not folder.is_dir():
            return []
        downloads = paths().downloads
        return [
            (p, p.relative_to(downloads).as_posix(), norm(p.stem), norm(NUMBER_PREFIX.sub("", p.stem, count=1)))
            for p in folder.iterdir()
            if p.suffix.lower() in AUDIO_SUFFIXES and not fileops.is_temp(p)
        ]

    def find_file(
        self,
        pl: dict,
        tr: dict,
        exclude: set[str] | None = None,
        index: list[tuple[Path, str, str, str]] | None = None,
    ) -> Path | None:
        exclude = exclude or set()
        title, artist = norm(tr["name"]), norm(tr["artist"])
        full = norm(", ".join(tr["artists"]) + " - " + tr["name"])
        if not full:
            return None
        scored = []
        for f, relative, name, bare in self.folder_index(pl) if index is None else index:
            if relative in exclude or not name:
                continue
            score = max(fuzz.ratio(full, name), fuzz.ratio(full, bare))
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

    def music_available(self) -> bool:
        downloads = paths().downloads
        linked = any(tr.get("file") for pl in self.data["playlists"] for tr in pl["tracks"])
        try:
            return downloads.is_dir() and (not linked or any(downloads.iterdir()))
        except OSError:
            return False

    def link_files(self) -> bool:
        downloads = paths().downloads
        with self.lock:
            if not self.music_available():
                log.warning("Music folder %s is not available; files stay linked", downloads)
                return False
            for pl in self.data["playlists"]:
                taken: set[str] = set()
                for tr in pl["tracks"]:
                    if tr.get("file") and (downloads / tr["file"]).exists() and tr["file"] not in taken:
                        taken.add(tr["file"])
                    else:
                        tr["file"] = None
                index = self.folder_index(pl) if any(tr["file"] is None for tr in pl["tracks"]) else []
                for tr in pl["tracks"]:
                    if tr["file"] is None:
                        found = self.find_file(pl, tr, exclude=taken, index=index)
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
        return True

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
        now = time.time()
        due = []
        with self.lock:
            for p in self.data["playlists"]:
                if p.get("origin") == SINGLES_ID:
                    continue
                failed = p.get("sync_failed")
                if failed:
                    wait = min(hours * 3600, SYNC_RETRY * 2 ** (failed["count"] - 1))
                    if now >= failed["t"] + wait:
                        due.append(p["id"])
                elif (p.get("synced") or p.get("added") or 0) < now - hours * 3600:
                    due.append(p["id"])
        return due

    def note_sync(self, pid: str | None, ok: bool) -> None:
        with self.lock:
            pl = self.playlist(pid) if pid else None
            if pl is None:
                return
            if ok:
                pl.pop("sync_failed", None)
            else:
                count = (pl.get("sync_failed") or {}).get("count", 0) + 1
                pl["sync_failed"] = {"t": time.time(), "count": count}

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

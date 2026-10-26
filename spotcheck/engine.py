from __future__ import annotations

import asyncio
import concurrent.futures as cf
import contextlib
import json
import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from . import analysis, deps, diagnostics, fileops, origins, sources, terms
from .acquire import Acquiring
from .checks import Checks
from .config import paths
from .errors import Cancelled, JobError
from .files import Placement
from .library import (
    SINGLES_ID,
    Library,
    compute_status,
    playlist_id,
    track_key,
)
from .organize import Organizing

log = logging.getLogger(__name__)

CANDIDATE_CACHE_DAYS = 14
CANDIDATE_CACHE_BYTES = 3_000_000_000
SCAN_INTERVAL = 300
FULL_PROCESS = {"download": True, "validate": True, "verify": True, "fix": True, "trim": True}
SPOTDL_LOAD_TIMEOUT = 1800
SPOTDL_FOUND = re.compile(r"Found (\d+) songs? in (.+?)(?: \((?:Playlist|Album|Song)\))?\s*$")
LOG_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}
INTERRUPTIBLE = ("process", "sync")
TERMINAL_STATES = ("done", "failed", "cancelled")
JOB_HISTORY = 100


class Bus:
    def __init__(self) -> None:
        self.subscribers: set = set()
        self.loop = None
        self.logs: deque[dict] = deque(maxlen=400)

    def emit(self, event: dict) -> None:
        if self.loop is None:
            return
        for q in list(self.subscribers):
            self.loop.call_soon_threadsafe(self.deliver, q, event)

    @staticmethod
    def deliver(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            while not q.empty():
                q.get_nowait()
            q.put_nowait({"type": "resync"})

    def log(self, level: str, code: str, track: str | None = None, **params: Any) -> None:
        event = {"type": "log", "t": time.time(), "level": level, "code": code, "params": params, "track": track}
        self.logs.append(event)
        log.log(LOG_LEVELS.get(level, logging.INFO), "%s", diagnostics.format_event(event))
        self.emit(event)


class Engine(Checks, Acquiring, Placement, Organizing):
    def __init__(self, library: Library, bus: Bus) -> None:
        self.lib = library
        self.bus = bus
        self.queue: queue.Queue[str] = queue.Queue()
        self.jobs: dict[str, dict] = {}
        self.order: list[str] = []
        self.lock = threading.RLock()
        self._preview_fps: dict[tuple[str, float], tuple[int, ...]] = {}
        self._preview_lock = threading.Lock()
        self._clean_workspace()
        self._next_scan = 0.0
        self._automation = threading.Event()
        self._rename_blocked: dict[str, float] = {}
        self._m3u_blocked: dict[str, float] = {}
        threading.Thread(target=self._worker, daemon=True, name="spotcheck-worker").start()

    def start_automation(self) -> None:
        threading.Thread(target=self._automation_loop, daemon=True, name="spotcheck-automation").start()

    def rescan_soon(self) -> None:
        self._next_scan = 0.0
        self._automation.set()

    def _automation_loop(self) -> None:
        while True:
            try:
                self.automate()
            except Exception:
                log.exception("Automatic maintenance failed")
            self._automation.wait(30)
            self._automation.clear()

    def automate(self) -> list[str]:
        if self.busy() or not terms.accepted():
            return []
        jobs = []
        st = self.st
        due = self.lib.due_for_sync(float(st["auto_sync_hours"]))
        if due:
            jobs.append(
                self.submit(
                    "sync", "auto_sync", {"count": len(due)}, playlist_ids=due, follow_up=st["auto_download_new"]
                )
            )
        if time.time() >= self._next_scan:
            self._next_scan = time.time() + SCAN_INTERVAL
            self.lib.link_files()
            changed = self.lib.changed_tracks() if st["auto_check"] else []
            if changed:
                flags = {"validate": True, "verify": True}
                jobs.append(
                    self.submit("process", "auto_check", {"count": len(changed)}, track_ids=changed, flags=flags)
                )
        if not jobs and self.needs_organize():
            jobs.append(self.submit("organize", "organize"))
        return jobs

    @property
    def st(self) -> dict[str, Any]:
        return self.lib.settings

    def _clean_workspace(self) -> None:
        for p in paths().tmp.iterdir():
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        for pl in self.lib.data["playlists"]:
            fileops.remove_leftovers(self.lib.folder(pl))
        self.prune_candidates()

    def prune_candidates(self) -> int:
        keep = {
            tr["proposal"]["audio"]
            for pl in self.lib.data["playlists"]
            for tr in pl["tracks"]
            if tr.get("proposal") and tr["proposal"].get("audio")
        }
        cutoff = time.time() - CANDIDATE_CACHE_DAYS * 86400
        files = []
        for p in paths().candidates.glob("*"):
            try:
                files.append((p, p.stat()))
            except OSError:
                continue
        total = removed = 0
        for p, st in sorted(files, key=lambda item: item[1].st_mtime, reverse=True):
            if p.name in keep:
                continue
            total += st.st_size
            if st.st_mtime < cutoff or total > CANDIDATE_CACHE_BYTES:
                with contextlib.suppress(OSError):
                    p.unlink()
                    removed += 1
        return removed

    def busy(self) -> bool:
        with self.lock:
            return any(j["state"] in ("queued", "running") for j in self.jobs.values())

    def reset(self, library: Library) -> None:
        with self.lock:
            self.lib = library
            self.jobs.clear()
            self.order.clear()
            self.bus.logs.clear()
            self._clean_workspace()
        self.rescan_soon()

    @staticmethod
    def public_job(job: dict) -> dict:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def jobs_public(self) -> list[dict]:
        with self.lock:
            return [self.public_job(self.jobs[i]) for i in self.order[-25:]]

    def _emit_job(self, job: dict) -> None:
        self.bus.emit({"type": "job", "job": self.public_job(job)})

    def submit(self, kind: str, label: str, label_params: dict | None = None, **params: Any) -> str:
        job = {
            "id": uuid.uuid4().hex[:10],
            "kind": kind,
            "label": {"code": label, "params": label_params or {}},
            "state": "queued",
            "done": 0,
            "total": 0,
            "current": "",
            "created": time.time(),
            "started": None,
            "finished": None,
            "summary": {},
            "_params": params,
            "_cancel": False,
        }
        with self.lock:
            self.jobs[job["id"]] = job
            self.order.append(job["id"])
            for old in self.order[:-JOB_HISTORY]:
                if self.jobs[old]["state"] in TERMINAL_STATES:
                    del self.jobs[old]
            self.order = [i for i in self.order if i in self.jobs]
        self.queue.put(job["id"])
        self._emit_job(job)
        return job["id"]

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["state"] in TERMINAL_STATES:
                return False
            job["_cancel"] = True
            if job["state"] == "queued":
                job["state"], job["finished"] = "cancelled", time.time()
            interrupt = job["state"] == "running" and job["kind"] in INTERRUPTIBLE
            self._emit_job(job)
        if interrupt:
            analysis.stop_all(jobs_only=True)
        return True

    def _worker(self) -> None:
        handlers: dict[str, Callable[..., None]] = {
            "process": self._job_process,
            "sync": self._job_sync,
            "deps": self._job_deps,
            "apply": self._job_apply,
            "restore": self._job_restore,
            "organize": self._job_organize,
            "export": self._job_export,
            "tags": self._job_tags,
        }
        while True:
            job = self.jobs.get(self.queue.get())
            if job is None or job["state"] != "queued":
                continue
            job["state"], job["started"] = "running", time.time()
            self._emit_job(job)
            outcome = self._run_job(handlers[job["kind"]], job)
            try:
                self.lib.save()
                sources.flush_index()
                if job["kind"] in ("process", "apply"):
                    self.prune_candidates()
            except Exception as exc:
                log.exception("Saving after job %s failed", job["id"])
                self.bus.log("error", "save_failed", error=str(exc))
            job["state"], job["finished"], job["current"] = outcome, time.time(), ""
            self._emit_job(job)
            self.bus.emit({"type": "library"})

    def _run_job(self, handler: Callable[..., None], job: dict) -> str:
        try:
            handler(job, **job["_params"])
            return "cancelled" if job["_cancel"] else "done"
        except Cancelled:
            return "cancelled"
        except JobError as exc:
            job["summary"] = {**job["summary"], "error": exc.code}
            self.bus.log("error", exc.code)
        except Exception as exc:
            log.exception("Job %s (%s) failed", job["id"], job["kind"])
            job["summary"] = {**job["summary"], "error": str(exc)}
            self.bus.log("error", "job_failed", error=str(exc))
        return "failed"

    @staticmethod
    def _check_cancel(job: dict) -> None:
        if job["_cancel"]:
            raise Cancelled

    def touch(self, pl: dict, tr: dict, step: str | None = None) -> None:
        tr["status"] = compute_status(tr)
        tr["updated"] = time.time()
        tr["step"] = step
        self.bus.emit({"type": "track", "playlist_id": pl["id"], "track": Library.public_track(tr)})

    def process_track(self, job: dict, pl: dict, tr: dict, flags: dict[str, bool]) -> None:
        label = f"{tr['artist']} - {tr['name']}"
        job["current"] = label
        self._emit_job(job)
        try:
            path = self.lib.abs_file(tr)
            fresh = False
            if path is None:
                if flags.get("download"):
                    path = self.acquire_new(job, pl, tr)
                    fresh = path is not None
                if path is None:
                    tr["file"], tr["check"], tr["verify"] = None, None, None
                    self.refresh_notes(tr)
                    self.touch(pl, tr)
                    return
            self._check_cancel(job)
            if not fresh:
                if flags.get("validate") or tr.get("check") is None:
                    self.touch(pl, tr, "check")
                    self.sync_source(tr, path)
                    tr["check"] = self.do_check(tr, path)
                if flags.get("verify") or tr.get("verify") is None:
                    self.touch(pl, tr, "verify")
                    tr["verify"] = self.do_verify(tr, path)
                self._check_cancel(job)
                if flags.get("fix"):
                    if self.needs_fix(tr) or flags.get("force_fix"):
                        job["_outcomes"].append(self.improve(job, pl, tr, path))
                    else:
                        tr["proposal"] = None
                        job["_outcomes"].append("not_needed")
            if flags.get("trim"):
                self.maybe_trim(pl, tr, path, backup=not fresh)
            self.refresh_notes(tr)
            self.touch(pl, tr)
        except Cancelled:
            self.touch(pl, tr)
            raise
        except fileops.FileLockedError:
            job["_failed"] += 1
            self.bus.log("error", "file_locked", tr["id"], name=label)
            self.refresh_notes(tr)
            self.touch(pl, tr)
        except Exception as exc:
            log.exception("Processing %s failed", label)
            job["_failed"] += 1
            self.bus.log("error", "track_failed", tr["id"], name=label, error=str(exc))
            self.touch(pl, tr)

    def select(
        self,
        playlist_id: str | None = None,
        track_ids: list[str] | None = None,
        only: str | None = None,
        playlist_ids: list[str] | None = None,
    ) -> list[tuple[dict, dict]]:
        keys = set(track_ids or [])
        wanted = set(playlist_ids or []) | ({playlist_id} if playlist_id else set())
        chosen = []
        for pl in self.lib.data["playlists"]:
            if wanted and pl["id"] not in wanted:
                continue
            for tr in pl["tracks"]:
                if keys and track_key(pl, tr) not in keys and tr["id"] not in keys:
                    continue
                if only == "missing" and tr.get("file"):
                    continue
                if only == "problems" and tr.get("status") not in ("bad", "review"):
                    continue
                if only == "files" and not tr.get("file"):
                    continue
                chosen.append((pl, tr))
        return chosen

    def _job_process(
        self,
        job: dict,
        playlist_id: str | None = None,
        track_ids: list[str] | None = None,
        only: str | None = None,
        flags: dict[str, bool] | None = None,
        playlist_ids: list[str] | None = None,
    ) -> None:
        items = self.select(playlist_id, track_ids, only, playlist_ids)
        job["_outcomes"], job["_failed"] = [], 0
        job["total"] = len(items)
        self._emit_job(job)
        done_lock = threading.Lock()

        def work(item: tuple[dict, dict]) -> None:
            if job["_cancel"]:
                return
            pl, tr = item
            try:
                self.process_track(job, pl, tr, flags or {})
            except Cancelled:
                return
            finally:
                with done_lock:
                    job["done"] += 1
                self._emit_job(job)

        with cf.ThreadPoolExecutor(max_workers=int(self.st["threads"]), thread_name_prefix="spotcheck-job") as pool:
            list(pool.map(work, items))
        self.organize({pl["id"] for pl, _ in items})
        statuses: dict[str, int] = {}
        for _, tr in items:
            statuses[tr["status"]] = statuses.get(tr["status"], 0) + 1
        outcomes = job["_outcomes"]
        job["summary"] = {
            "tracks": len(items),
            "statuses": statuses,
            "proposals": sum(1 for _, tr in items if tr.get("proposal")),
            "replaced": outcomes.count("replaced"),
            "failed": job["_failed"],
            "fix": outcomes[0] if track_ids and len(items) == 1 and outcomes else None,
        }

    def _job_apply(self, job: dict, key: str) -> None:
        pl, tr = self.lib.find_track(key)
        if tr is None or not tr.get("proposal"):
            raise JobError("proposal_not_found")
        path = self.lib.abs_file(tr)
        if path is None:
            raise JobError("file_missing")
        job["total"], job["current"] = 1, f"{tr['artist']} - {tr['name']}"
        replaced = None
        try:
            replaced = self.replace_with(pl, tr, path, tr["proposal"])
            if replaced:
                self.maybe_trim(pl, tr, replaced, backup=False)
        except fileops.FileLockedError as exc:
            raise JobError("file_locked") from exc
        finally:
            self.refresh_notes(tr)
            self.touch(pl, tr)
        if not replaced:
            raise JobError("proposal_download_failed")
        self.organize({pl["id"]})
        job["done"] = 1

    def _job_restore(self, job: dict, key: str, index: int = 0) -> None:
        pl, tr = self.lib.find_track(key)
        if tr is None or not 0 <= index < len(tr["backups"]):
            raise JobError("backup_not_found")
        if not (paths().data / tr["backups"][index]["file"]).exists():
            raise JobError("backup_file_missing")
        job["total"], job["current"] = 1, f"{tr['artist']} - {tr['name']}"
        self.touch(pl, tr, "restore")
        try:
            self.restore_backup(pl, tr, tr["backups"][index])
        finally:
            self.refresh_notes(tr)
            self.touch(pl, tr)
        self.bus.log("info", "restored", tr["id"])
        self.organize({pl["id"]})
        job["done"] = 1

    def _job_sync(
        self,
        job: dict,
        urls: list[str] | None = None,
        playlist_ids: list[str] | None = None,
        follow_up: bool = False,
    ) -> None:
        targets: list[tuple[str, str | None]] = [(url, None) for url in urls or []]
        for pid in playlist_ids or []:
            pl = self.lib.playlist(pid)
            if pl and pl.get("origin") != SINGLES_ID:
                targets.append((pl["url"], pid))
        job["total"] = len(targets)
        totals = {"playlists": 0, "added": 0, "removed": 0, "failed": 0}
        synced: list[str] = []
        for url, pid in targets:
            self._check_cancel(job)
            job["current"] = url
            self._emit_job(job)
            self.bus.log("info", "playlist_loading", url=url)
            single = bool(origins.classify_track(url))
            try:
                result = self.sync_single(url) if single else self.sync_playlist(url)
            except Cancelled:
                raise
            except Exception as exc:
                log.exception("Loading %s failed", url)
                self.bus.log("error", "sync_error", url=url, error=str(exc))
                result = None
            self._check_cancel(job)
            if not single:
                self.lib.note_sync(pid or playlist_id(url), ok=result is not None)
            if result is None:
                totals["failed"] += 1
            else:
                totals["playlists"] += 1
                totals["added"] += result["added"]
                totals["removed"] += result["removed"]
                if result["id"] not in synced:
                    synced.append(result["id"])
                self.bus.emit({"type": "library"})
            job["done"] += 1
            self._emit_job(job)
        job["summary"] = totals
        self.organize(set(synced))
        if follow_up and synced:
            missing = self.select(only="missing", playlist_ids=synced)
            if missing:
                self.submit(
                    "process",
                    "process_new",
                    {"count": len(missing)},
                    playlist_ids=synced,
                    only="missing",
                    flags=dict(FULL_PROCESS),
                )

    def sync_single(self, url: str) -> dict | None:
        songs = self.load_track(url)
        if not songs:
            self.bus.log("error", "single_failed", url=url)
            return None
        change = self.lib.add_singles(songs)
        self.lib.link_files()
        label = f"{songs[0]['artist']} - {songs[0]['name']}"
        self.bus.log("info", "track_added" if change["added"] else "track_known", name=label)
        return {"id": change["playlist"]["id"], "added": len(change["added"]), "removed": 0}

    def sync_playlist(self, url: str) -> dict | None:
        name, songs = self.load_playlist(url)
        if not songs:
            self.bus.log("error", "playlist_failed", url=url)
            return None
        change = self.lib.upsert_playlist(url, name, songs)
        self.lib.link_files()
        pl = change["playlist"]
        if change["new"]:
            self.bus.log("info", "playlist_loaded", name=name, count=len(pl["tracks"]))
        else:
            if change["old_name"] != name:
                self.bus.log("info", "playlist_renamed", old=change["old_name"], name=name)
            if change["added"] or change["removed"]:
                kept = sum(1 for t in change["removed"] if t.get("file"))
                added, removed = len(change["added"]), len(change["removed"])
                self.bus.log("info", "playlist_updated", name=name, added=added, removed=removed, kept=kept)
        return {
            "id": pl["id"],
            "added": 0 if change["new"] else len(change["added"]),
            "removed": len(change["removed"]),
        }

    def load_track(self, url: str) -> list[dict]:
        if (origins.classify_track(url) or ("",))[0] == "spotify":
            return self.load_playlist(url)[1]
        return origins.load_track(
            url,
            self.ytdlp_cmd(),
            on_error=lambda code, detail: self.bus.log("warn", code, url=url, detail=diagnostics.short(detail)),
        )

    def load_playlist(self, url: str) -> tuple[str, list[dict]]:
        if (origins.classify(url) or ("spotify",))[0] != "spotify":
            name, songs, unavailable = origins.load(
                url,
                self.ytdlp_cmd(),
                on_error=lambda code, detail: self.bus.log("warn", code, url=url, detail=diagnostics.short(detail)),
            )
            if unavailable:
                self.bus.log("warn", "tracks_unavailable", name=name or url, count=unavailable)
            return name or "Playlist", songs
        out = paths().tmp / f"sync-{uuid.uuid4().hex[:8]}.spotdl"
        announced: list[bool] = []

        def progress(line: str) -> None:
            found = SPOTDL_FOUND.search(line)
            if found and not announced:
                announced.append(True)
                self.bus.log("info", "playlist_found", name=found.group(2).strip(), count=int(found.group(1)))

        try:
            result = analysis.run_streaming(
                [sys.executable, "-m", "spotdl", "save", url, "--save-file", str(out), "--simple-tui"],
                progress,
                timeout=SPOTDL_LOAD_TIMEOUT,
                cwd=str(paths().tmp),
            )
            if result.returncode != 0 or not out.exists():
                output = (result.stderr or "") + " " + (result.stdout or "")
                log.warning("spotDL output for %s: %s", url, diagnostics.sanitize(output.strip()[-3000:]))
                self.bus.log(
                    "warn", "spotdl_failed", url=url, exit_code=result.returncode, detail=diagnostics.short(output)
                )
        except subprocess.TimeoutExpired:
            self.bus.log("warn", "spotdl_timeout", url=url, minutes=SPOTDL_LOAD_TIMEOUT // 60)
        except (OSError, subprocess.SubprocessError) as exc:
            self.bus.log("warn", "spotdl_error", error=str(exc))
        songs: list[dict] = []
        if out.exists():
            try:
                loaded = json.loads(out.read_text(encoding="utf-8"))
                songs = [s for s in loaded if isinstance(s, dict) and s.get("url")] if isinstance(loaded, list) else []
            except (OSError, ValueError) as exc:
                self.bus.log("warn", "spotdl_error", error=str(exc))
            finally:
                out.unlink(missing_ok=True)
        return (songs[0].get("list_name") or "Playlist") if songs else "Playlist", songs

    def _job_deps(self, job: dict, action: str = "all") -> None:
        job["total"] = 1
        ok = deps.install(action, lambda level, code, **params: self.bus.log(level, code, **params))
        job["done"] = 1
        job["summary"] = {"ok": bool(ok)}

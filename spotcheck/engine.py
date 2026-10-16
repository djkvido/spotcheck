from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import analysis, deps, diagnostics, fileops, origins, sources, tags, terms
from .config import paths
from .library import AUDIO_SUFFIXES, SINGLES_ID, Library, base_name, compute_status, folder_name, track_key

log = logging.getLogger(__name__)

CANDIDATE_CACHE_DAYS = 14
SCAN_INTERVAL = 300
FULL_PROCESS = {"download": True, "validate": True, "verify": True, "fix": True, "trim": True}
YTDLP_FORMATS = {"opus": "bestaudio[acodec=opus]/bestaudio", "m4a": "bestaudio[ext=m4a]/bestaudio", "mp3": "bestaudio"}
RENAME_RETRY = 600
UPSCALE_BITRATE = 192
UPSCALE_CUTOFF = 16.5
SPOTDL_LOAD_TIMEOUT = 1800
LOG_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class Cancelled(Exception):
    pass


class JobError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Bus:
    def __init__(self) -> None:
        self.subscribers: set = set()
        self.loop = None
        self.logs: deque[dict] = deque(maxlen=400)

    def emit(self, event: dict) -> None:
        if self.loop is None:
            return
        for q in list(self.subscribers):
            self.loop.call_soon_threadsafe(q.put_nowait, event)

    def log(self, level: str, code: str, track: str | None = None, **params: Any) -> None:
        event = {"type": "log", "t": time.time(), "level": level, "code": code, "params": params, "track": track}
        self.logs.append(event)
        log.log(LOG_LEVELS.get(level, logging.INFO), "%s", diagnostics.format_event(event))
        self.emit(event)


class Engine:
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
        cutoff = time.time() - CANDIDATE_CACHE_DAYS * 86400
        for p in paths().candidates.glob("*"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)

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
        self.queue.put(job["id"])
        self._emit_job(job)
        return job["id"]

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["state"] in ("done", "failed", "cancelled"):
                return False
            job["_cancel"] = True
            if job["state"] == "queued":
                job["state"], job["finished"] = "cancelled", time.time()
            self._emit_job(job)
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
            try:
                handlers[job["kind"]](job, **job["_params"])
                job["state"] = "cancelled" if job["_cancel"] else "done"
            except Cancelled:
                job["state"] = "cancelled"
            except JobError as exc:
                job["state"] = "failed"
                job["summary"] = {**job["summary"], "error": exc.code}
                self.bus.log("error", exc.code)
            except Exception as exc:
                log.exception("Job %s (%s) failed", job["id"], job["kind"])
                job["state"] = "failed"
                job["summary"] = {**job["summary"], "error": str(exc)}
                self.bus.log("error", "job_failed", error=str(exc))
            job["finished"], job["current"] = time.time(), ""
            self._emit_job(job)
            self.lib.save()
            self.bus.emit({"type": "library"})

    @staticmethod
    def _check_cancel(job: dict) -> None:
        if job["_cancel"]:
            raise Cancelled

    def touch(self, pl: dict, tr: dict, step: str | None = None) -> None:
        tr["status"] = compute_status(tr)
        tr["updated"] = time.time()
        tr["step"] = step
        self.bus.emit({"type": "track", "playlist_id": pl["id"], "track": Library.public_track(tr)})

    @staticmethod
    def set_file(tr: dict, path: Path, via: str) -> None:
        tr["file"] = path.relative_to(paths().downloads).as_posix()
        source = sources.file_source(path)
        tr["source"] = {**source, "via": via} if source else None

    @staticmethod
    def sync_source(tr: dict, path: Path) -> None:
        source = sources.file_source(path)
        if (source or {}).get("id") != (tr.get("source") or {}).get("id"):
            tr["source"] = {**source, "via": "file"} if source else None

    @staticmethod
    def ytdlp_cmd() -> list[str]:
        cmd = [*deps.YTDLP_CMD, "--ignore-config"]
        deno = deps.deno_path()
        if deno:
            cmd += ["--js-runtimes", f"deno:{deno}"]
        return cmd

    def preview_fingerprint(self, preview: dict) -> tuple[int, ...]:
        file = paths().previews / preview["file"]
        key = (file.name, file.stat().st_mtime)
        with self._preview_lock:
            cached = self._preview_fps.get(key)
        if cached is None:
            cached = analysis.raw_fingerprint(file)
            with self._preview_lock:
                if len(self._preview_fps) > 400:
                    self._preview_fps.clear()
                self._preview_fps[key] = cached
        return cached

    def spotdl_fetch(self, pl: dict, tr: dict, url: str | None = None) -> Path | None:
        work = paths().tmp / f"dl-{tr['id']}-{uuid.uuid4().hex[:6]}"
        work.mkdir(parents=True)
        song = {**tr["raw"], "list_name": pl["name"]}
        if url:
            song["download_url"] = url
        spec = work / "song.spotdl"
        spec.write_text(json.dumps([song], ensure_ascii=False), encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "spotdl",
            "download",
            str(spec),
            "--output",
            (work / "{artists} - {title}.{output-ext}").as_posix(),
            "--overwrite",
            "force",
            "--simple-tui",
            "--threads",
            "1",
            "--audio",
            *self.st["audio_providers"],
            "--format",
            self.st["audio_format"],
            "--bitrate",
            self.st["mp3_bitrate"] if self.st["audio_format"] == "mp3" else "disable",
        ]
        if analysis.ffmpeg_path():
            cmd += ["--ffmpeg", analysis.ffmpeg_path()]
        try:
            analysis.run_text(cmd, timeout=480, cwd=str(work))
        except (OSError, subprocess.SubprocessError) as exc:
            self.bus.log("warn", "spotdl_error", tr["id"], error=str(exc))
        produced = [p for p in work.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES]
        if not produced:
            shutil.rmtree(work, ignore_errors=True)
            return None
        return produced[0]

    @staticmethod
    def default_via(tr: dict) -> str:
        return "spotdl" if tr.get("origin", "spotify") == "spotify" else "origin"

    def fetch_audio(self, pl: dict, tr: dict, url: str | None = None) -> Path | None:
        if tr.get("origin", "spotify") != "spotify":
            return self.ytdlp_fetch(pl, tr, url or tr["url"])
        path = self.spotdl_fetch(pl, tr, url)
        if path is None and url:
            self.bus.log("warn", "spotdl_retry", tr["id"])
            path = self.ytdlp_fetch(pl, tr, url)
        return path

    def ytdlp_fetch(self, pl: dict, tr: dict, url: str) -> Path | None:
        st = self.st
        fmt = st["audio_format"]
        work = paths().tmp / f"dl-{tr['id']}-{uuid.uuid4().hex[:6]}"
        work.mkdir(parents=True)
        stem = folder_name(f"{', '.join(tr['artists'])} - {tr['name']}")
        cmd = [*self.ytdlp_cmd(), "--no-playlist", "-q", "--no-warnings", "-f", YTDLP_FORMATS[fmt]]
        cmd += ["-x", "--audio-format", fmt]
        if fmt == "mp3":
            cmd += ["--audio-quality", st["mp3_bitrate"].upper()]
        template = str(work / f"{stem.replace('%', '%%')}.%(ext)s")
        cmd += ["--ffmpeg-location", analysis.ffmpeg(), "-o", template, "--", url]
        try:
            analysis.run_text(cmd, timeout=480, cwd=str(work))
        except (OSError, subprocess.SubprocessError) as exc:
            self.bus.log("warn", "download_error", tr["id"], error=str(exc))
        produced = [p for p in work.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES]
        if not produced:
            shutil.rmtree(work, ignore_errors=True)
            return None
        path = produced[0]
        values = {
            "title": tr["name"],
            "artist": ", ".join(tr["artists"]),
            "album": tr.get("album") or pl["name"],
            "date": str(tr.get("year") or ""),
            "comment": url,
        }
        try:
            tags.write(path, values, self.cover_bytes(tr), overwrite=True)
        except (OSError, ValueError) as exc:
            self.bus.log("warn", "tags_failed", tr["id"], error=str(exc))
        return path

    @staticmethod
    def cover_bytes(tr: dict) -> bytes | None:
        if not tr.get("cover_url"):
            return None
        try:
            data = sources.http_get(tr["cover_url"], binary=True)
        except Exception:
            return None
        return data if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" else None

    @staticmethod
    def free_path(target: Path) -> Path:
        candidate, n = target, 2
        while candidate.exists():
            candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
            n += 1
        return candidate

    def remove_replaced(self, tr: dict, old: Path, new: Path) -> None:
        if old == new:
            return
        try:
            fileops.remove(old)
        except OSError:
            self.bus.log("warn", "old_file_kept", tr["id"], file=old.name)

    def place_new(self, pl: dict, tmp: Path) -> Path:
        folder = self.lib.folder(pl)
        folder.mkdir(parents=True, exist_ok=True)
        target = self.free_path(folder / tmp.name)
        shutil.move(str(tmp), str(target))
        shutil.rmtree(tmp.parent, ignore_errors=True)
        return target

    @staticmethod
    def place_over(tmp: Path, target: Path) -> Path:
        try:
            fileops.install(tmp, target)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        return target

    @staticmethod
    def discard(tmp: Path | None) -> None:
        if tmp is not None:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    def backup(self, tr: dict, path: Path, reason: str) -> dict:
        folder = paths().backups / tr["id"]
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        target = folder / f"{int(now)}-{uuid.uuid4().hex[:4]}{path.suffix}"
        shutil.copy2(path, target)
        check, verify = tr.get("check") or {}, tr.get("verify") or {}
        entry = {
            "file": target.relative_to(paths().data).as_posix(),
            "t": now,
            "reason": reason,
            "source": tr.get("source"),
            "trimmed": tr.get("trimmed"),
            "file_len": check.get("file_len"),
            "ber": verify.get("ber"),
            "verdict": verify.get("verdict"),
        }
        tr["backups"].insert(0, entry)
        return entry

    @staticmethod
    def drop_backup(tr: dict, entry: dict) -> None:
        if entry in tr["backups"]:
            tr["backups"].remove(entry)
        (paths().data / entry["file"]).unlink(missing_ok=True)

    def prune_backups(self, tr: dict) -> None:
        keep = int(self.st["keep_backups"])
        for old in tr["backups"][keep:]:
            (paths().data / old["file"]).unlink(missing_ok=True)
        del tr["backups"][keep:]

    def do_check(self, tr: dict, path: Path) -> dict:
        errors, decoded = analysis.decode_check(path)
        header_length = analysis.duration(path)
        length = decoded or header_length
        lead, tail = analysis.silence_edges(path, length)
        volume = analysis.head_volume(path)
        gap = analysis.head_gap(path, lead)
        bitrate = analysis.bitrate_kbps(path)
        codec = analysis.codec_name(path)
        measured, cutoff = analysis.cutoff_khz(path, length)
        missing_tags = tags.missing(path) or []
        expected = float(tr["duration"])
        diff = length - expected
        extra = diff - lead - tail
        verdict, notes = "OK", []
        rank = {"OK": 0, "WARN": 1, "FAIL": 2}

        def flag(level: str, code: str | None = None, **params: Any) -> None:
            nonlocal verdict
            if rank[level] > rank[verdict]:
                verdict = level
            if code:
                notes.append({"code": code, "params": params})

        if errors:
            flag("FAIL", "damaged", errors=errors)
        if decoded and header_length - decoded > 2:
            flag("FAIL", "incomplete", header=round(header_length, 1), decoded=round(decoded, 1))
        if extra > 4:
            flag("FAIL", "intro_likely", seconds=round(extra, 1))
        elif extra > 2:
            flag("WARN", "intro_possible", seconds=round(extra, 1))
        if extra < -8:
            flag("FAIL", "shorter", seconds=round(-extra, 1))
        elif abs(diff) > 3 and extra <= 2:
            flag("WARN", "length_diff", seconds=round(diff, 1))
        if lead >= 2:
            flag("WARN", "lead_silence", seconds=round(lead, 1))
        if tail >= 2:
            flag("WARN", "tail_silence", seconds=round(tail, 1))
        if volume is not None and volume < -55:
            flag("FAIL", "silent_start", db=round(volume))
        elif volume is not None and volume < -40:
            flag("WARN", "quiet_start", db=round(volume))
        if bitrate is not None and bitrate < self.st["min_bitrate"]:
            flag("WARN", "low_bitrate", kbps=bitrate)
        if measured and cutoff is not None and cutoff < self.st["min_cutoff_khz"]:
            flag("WARN", "low_quality", khz=cutoff)
        elif (
            measured
            and cutoff is not None
            and cutoff <= UPSCALE_CUTOFF
            and (bitrate or 0) >= UPSCALE_BITRATE
            and codec in ("MP3", "AAC")
        ):
            flag("WARN", "upscaled", kbps=bitrate, khz=cutoff)
        if missing_tags:
            notes.append({"code": "tags_missing", "params": {"fields": missing_tags}})
        if gap is not None:
            if extra > 1:
                flag("WARN")
            notes.append({"code": "pause", "params": {"seconds": round(gap, 1)}})
        return {
            "verdict": verdict,
            "sig": self.lib.signature(path),
            "file_len": round(length, 1),
            "spotify_len": expected,
            "diff": round(diff, 1),
            "lead": round(lead, 1),
            "tail": round(tail, 1),
            "extra": round(extra, 1),
            "head_db": None if volume is None else round(volume, 1),
            "gap": None if gap is None else round(gap, 1),
            "bitrate": bitrate,
            "codec": codec,
            "cutoff": cutoff,
            "cutoff_measured": measured,
            "tags_missing": missing_tags,
            "decode_errors": errors,
            "notes": notes,
        }

    def do_verify(self, tr: dict, path: Path) -> dict:
        if not analysis.ffmpeg_info()["chromaprint"]:
            return {"verdict": "NO_PREVIEW", "reason": "no_chromaprint"}
        preview = sources.get_preview(tr, self.st)
        if preview is None:
            return {"verdict": "NO_PREVIEW", "reason": "no_preview"}
        match = analysis.best_alignment(path, self.preview_fingerprint(preview))
        if match is None:
            return {"verdict": "NO_PREVIEW", "reason": "preview_too_short", "ref": preview}
        ber = match["ber"]
        if ber <= self.st["accept_ber"]:
            verdict = "MATCH"
        elif ber <= self.st["unsure_ber"]:
            verdict = "UNSURE"
        else:
            verdict = "MISMATCH"
        return {
            "verdict": verdict,
            "ber": round(ber, 3),
            "at": round(match["at"], 1),
            "window": round(match["window"], 1),
            "file_len": round(match["file_len"], 1),
            "curve": analysis.downsample_min(match["curve"]),
            "ref": preview,
        }

    def check_and_verify(self, pl: dict, tr: dict, path: Path) -> None:
        self.touch(pl, tr, "check")
        tr["check"] = self.do_check(tr, path)
        self.touch(pl, tr, "verify")
        tr["verify"] = self.do_verify(tr, path)

    def acceptable(self, tr: dict) -> bool:
        check, verify = tr.get("check") or {}, tr.get("verify") or {}
        if verify.get("verdict") == "MATCH":
            return check.get("verdict") != "FAIL"
        if verify.get("verdict") == "NO_PREVIEW":
            return check.get("verdict") != "FAIL" and abs(check.get("extra", 99)) <= self.st["exact_diff"] + 1
        return False

    @staticmethod
    def needs_fix(tr: dict) -> bool:
        verdict = (tr.get("verify") or {}).get("verdict")
        check = tr.get("check") or {}
        extra = check.get("extra") or 0
        wrong_length = check.get("verdict") == "FAIL" and (extra > 4 or extra < -8)
        low_quality = any(n["code"] == "low_quality" for n in check.get("notes", []))
        return verdict in ("MISMATCH", "UNSURE") or wrong_length or low_quality

    @staticmethod
    def refresh_notes(tr: dict) -> None:
        notes = list((tr.get("check") or {}).get("notes", []))
        verify = tr.get("verify") or {}
        if verify.get("verdict") in ("MISMATCH", "UNSURE"):
            notes.insert(0, {"code": verify["verdict"].lower(), "params": {"ber": verify["ber"]}})
        elif verify.get("verdict") == "NO_PREVIEW":
            notes.append({"code": verify.get("reason", "no_preview"), "params": {}})
        if tr.get("proposal"):
            notes.insert(0, {"code": "proposal", "params": {"ber": tr["proposal"].get("ber")}})
        tr["notes"] = notes

    def find_best_candidate(self, job: dict, tr: dict, exclude: set[str]) -> tuple[dict | None, list[dict]]:
        st = self.st
        preview = sources.get_preview(tr, st)
        candidates = sources.find_candidates(tr, exclude, st)
        if preview is None or not analysis.ffmpeg_info()["chromaprint"]:
            close = [c for c in candidates if abs(c["diff"]) <= st["exact_diff"]]
            return (dict(close[0], ber=None) if close else None), [dict(c, ber=None) for c in candidates]

        reference = self.preview_fingerprint(preview)
        tried: list[dict] = []
        for cand in candidates:
            self._check_cancel(job)
            audio = sources.fetch_candidate(cand, self.ytdlp_cmd())
            if audio is None:
                tried.append(dict(cand, ber=None, error="download_failed"))
                continue
            match = analysis.best_alignment(audio, reference)
            record = dict(
                cand,
                ber=None if match is None else round(match["ber"], 3),
                at=None if match is None else round(match["at"], 1),
            )
            if record["ber"] is not None and record["ber"] <= st["accept_ber"]:
                record["cutoff_measured"], record["cutoff"] = analysis.cutoff_khz(audio, float(cand["duration"] or 0))
            tried.append(record)
            strong = record["ber"] is not None and record["ber"] <= st["strong_ber"]
            if strong and abs(record["diff"]) <= st["exact_diff"]:
                break
        matching = [r for r in tried if r["ber"] is not None and r["ber"] <= st["accept_ber"]]
        if not matching:
            return None, tried
        fits = [r for r in matching if abs(r["diff"]) <= st["exact_diff"]] or matching
        lowest = min(r["ber"] for r in fits)
        close = [r for r in fits if r["ber"] <= lowest + 0.05]
        best = max(close, key=lambda r: (self.spectrum_rank(r), -r["ber"]))
        return best, tried

    @staticmethod
    def spectrum_rank(record: dict) -> float:
        if not record.get("cutoff_measured"):
            return 0.0
        return 30.0 if record.get("cutoff") is None else float(record["cutoff"])

    def is_better(self, cand: dict, tr: dict) -> bool:
        st = self.st
        verify, check = tr.get("verify") or {}, tr.get("check") or {}
        if cand.get("ber") is None:
            return abs(cand["diff"]) <= st["exact_diff"] and abs(check.get("extra", 99)) > st["exact_diff"] + 1
        if verify.get("verdict") in ("MISMATCH", "UNSURE"):
            return verify.get("ber") is None or cand["ber"] < verify["ber"] - 0.05
        if verify.get("verdict") == "NO_PREVIEW":
            return True
        fits = abs(cand["diff"]) <= st["exact_diff"]
        current = check.get("cutoff")
        low = bool(check.get("cutoff_measured")) and current is not None and current < st["min_cutoff_khz"]
        sounds_better = low and self.spectrum_rank(cand) >= current + 1.5
        return fits and ((check.get("extra") or 0) > 4 or sounds_better)

    def acquire_new(self, job: dict, pl: dict, tr: dict) -> Path | None:
        exclude = set(tr["rejected_ids"])
        copied = self.copy_twin(pl, tr)
        if copied is not None:
            return copied
        self.touch(pl, tr, "download")
        first = self.fetch_audio(pl, tr)
        fallback = None
        if first is not None:
            self.check_and_verify(pl, tr, first)
            if self.acceptable(tr):
                path = self.place_new(pl, first)
                self.set_file(tr, path, self.default_via(tr))
                return path
            fallback = (first, tr["check"], tr["verify"])
            source = sources.file_source(first)
            if source:
                exclude.add(source["id"])
            self.bus.log("warn", "source_mismatch", tr["id"])
        self._check_cancel(job)

        self.touch(pl, tr, "search")
        best, tried = self.find_best_candidate(job, tr, exclude)
        tr["candidates"] = tried[:10]
        if best is not None:
            self.touch(pl, tr, "download")
            second = self.fetch_audio(pl, tr, url=best["url"])
            if second is not None:
                self.check_and_verify(pl, tr, second)
                fallback_ber = fallback[2].get("ber") if fallback else None
                if fallback is None or self.acceptable(tr) or (tr["verify"].get("ber") or 1) < (fallback_ber or 1):
                    self.discard(fallback[0] if fallback else None)
                    path = self.place_new(pl, second)
                    self.set_file(tr, path, "candidate")
                    return path
                self.discard(second)
        if fallback is not None:
            first, tr["check"], tr["verify"] = fallback
            path = self.place_new(pl, first)
            self.set_file(tr, path, self.default_via(tr))
            return path
        return None

    def copy_twin(self, pl: dict, tr: dict) -> Path | None:
        for other in self.lib.data["playlists"]:
            if other is pl:
                continue
            twin = next((t for t in other["tracks"] if t["id"] == tr["id"]), None)
            source = self.lib.abs_file(twin) if twin and twin.get("status") in ("ok", "partial") else None
            if source is None:
                continue
            self.touch(pl, tr, "copy")
            work = paths().tmp / f"copy-{tr['id']}-{uuid.uuid4().hex[:6]}"
            work.mkdir(parents=True)
            tmp = work / base_name(twin, source.name)
            shutil.copy2(source, tmp)
            self.check_and_verify(pl, tr, tmp)
            if not self.acceptable(tr):
                self.discard(tmp)
                return None
            path = self.place_new(pl, tmp)
            self.set_file(tr, path, (twin.get("source") or {}).get("via", "spotdl"))
            tr["trimmed"] = twin.get("trimmed")
            self.bus.log("info", "copied_twin", tr["id"], playlist=other["name"])
            return path
        return None

    def replace_with(self, pl: dict, tr: dict, path: Path, cand: dict) -> Path | None:
        self.touch(pl, tr, "replace")
        tmp = self.fetch_audio(pl, tr, url=cand["url"])
        if tmp is None:
            self.bus.log("warn", "proposal_download_failed", tr["id"])
            return None
        old_id = (tr.get("source") or {}).get("id")
        entry = self.backup(tr, path, "replace")
        target = path.with_suffix(tmp.suffix.lower())
        if target != path:
            target = self.free_path(target)
        try:
            self.place_over(tmp, target)
        except OSError:
            self.drop_backup(tr, entry)
            raise
        self.remove_replaced(tr, path, target)
        self.prune_backups(tr)
        if old_id and old_id not in tr["rejected_ids"]:
            tr["rejected_ids"].append(old_id)
        self.set_file(tr, target, "candidate")
        tr["proposal"], tr["trimmed"] = None, None
        self.check_and_verify(pl, tr, target)
        self.bus.log("info", "replaced", tr["id"], url=cand["url"])
        return target

    def improve(self, job: dict, pl: dict, tr: dict, path: Path) -> str:
        exclude = set(tr["rejected_ids"])
        if (tr.get("source") or {}).get("id"):
            exclude.add(tr["source"]["id"])
        self.touch(pl, tr, "fix")
        best, tried = self.find_best_candidate(job, tr, exclude)
        tr["candidates"] = tried[:10]
        if best is None or not self.is_better(best, tr):
            tr["proposal"] = None
            return "none"
        if self.st["auto_replace"]:
            return "replaced" if self.replace_with(pl, tr, path, best) else "none"
        audio = sources.cached_candidate(best["id"])
        tr["proposal"] = {**best, "found": time.time(), "audio": audio.name if audio else None}
        self.bus.log("info", "proposal_found", tr["id"], url=best["url"])
        return "proposed"

    def maybe_trim(self, pl: dict, tr: dict, path: Path, backup: bool) -> None:
        st, check = self.st, tr.get("check") or {}
        if not st["trim_silence"] or not check:
            return
        lead, tail, length = check.get("lead", 0), check.get("tail", 0), check.get("file_len", 0)
        start = max(0.0, lead - st["keep_lead"]) if lead >= st["min_silence"] else 0.0
        end = length - max(0.0, tail - st["keep_tail"]) if tail >= st["min_silence"] else length
        if start <= 0.05 and length - end <= 0.05:
            return
        self.touch(pl, tr, "trim")
        entry = self.backup(tr, path, "trim") if backup else None
        try:
            trimmed = analysis.trim(path, start, end - start)
        except OSError:
            if entry is not None:
                self.drop_backup(tr, entry)
            raise
        if not trimmed:
            if entry is not None:
                self.drop_backup(tr, entry)
            self.bus.log("warn", "trim_failed", tr["id"])
            return
        self.prune_backups(tr)
        done = tr.get("trimmed") or {"start": 0, "end": 0}
        tr["trimmed"] = {"start": round(done["start"] + start, 1), "end": round(done["end"] + length - end, 1)}
        self.check_and_verify(pl, tr, path)
        self.bus.log("info", "trimmed", tr["id"], start=round(start, 1), end=round(length - end, 1))

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

        with cf.ThreadPoolExecutor(max_workers=int(self.st["threads"])) as pool:
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
        chosen = tr["backups"].pop(index)
        held = paths().tmp / f"restore-{uuid.uuid4().hex[:6]}{Path(chosen['file']).suffix}"
        shutil.move(str(paths().data / chosen["file"]), str(held))
        path = self.lib.abs_file(tr)
        if path is None:
            folder = self.lib.folder(pl)
            folder.mkdir(parents=True, exist_ok=True)
            stem = Path(tr["file"]).stem if tr.get("file") else folder_name(f"{tr['artist']} - {tr['name']}")
            path = self.free_path(folder / f"{stem}{held.suffix}")
            shutil.move(str(held), str(path))
        else:
            entry = self.backup(tr, path, "before_restore")
            target = path.with_suffix(held.suffix)
            if target != path:
                target = self.free_path(target)
            try:
                fileops.install(held, target)
            except OSError as exc:
                self.drop_backup(tr, entry)
                shutil.move(str(held), str(paths().data / chosen["file"]))
                tr["backups"].insert(index, chosen)
                self.touch(pl, tr)
                locked = isinstance(exc, fileops.FileLockedError)
                raise JobError("file_locked" if locked else "restore_failed") from exc
            self.remove_replaced(tr, path, target)
            path = target
            self.prune_backups(tr)
        self.set_file(tr, path, (chosen.get("source") or {}).get("via", "backup"))
        tr["trimmed"], tr["proposal"] = chosen.get("trimmed"), None
        restored_id = (tr.get("source") or {}).get("id")
        if restored_id in tr["rejected_ids"]:
            tr["rejected_ids"].remove(restored_id)
        self.check_and_verify(pl, tr, path)
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
        targets = list(urls or [])
        for pid in playlist_ids or []:
            pl = self.lib.playlist(pid)
            if pl and pl.get("origin") != SINGLES_ID:
                targets.append(pl["url"])
        job["total"] = len(targets)
        loaded, added, removed, failed = 0, 0, 0, 0
        synced: list[str] = []
        for url in targets:
            self._check_cancel(job)
            job["current"] = url
            self._emit_job(job)
            self.bus.log("info", "playlist_loading", url=url)
            if origins.classify_track(url):
                songs = self.load_track(url)
                if not songs:
                    failed += 1
                    self.bus.log("error", "track_failed", url=url)
                else:
                    change = self.lib.add_singles(songs)
                    self.lib.link_files()
                    if change["playlist"]["id"] not in synced:
                        synced.append(change["playlist"]["id"])
                    loaded += 1
                    added += len(change["added"])
                    label = f"{songs[0]['artist']} - {songs[0]['name']}"
                    self.bus.log("info", "track_added" if change["added"] else "track_known", name=label)
                    self.bus.emit({"type": "library"})
                job["done"] += 1
                self._emit_job(job)
                continue
            name, songs = self.load_playlist(url)
            if not songs:
                failed += 1
                self.bus.log("error", "playlist_failed", url=url)
            else:
                change = self.lib.upsert_playlist(url, name, songs)
                self.lib.link_files()
                pl = change["playlist"]
                synced.append(pl["id"])
                loaded += 1
                added += 0 if change["new"] else len(change["added"])
                removed += len(change["removed"])
                if change["new"]:
                    self.bus.log("info", "playlist_loaded", name=name, count=len(pl["tracks"]))
                else:
                    if change["old_name"] != name:
                        self.bus.log("info", "playlist_renamed", old=change["old_name"], name=name)
                    kept = sum(1 for t in change["removed"] if t.get("file"))
                    if change["added"] or change["removed"]:
                        self.bus.log(
                            "info",
                            "playlist_updated",
                            name=name,
                            added=len(change["added"]),
                            removed=len(change["removed"]),
                            kept=kept,
                        )
                self.bus.emit({"type": "library"})
            job["done"] += 1
            self._emit_job(job)
        job["summary"] = {"playlists": loaded, "added": added, "removed": removed, "failed": failed}
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
        try:
            result = analysis.run_text(
                [sys.executable, "-m", "spotdl", "save", url, "--save-file", str(out), "--simple-tui"],
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
            songs = json.loads(out.read_text(encoding="utf-8"))
            out.unlink(missing_ok=True)
        return (songs[0].get("list_name") or "Playlist") if songs else "Playlist", songs

    def planned_names(self, pl: dict) -> list[tuple[dict, Path, Path, bool]]:
        number = bool(self.st["number_files"])
        width = max(3, len(str(len(pl["tracks"]))))
        folder = self.lib.folder(pl)
        moves = []
        for index, tr in enumerate(pl["tracks"], 1):
            path = self.lib.abs_file(tr)
            if path is None or path.parent != folder:
                continue
            base = base_name(tr, path.name)
            name = f"{index:0{width}d} - {base}" if number else base
            if name != path.name:
                moves.append((tr, path, folder / name, number))
            else:
                tr["numbered"] = number
        return moves

    def playlist_text(self, pl: dict, names: dict[str, str] | None = None) -> str | None:
        folder = self.lib.folder(pl)
        lines = ["#EXTM3U", f"#PLAYLIST:{pl['name']}"]
        for tr in pl["tracks"]:
            if names is not None:
                name = names.get(tr["id"])
            else:
                path = self.lib.abs_file(tr)
                name = None if path is None else Path(os.path.relpath(path, folder)).as_posix()
            if not name:
                continue
            length = round((tr.get("check") or {}).get("file_len") or tr["duration"] or -1)
            lines += [f"#EXTINF:{length},{', '.join(tr['artists'])} - {tr['name']}", name]
        return "\r\n".join(lines) + "\r\n" if len(lines) > 2 else None

    def playlist_file(self, pl: dict) -> Path:
        return self.lib.folder(pl) / f"{folder_name(pl['name'])}.m3u8"

    def playlist_file_due(self, pl: dict) -> bool:
        target = self.playlist_file(pl)
        if not self.st["write_playlists"]:
            return False
        text = self.playlist_text(pl)
        if text is None:
            return False
        try:
            return target.read_bytes() != text.encode("utf-8") or pl.get("m3u") != target.name
        except OSError:
            return True

    def needs_organize(self) -> bool:
        now = time.time()
        with self.lib.lock:
            for pl in self.lib.data["playlists"]:
                moves = self.planned_names(pl)
                due = [m for m in moves if now - self._rename_blocked.get(str(m[1]), 0) > RENAME_RETRY]
                if due or self.playlist_file_due(pl):
                    return True
        return False

    def rename_files(self, moves: list[tuple[dict, Path, Path, bool]]) -> tuple[int, int]:
        moving = {str(m[1]).lower() for m in moves}
        downloads = paths().downloads
        staged = []
        blocked = 0
        for tr, src, dst, numbered in moves:
            taken = dst.exists() and str(dst).lower() not in moving and not src.samefile(dst)
            if taken:
                self.bus.log("warn", "rename_conflict", tr["id"], file=dst.name)
                continue
            tmp = src.with_name(f"{dst.stem}.renaming-{uuid.uuid4().hex[:6]}{src.suffix}")
            try:
                os.rename(src, tmp)
            except OSError:
                self._rename_blocked[str(src)] = time.time()
                blocked += 1
                continue
            tr["file"] = tmp.relative_to(downloads).as_posix()
            staged.append((tr, src, tmp, dst, numbered))
        renamed = 0
        for tr, src, tmp, dst, numbered in staged:
            for target in (dst, src):
                try:
                    os.rename(tmp, target)
                except OSError:
                    continue
                tr["file"] = target.relative_to(downloads).as_posix()
                if target == dst:
                    tr["numbered"] = numbered
                    renamed += 1
                break
        if blocked:
            self.bus.log("warn", "rename_blocked", count=blocked)
        return renamed, blocked

    def write_playlist_file(self, pl: dict, force: bool = False) -> bool:
        old = self.lib.folder(pl) / pl["m3u"] if pl.get("m3u") else None
        if not force and not self.playlist_file_due(pl):
            return False
        text = self.playlist_text(pl)
        if text is None:
            return False
        target = self.playlist_file(pl)
        tmp = fileops.temp_path(target, "m3u")
        tmp.write_bytes(text.encode("utf-8"))
        fileops.replace(tmp, target)
        if old is not None and old != target:
            old.unlink(missing_ok=True)
        pl["m3u"] = target.name
        return True

    def organize(self, playlist_ids: set[str] | None = None) -> dict[str, int]:
        renamed = blocked = written = 0
        for pl in self.lib.data["playlists"]:
            if playlist_ids is not None and pl["id"] not in playlist_ids:
                continue
            moves = self.planned_names(pl)
            if moves:
                done, failed = self.rename_files(moves)
                renamed, blocked = renamed + done, blocked + failed
                for tr, *_ in moves:
                    self.touch(pl, tr)
            try:
                written += self.write_playlist_file(pl)
            except OSError as exc:
                self.bus.log("warn", "playlist_file_failed", name=pl["name"], error=str(exc))
        if renamed:
            self.bus.log("info", "renamed", count=renamed)
        return {"renamed": renamed, "blocked": blocked, "playlists": written}

    def _job_organize(self, job: dict) -> None:
        job["total"] = 1
        job["summary"] = self.organize()
        job["done"] = 1

    def export_names(self, pl: dict, number: bool) -> dict[str, str]:
        width = max(3, len(str(len(pl["tracks"]))))
        names = {}
        for index, tr in enumerate(pl["tracks"], 1):
            path = self.lib.abs_file(tr)
            if path is not None:
                base = base_name(tr, path.name)
                names[tr["id"]] = f"{index:0{width}d} - {base}" if number else base
        return names

    def _job_export(self, job: dict, playlist_ids: list[str], target: str, number: bool, reveal: bool) -> None:
        root = Path(target)
        lists = [pl for pl in self.lib.data["playlists"] if pl["id"] in set(playlist_ids)]
        plan = []
        for pl in lists:
            names = self.export_names(pl, number)
            dest = root / pl["folder"]
            for tr in pl["tracks"]:
                if tr["id"] in names:
                    plan.append((pl, tr, self.lib.abs_file(tr), dest / names[tr["id"]]))
        job["total"] = len(plan)
        self._emit_job(job)

        def unchanged(src: Path, dst: Path) -> bool:
            if not dst.exists():
                return False
            have, want = dst.stat(), src.stat()
            return have.st_size == want.st_size and have.st_mtime >= want.st_mtime

        needed = sum(src.stat().st_size for _, _, src, dst in plan if not unchanged(src, dst))
        root.mkdir(parents=True, exist_ok=True)
        if needed > shutil.disk_usage(root).free:
            raise JobError("not_enough_space")
        copied = skipped = 0
        for _, tr, src, dst in plan:
            self._check_cancel(job)
            job["current"] = f"{tr['artist']} - {tr['name']}"
            if unchanged(src, dst):
                skipped += 1
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                staged = fileops.temp_path(dst, "copy")
                shutil.copy2(src, staged)
                fileops.replace(staged, dst)
                copied += 1
            job["done"] += 1
            self._emit_job(job)
        for pl in lists:
            text = self.playlist_text(pl, self.export_names(pl, number))
            if text is not None:
                dest = root / pl["folder"]
                dest.mkdir(parents=True, exist_ok=True)
                (dest / f"{folder_name(pl['name'])}.m3u8").write_bytes(text.encode("utf-8"))
        job["summary"] = {"playlists": len(lists), "files": len(plan), "copied": copied, "skipped": skipped}
        self.bus.log("info", "exported", count=len(plan), folder=str(root))
        if reveal:
            fileops.reveal(root)

    def _job_tags(self, job: dict, playlist_id: str | None = None, track_ids: list[str] | None = None) -> None:
        items = [
            (pl, tr)
            for pl, tr in self.select(playlist_id, track_ids, "files")
            if (tr.get("check") or {}).get("tags_missing")
        ]
        job["total"] = len(items)
        self._emit_job(job)
        fixed = failed = 0
        for pl, tr in items:
            self._check_cancel(job)
            job["current"] = f"{tr['artist']} - {tr['name']}"
            path = self.lib.abs_file(tr)
            if path is None:
                continue
            check = tr["check"]
            values = {
                "title": tr["name"],
                "artist": ", ".join(tr["artists"]),
                "album": tr.get("album") or "",
                "date": str(tr.get("year") or ""),
            }
            cover = self.cover_bytes(tr) if "cover" in check["tags_missing"] else None
            staged = fileops.temp_path(path, "tags")
            try:
                shutil.copy2(path, staged)
                tags.write(staged, values, cover)
                fileops.replace(staged, path)
            except (OSError, ValueError) as exc:
                staged.unlink(missing_ok=True)
                failed += 1
                code = "file_locked" if isinstance(exc, fileops.FileLockedError) else "tags_failed"
                self.bus.log("error", code, tr["id"], name=job["current"], error=str(exc))
            else:
                fixed += 1
                (paths().covers / f"{tr['id']}.jpg").unlink(missing_ok=True)
                check["tags_missing"] = tags.missing(path) or []
                check["sig"] = self.lib.signature(path)
                check["notes"] = [n for n in check.get("notes", []) if n["code"] != "tags_missing"]
                if check["tags_missing"]:
                    check["notes"].append({"code": "tags_missing", "params": {"fields": check["tags_missing"]}})
                self.refresh_notes(tr)
            self.touch(pl, tr)
            job["done"] += 1
            self._emit_job(job)
        job["summary"] = {"tracks": len(items), "fixed": fixed, "failed": failed}

    def _job_deps(self, job: dict, action: str = "all") -> None:
        job["total"] = 1
        ok = deps.install(action, lambda level, code, **params: self.bus.log(level, code, **params))
        job["done"] = 1
        job["summary"] = {"ok": bool(ok)}

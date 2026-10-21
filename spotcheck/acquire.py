from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import analysis, deps, sources, tags
from .config import paths
from .library import AUDIO_SUFFIXES, base_name, folder_name

YTDLP_FORMATS = {"opus": "bestaudio[acodec=opus]/bestaudio", "m4a": "bestaudio[ext=m4a]/bestaudio", "mp3": "bestaudio"}


class Acquiring:
    @staticmethod
    def ytdlp_cmd() -> list[str]:
        cmd = [*deps.YTDLP_CMD, "--ignore-config"]
        deno = deps.deno_path()
        if deno:
            cmd += ["--js-runtimes", f"deno:{deno}"]
        return cmd

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
            target = self.reserve(target)
        try:
            self.place_over(tmp, target)
        except OSError:
            self.drop_backup(tr, entry)
            if target != path:
                self.release(target)
            raise
        self.remove_replaced(tr, path, target)
        self.prune_backups(tr)
        if old_id and old_id not in tr["rejected_ids"]:
            tr["rejected_ids"].append(old_id)
        self.set_file(tr, target, "candidate")
        tr["proposal"], tr["trimmed"] = None, None
        self.check_and_verify(pl, tr, target)
        self.lib.save()
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
        if entry is not None:
            self.lib.save()
        self.bus.log("info", "trimmed", tr["id"], start=round(start, 1), end=round(length - end, 1))

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path

from . import fileops, tags
from .config import paths
from .errors import JobError
from .library import base_name, folder_name

RENAME_RETRY = 600


class Organizing:
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
                if due:
                    return True
                if now - self._m3u_blocked.get(pl["id"], 0) > RENAME_RETRY and self.playlist_file_due(pl):
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
                self._rename_blocked[str(src)] = time.time()
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
                self._m3u_blocked[pl["id"]] = time.time()
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

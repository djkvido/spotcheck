from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from . import fileops, sources
from .config import paths
from .errors import JobError
from .library import folder_name


class Placement:
    @staticmethod
    def set_file(tr: dict, path: Path, via: str) -> None:
        tr["file"] = path.relative_to(paths().downloads).as_posix()
        source = sources.file_source(path)
        tr["source"] = {**source, "via": via} if source else None
        (paths().covers / f"{tr['id']}.jpg").unlink(missing_ok=True)

    @staticmethod
    def sync_source(tr: dict, path: Path) -> None:
        source = sources.file_source(path)
        if (source or {}).get("id") != (tr.get("source") or {}).get("id"):
            tr["source"] = {**source, "via": "file"} if source else None

    free_path = staticmethod(fileops.free_path)
    reserve = staticmethod(fileops.reserve)
    release = staticmethod(fileops.release)

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
        target = self.reserve(folder / tmp.name)
        try:
            fileops.install(tmp, target)
        except OSError:
            self.release(target)
            raise
        finally:
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

    def restore_backup(self, pl: dict, tr: dict, chosen: dict) -> Path:
        stored = paths().data / chosen["file"]
        path = self.lib.abs_file(tr)
        entry = None
        if path is None:
            folder = self.lib.folder(pl)
            folder.mkdir(parents=True, exist_ok=True)
            stem = Path(tr["file"]).stem if tr.get("file") else folder_name(f"{tr['artist']} - {tr['name']}")
            target = self.reserve(folder / f"{stem}{stored.suffix}")
        else:
            entry = self.backup(tr, path, "before_restore")
            target = path.with_suffix(stored.suffix)
            if target != path:
                target = self.reserve(target)
        try:
            fileops.install_copy(stored, target)
        except OSError as exc:
            if entry is not None:
                self.drop_backup(tr, entry)
            if target != path:
                self.release(target)
            locked = isinstance(exc, fileops.FileLockedError)
            raise JobError("file_locked" if locked else "restore_failed") from exc
        if path is not None:
            self.remove_replaced(tr, path, target)
        tr["backups"].remove(chosen)
        stored.unlink(missing_ok=True)
        self.prune_backups(tr)
        self.set_file(tr, target, (chosen.get("source") or {}).get("via", "backup"))
        tr["trimmed"], tr["proposal"] = chosen.get("trimmed"), None
        restored_id = (tr.get("source") or {}).get("id")
        if restored_id in tr["rejected_ids"]:
            tr["rejected_ids"].remove(restored_id)
        self.lib.save()
        self.check_and_verify(pl, tr, target)
        return target

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TEMP_MARK = ".spotcheck-"
RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.2, 1.6, 2.0)


class FileLockedError(OSError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"File is in use: {path}")
        self.path = path


def temp_path(target: Path, tag: str) -> Path:
    return target.with_name(f"{target.stem}{TEMP_MARK}{tag}{target.suffix}")


def is_temp(path: Path) -> bool:
    return TEMP_MARK in path.name


def replace(src: Path, dst: Path) -> None:
    for delay in (*RETRY_DELAYS, None):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if delay is None:
                raise FileLockedError(dst) from exc
            time.sleep(delay)


def install(src: Path, dst: Path) -> None:
    staged = temp_path(dst, "new")
    shutil.move(str(src), str(staged))
    try:
        replace(staged, dst)
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def remove(path: Path) -> None:
    for delay in (*RETRY_DELAYS, None):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as exc:
            if delay is None:
                raise FileLockedError(path) from exc
            time.sleep(delay)


def reveal(target: Path) -> None:
    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            os.startfile(str(target))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target.is_file() else ["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target if target.is_dir() else target.parent)])


def remove_leftovers(folder: Path) -> int:
    removed = 0
    if folder.is_dir():
        for p in folder.glob(f"*{TEMP_MARK}*"):
            if p.is_file():
                p.unlink(missing_ok=True)
                removed += 1
    return removed

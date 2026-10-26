from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

TEMP_MARK = ".spotcheck-"
RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.2, 1.6, 2.0)

_reserve_lock = threading.Lock()


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


def free_path(target: Path) -> Path:
    candidate, n = target, 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        n += 1
    return candidate


def reserve(target: Path) -> Path:
    with _reserve_lock:
        while True:
            candidate = free_path(target)
            try:
                candidate.open("xb").close()
            except FileExistsError:
                continue
            return candidate


def release(reserved: Path) -> None:
    try:
        if reserved.stat().st_size == 0:
            reserved.unlink()
    except OSError:
        pass


def install(src: Path, dst: Path) -> None:
    staged = temp_path(dst, "new")
    try:
        shutil.move(str(src), str(staged))
        replace(staged, dst)
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def install_copy(src: Path, dst: Path) -> None:
    staged = temp_path(dst, "new")
    try:
        shutil.copy2(src, staged)
        replace(staged, dst)
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def previous_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def write_json(path: Path, data: Any, keep_previous: bool = False) -> None:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if keep_previous and path.exists():
            keep_copy(path, previous_path(path))
        replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def keep_copy(path: Path, previous: Path) -> None:
    try:
        previous.unlink(missing_ok=True)
        try:
            os.link(path, previous)
        except OSError:
            shutil.copy2(path, previous)
    except OSError:
        pass


def _read_text(path: Path) -> str:
    for delay in RETRY_DELAYS:
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(delay)
    return path.read_text(encoding="utf-8")


def read_json(path: Path, valid: Callable[[Any], bool] = lambda data: True) -> tuple[Any, Path | None]:
    for candidate in (path, previous_path(path)):
        try:
            data = json.loads(_read_text(candidate))
        except (FileNotFoundError, ValueError):
            continue
        if valid(data):
            return data, candidate
    return None, None


def quarantine(path: Path) -> Path | None:
    if not path.exists():
        return None
    target = path.with_name(f"{path.stem}.corrupt-{int(time.time())}{path.suffix}")
    shutil.copy2(path, target)
    return target


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

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import analysis, codecs, fileops
from .convert import (
    EMIT_INTERVAL,
    HISTORY,
    NAME_LIMIT,
    PRESET_BY_ID,
    PROGRESS_LINE,
    STALL_TIMEOUT,
    UNSAFE_CHARS,
    Target,
    build_command,
    capabilities,
    check_source,
    default_folder,
    formats,
    in_inbox,
    inbox,
    number,
    output_format,
    probe,
    progress_seconds,
    target_kind,
    validate_target,
    warnings,
)
from .errors import JobError

log = logging.getLogger(__name__)


class Converter:
    def __init__(self, bus) -> None:
        self.bus = bus
        self.cond = threading.Condition()
        self.items: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.workers = max(1, min(4, (os.cpu_count() or 2) // 4))
        self.threads: list[threading.Thread] = []
        self.codecs_job: dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": None}
        self._codecs_cancel = False
        self._closing = False

    @staticmethod
    def public_item(item: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in item.items() if not k.startswith("_")}

    def public(self) -> list[dict[str, Any]]:
        with self.cond:
            return [self.public_item(self.items[i]) for i in self.order]

    def busy(self) -> bool:
        with self.cond:
            return any(i["state"] in ("queued", "running") for i in self.items.values())

    def _emit(self, item: dict[str, Any], force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - item.get("_emitted", 0) < EMIT_INTERVAL:
            return
        item["_emitted"] = now
        self.bus.emit({"type": "convert", "item": self.public_item(item)})

    def _ensure_workers(self) -> None:
        self.threads = [t for t in self.threads if t.is_alive()]
        while len(self.threads) < self.workers:
            thread = threading.Thread(target=self._worker, name=f"spotcheck-convert-{len(self.threads)}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def add(self, entries: list[dict[str, Any]], raw_target: dict[str, Any], folder: Path | None) -> list[str]:
        caps = capabilities()
        if not caps["ffmpeg"]:
            raise JobError("ffmpeg_missing")
        target = Target.from_dict(raw_target)
        containers = None if target.preset else {c["id"] for c in formats()["containers"]}
        validate_target(target, caps, containers)
        prepared = []
        for entry in entries:
            src = Path(entry["path"])
            info = entry.get("info") or probe(src)
            check_source(info, target)
            ext, muxer = output_format(target, info)
            dest_folder = folder or (default_folder() if in_inbox(src) else src.parent)
            prepared.append((src, info, ext, muxer, dest_folder))
        ids = []
        with self.cond:
            for src, info, ext, muxer, dest_folder in prepared:
                item = {
                    "id": uuid.uuid4().hex[:10],
                    "name": src.name,
                    "source": str(src),
                    "folder": str(dest_folder),
                    "output": None,
                    "ext": ext,
                    "target": raw_target,
                    "kind": target_kind(target),
                    "state": "queued",
                    "progress": 0.0,
                    "speed": None,
                    "eta": None,
                    "error": None,
                    "detail": None,
                    "warnings": warnings(info, target),
                    "encoder": None,
                    "size_in": _size(src),
                    "size_out": None,
                    "duration": info.get("duration"),
                    "created": time.time(),
                    "started": None,
                    "finished": None,
                    "inbox": in_inbox(src),
                    "_target": target,
                    "_info": info,
                    "_muxer": muxer,
                    "_cancel": False,
                    "_proc": None,
                    "_emitted": 0.0,
                }
                self.items[item["id"]] = item
                self.order.append(item["id"])
                ids.append(item["id"])
            self._prune()
            self._ensure_workers()
            self.cond.notify_all()
        for item_id in ids:
            self._emit(self.items[item_id])
        return ids

    def _prune(self) -> None:
        excess = len(self.order) - HISTORY
        if excess <= 0:
            return
        for item_id in list(self.order):
            if excess <= 0:
                break
            if self.items[item_id]["state"] in ("done", "failed", "cancelled"):
                self._drop(item_id)
                excess -= 1

    def _drop(self, item_id: str) -> None:
        item = self.items.pop(item_id)
        self.order.remove(item_id)
        if item["inbox"]:
            source = Path(item["source"])
            if not any(other["source"] == item["source"] for other in self.items.values()):
                shutil.rmtree(source.parent, ignore_errors=True)

    def _next(self) -> dict[str, Any] | None:
        video_running = any(i["state"] == "running" and i["kind"] == "video" for i in self.items.values())
        for item_id in self.order:
            item = self.items[item_id]
            if item["state"] != "queued":
                continue
            if item["kind"] == "video" and video_running:
                continue
            return item
        return None

    def _worker(self) -> None:
        while True:
            with self.cond:
                item = self._next()
                while item is None and not self._closing:
                    self.cond.wait(30)
                    item = self._next()
                if self._closing:
                    return
                item["state"], item["started"] = "running", time.time()
            self._emit(item)
            try:
                self._convert(item)
            except Exception as exc:
                log.exception("Conversion of %s failed", item["source"])
                item["state"], item["error"], item["detail"] = "failed", "convert_failed", str(exc)[:300]
            item["finished"] = time.time()
            item["_proc"] = None
            self._emit(item)
            with self.cond:
                self.cond.notify_all()

    def _convert(self, item: dict[str, Any]) -> None:
        caps = capabilities()
        src = Path(item["source"])
        if not src.is_file():
            item["state"], item["error"] = "failed", "file_not_found"
            return
        folder = Path(item["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        stem = UNSAFE_CHARS.sub("_", src.stem).strip(" .")[:NAME_LIMIT] or "file"
        final = fileops.reserve(folder / f"{stem}.{item['ext']}")
        item["output"] = str(final)
        temp = fileops.temp_path(final, f"convert-{item['id']}")
        outcome = "failed"
        try:
            outcome = self._encode(item, caps, src, temp, hardware=True)
            if outcome == "retry":
                self.bus.log("warn", "convert_hw_fallback", file=src.name, encoder=item["encoder"])
                outcome = self._encode(item, caps, src, temp, hardware=False)
            if outcome == "done" and (not temp.is_file() or temp.stat().st_size == 0):
                outcome, item["error"] = "failed", "empty_output"
            if outcome == "done":
                try:
                    fileops.replace(temp, final)
                except OSError:
                    outcome, item["error"] = "failed", "file_locked"
            if outcome == "done":
                item["progress"], item["eta"], item["size_out"] = 1.0, 0, _size(final)
                self.bus.log("info", "convert_done", file=final.name)
        finally:
            temp.unlink(missing_ok=True)
            if outcome != "done":
                fileops.release(final)
                item["output"] = None
            item["state"] = outcome

    def _encode(self, item: dict[str, Any], caps: dict[str, Any], src: Path, temp: Path, hardware: bool) -> str:
        target: Target = item["_target"]
        info = item["_info"]
        hw = caps["hardware"] if hardware else {}
        cmd, used_hw = build_command(caps["ffmpeg"], src, temp, target, info, hw, item["_muxer"])
        item["encoder"] = used_hw
        start = target.start or 0.0
        end = target.end if target.end is not None else info.get("duration")
        total = max((end or 0) - start, 0) or None
        values: dict[str, str] = {}
        errors: list[str] = []
        seen = [time.monotonic()]
        begun = time.monotonic()
        stalled = threading.Event()
        finished = threading.Event()

        def on_start(proc: subprocess.Popen) -> None:
            item["_proc"] = proc
            if item["_cancel"]:
                analysis.kill_tree(proc)

        def on_line(line: str) -> None:
            seen[0] = time.monotonic()
            m = PROGRESS_LINE.match(line.strip())
            if not m:
                if line.strip():
                    errors.append(line.strip()[:300])
                    del errors[:-12]
                return
            values[m.group(1)] = m.group(2)
            if m.group(1) != "progress":
                return
            done = progress_seconds(values)
            if total and done is not None:
                item["progress"] = round(min(done / total, 1.0), 4)
                elapsed = time.monotonic() - begun
                if item["progress"] > 0.01 and elapsed > 2:
                    item["eta"] = round(elapsed * (1 - item["progress"]) / item["progress"])
            speed = values.get("speed", "").rstrip("x")
            item["speed"] = number(speed)
            self._emit(item, force=False)

        def watchdog() -> None:
            while not finished.wait(15):
                if time.monotonic() - seen[0] > STALL_TIMEOUT and item.get("_proc") is not None:
                    stalled.set()
                    analysis.kill_tree(item["_proc"])
                    return

        guard = threading.Thread(target=watchdog, name="spotcheck-convert-watch", daemon=True)
        guard.start()
        try:
            result = analysis.run_streaming(cmd, on_line, on_start=on_start)
        except (OSError, subprocess.SubprocessError) as exc:
            item["error"], item["detail"] = "convert_failed", str(exc)[:300]
            return "failed"
        finally:
            finished.set()
            item["_proc"] = None
        if item["_cancel"]:
            return "cancelled"
        if stalled.is_set():
            item["error"] = "convert_stalled"
            return "failed"
        if result.returncode != 0:
            if used_hw:
                return "retry"
            item["error"] = "copy_incompatible" if _copying(target) else "convert_failed"
            item["detail"] = "\n".join(errors[-6:]) or None
            return "failed"
        return "done"

    def cancel(self, item_id: str) -> bool:
        with self.cond:
            item = self.items.get(item_id)
            if not item or item["state"] not in ("queued", "running"):
                return False
            item["_cancel"] = True
            if item["state"] == "queued":
                item["state"], item["finished"] = "cancelled", time.time()
            proc = item.get("_proc")
        if proc is not None:
            analysis.kill_tree(proc)
        self._emit(item)
        return True

    def cancel_all(self) -> int:
        with self.cond:
            ids = [i for i, item in self.items.items() if item["state"] in ("queued", "running")]
        return sum(self.cancel(i) for i in ids)

    def retry(self, item_id: str) -> str:
        with self.cond:
            item = self.items.get(item_id)
            if not item or item["state"] not in ("failed", "cancelled"):
                raise JobError("convert_not_found")
        folder = Path(item["folder"])
        entry = {"path": item["source"], "info": item["_info"]}
        if not Path(item["source"]).is_file():
            raise JobError("file_not_found")
        new = self.add([entry], item["target"], folder)
        with self.cond:
            if item_id in self.items:
                self._drop(item_id)
        self.bus.emit({"type": "convert_list"})
        return new[0]

    def clear(self) -> int:
        with self.cond:
            ids = [i for i in self.order if self.items[i]["state"] in ("done", "failed", "cancelled")]
            for item_id in ids:
                self._drop(item_id)
        self.bus.emit({"type": "convert_list"})
        return len(ids)

    def discard(self, paths: list[str]) -> int:
        removed = 0
        with self.cond:
            used = {i["source"] for i in self.items.values()}
        for raw in paths:
            path = Path(raw)
            if raw not in used and in_inbox(path) and path.parent.parent == inbox():
                shutil.rmtree(path.parent, ignore_errors=True)
                removed += 1
        return removed

    def find(self, item_id: str) -> dict[str, Any] | None:
        with self.cond:
            return self.items.get(item_id)

    def close(self, timeout: float = 10) -> None:
        self._codecs_cancel = True
        with self.cond:
            self._closing = True
            self.cond.notify_all()
        self.cancel_all()
        deadline = time.monotonic() + timeout
        with self.cond:
            while any(i["state"] == "running" for i in self.items.values()) and time.monotonic() < deadline:
                self.cond.wait(0.2)

    def install_codecs(self) -> bool:
        with self.cond:
            if self.codecs_job["state"] == "running":
                return False
            self.codecs_job = {"state": "running", "done": 0, "total": 0, "error": None}
            self._codecs_cancel = False
        threading.Thread(target=self._codecs_worker, name="spotcheck-codecs", daemon=True).start()
        self._emit_codecs()
        return True

    def cancel_codecs(self) -> bool:
        if self.codecs_job["state"] != "running":
            return False
        self._codecs_cancel = True
        return True

    def _emit_codecs(self) -> None:
        self.bus.emit({"type": "codecs", "job": dict(self.codecs_job), "status": codecs.status()})

    def _codecs_worker(self) -> None:
        last = [0.0]

        def progress(done: int, total: int) -> None:
            self.codecs_job.update(done=done, total=total)
            if time.monotonic() - last[0] > EMIT_INTERVAL:
                last[0] = time.monotonic()
                self._emit_codecs()

        try:
            codecs.install(progress, lambda: self._codecs_cancel)
            capabilities(refresh=True)
            self.codecs_job["state"] = "done"
            self.bus.log("info", "codecs_installed")
        except JobError as exc:
            self.codecs_job.update(state="failed", error=exc.code)
            self.bus.log("error", exc.code)
        except Exception as exc:
            if self._codecs_cancel:
                self.codecs_job["state"] = "cancelled"
            else:
                log.exception("Installing the extended codecs failed")
                self.codecs_job.update(state="failed", error="codecs_failed")
                self.bus.log("error", "codecs_failed", error=str(exc)[:300])
        self._emit_codecs()


def _copying(target: Target) -> bool:
    preset = PRESET_BY_ID.get(target.preset or "")
    return bool(preset and preset.copy) or "copy" in (target.video, target.audio)


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None

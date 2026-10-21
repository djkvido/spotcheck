from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, analysis, deps, diagnostics, fileops, origins, projects, sources, terms
from . import settings as settings_mod
from .config import STATIC_DIR, paths
from .engine import Bus, Engine
from .library import Library, playlist_id

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CSRF_HEADER = "x-spotcheck"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}
OPEN_BEFORE_TERMS = {"/api/terms", "/api/quit"}
FLAGS = ("download", "validate", "verify", "fix", "trim", "force_fix")
MAX_RANGE = 4 * 1024 * 1024
EVENT_QUEUE = 2000
PICKER_TIMEOUT = 900


class UrlsRequest(BaseModel):
    urls: str
    follow_up: bool = False


class SyncRequest(BaseModel):
    playlist_ids: list[str] | None = None
    follow_up: bool = False


class ProcessRequest(BaseModel):
    flags: dict[str, bool] = Field(default_factory=dict)
    only: Literal["missing", "problems", "files"] | None = None
    playlist_id: str | None = None
    track_ids: list[str] | None = None
    label: str = Field("process", pattern=r"^[a-z_]{1,40}$")


class InstallRequest(BaseModel):
    action: Literal["update-python", "ffmpeg", "deno", "all"] = "all"


class TermsRequest(BaseModel):
    version: str


class RestoreRequest(BaseModel):
    index: int = Field(0, ge=0)


class OpenRequest(BaseModel):
    track_id: str | None = None
    playlist_id: str | None = None


class ProjectRequest(BaseModel):
    name: str
    music: str
    playlists: list[str] = Field(default_factory=list)
    settings: dict[str, Any] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    music: str | None = None


class FolderRequest(BaseModel):
    initial: str | None = None


class ScopeRequest(BaseModel):
    playlist_id: str | None = None
    track_ids: list[str] | None = None


class ExportRequest(BaseModel):
    playlist_ids: list[str] | None = None
    mode: Literal["music", "copy"] = "copy"
    target: str = ""
    number: bool = False
    reveal: bool = True


def split_urls(text: str) -> tuple[list[str], list[str]]:
    valid: dict[str, str] = {}
    rejected = []
    for url in (u for u in re.split(r"[\s,]+", text) if u):
        if origins.classify(url):
            valid.setdefault(playlist_id(url), origins.canonical_url(url))
        elif origins.classify_track(url):
            valid.setdefault("track:" + origins.classify_track(url)[1], origins.canonical_track_url(url))
        else:
            rejected.append(url)
    return list(valid.values()), rejected


def export_target_error(raw: str, music: Path) -> str | None:
    if not raw.strip():
        return "required"
    target = Path(raw.strip()).expanduser()
    if not target.is_absolute():
        return "folder_not_absolute"
    if target.exists() and not target.is_dir():
        return "folder_not_directory"
    resolved, music = target.resolve(), music.resolve()
    if resolved == music or music in resolved.parents:
        return "folder_inside_music"
    existing = next((p for p in (resolved, *resolved.parents) if p.exists()), None)
    if existing is None or not os.access(existing, os.W_OK):
        return "folder_not_writable"
    return None


def read_range(path: Path, header: str | None) -> tuple[int, int, int, bytes] | None:
    size = path.stat().st_size
    m = re.match(r"bytes=(\d*)-(\d*)$", header or "")
    if not (m and (m.group(1) or m.group(2))):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:
        start, end = max(size - int(m.group(2)), 0), size - 1
    end = min(end, size - 1, start + MAX_RANGE - 1)
    if start > end:
        return size, start, end, b""
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(end - start + 1)
    return size, start, end, data


async def ranged_file(path: Path, request: Request) -> Response:
    media = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    header = request.headers.get("range")
    found = await asyncio.to_thread(read_range, path, header)
    if found is None:
        return FileResponse(path, media_type=media, headers={"Accept-Ranges": "bytes"})
    size, start, end, data = found
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    if not data:
        return Response(data, media_type=media, headers={"Accept-Ranges": "bytes"})
    headers = {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}"}
    return Response(data, status_code=206, media_type=media, headers=headers)


_picker_lock = threading.Lock()


def apple_script_folder(start: str) -> list[str]:
    location = start.replace("\\", "\\\\").replace('"', '\\"')
    return ["osascript", "-e", f'POSIX path of (choose folder default location POSIX file "{location}")']


def pick_folder(initial: str | None) -> str | None:
    start = initial if initial and Path(initial).is_dir() else str(Path.home())
    with _picker_lock:
        if sys.platform == "darwin":
            try:
                result = analysis.run_text(apple_script_folder(start), timeout=PICKER_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                return None
            chosen = result.stdout.strip() if result.returncode == 0 else ""
        else:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                chosen = filedialog.askdirectory(parent=root, initialdir=start, mustexist=False)
            finally:
                root.destroy()
    return str(Path(chosen)) if chosen else None


def folder_picker_available() -> bool:
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    return importlib.util.find_spec("tkinter") is not None


def project_error(exc: projects.ProjectError) -> HTTPException:
    if not exc.field:
        return HTTPException(404 if exc.code == "project_not_found" else 409, exc.code)
    return HTTPException(422, {"fields": {exc.field: {"code": exc.code, "params": exc.params}}})


def create_app(automation: bool = True) -> FastAPI:
    projects.activate(projects.current())
    bus = Bus()

    def on_save() -> None:
        bus.emit({"type": "saved", "t": engine.lib.saved_at})

    engine = Engine(Library(on_save=on_save), bus)

    def lib() -> Library:
        return engine.lib

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        bus.loop = asyncio.get_running_loop()
        await asyncio.to_thread(lib().link_files)
        if automation:
            engine.start_automation()
        yield
        await asyncio.to_thread(lib().save)

    app = FastAPI(
        title="SpotCheck", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Host not allowed"}, status_code=400)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"Missing {CSRF_HEADER} header"}, status_code=403)
        mutating = request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/")
        if mutating and request.url.path not in OPEN_BEFORE_TERMS and not terms.accepted():
            return JSONResponse({"detail": "terms_required"}, status_code=403)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def track_or_404(key: str) -> tuple[dict, dict]:
        pl, tr = lib().find_track(key)
        if tr is None:
            raise HTTPException(404, "track_not_found")
        return pl, tr

    def require_tracks_idle(keys: list[str] | None) -> None:
        if not keys:
            return
        index = lib().track_index()
        for key in keys:
            tr = index[key][1] if key in index else track_or_404(key)[1]
            require_track_idle(tr)

    def require_idle() -> None:
        if engine.busy():
            raise HTTPException(409, "busy")

    def require_track_idle(tr: dict) -> None:
        if tr.get("step"):
            raise HTTPException(409, "track_busy")

    async def switch_to(project: dict[str, Any]) -> None:
        require_idle()
        projects.activate(project)
        engine.reset(Library(on_save=on_save))
        await asyncio.to_thread(lib().link_files)
        bus.emit({"type": "project"})

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    def snapshot() -> dict[str, Any]:
        return {
            "project": projects.current(),
            "library": lib().public(),
            "settings_meta": settings_mod.meta(),
            "jobs": engine.jobs_public(),
            "logs": list(bus.logs)[-150:],
            "ffmpeg": analysis.ffmpeg_info(),
            "version": __version__,
            "terms": terms.public(),
        }

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return await asyncio.to_thread(snapshot)

    @app.post("/api/terms")
    async def accept_terms(body: TermsRequest) -> dict[str, Any]:
        if body.version != terms.VERSION:
            raise HTTPException(409, "terms_outdated")
        terms.accept()
        engine.rescan_soon()
        return terms.public()

    @app.post("/api/quit")
    async def quit_app(request: Request) -> dict[str, Any]:
        shutdown = getattr(request.app.state, "shutdown", None)
        if shutdown is None:
            raise HTTPException(501, "quit_unavailable")
        asyncio.get_running_loop().call_later(0.3, shutdown)
        return {"ok": True, "pid": os.getpid()}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        q: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE)
        bus.subscribers.add(q)

        async def stream():
            try:
                yield "retry: 2000\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(q.get(), 15)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                bus.subscribers.discard(q)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.get("/api/projects")
    async def list_projects() -> dict[str, Any]:
        registry = projects.load()
        items = await asyncio.to_thread(lambda: [projects.summary(p) for p in registry["projects"]])
        return {"current": registry["current"], "projects": items, "folder_picker": folder_picker_available()}

    @app.get("/api/projects/suggest")
    async def suggest_folder(name: str = "") -> dict[str, str]:
        return {"music": projects.default_music(name)}

    @app.post("/api/projects")
    async def create_project(body: ProjectRequest) -> dict[str, Any]:
        require_idle()
        urls, _ = split_urls("\n".join(body.playlists))
        if body.settings is not None:
            errors = settings_mod.validate(body.settings, settings_mod.defaults())
            if errors:
                raise HTTPException(422, {"fields": {"settings": next(iter(errors.values()))}})
        try:
            project = projects.create(body.name, body.music)
        except projects.ProjectError as exc:
            raise project_error(exc) from exc
        await switch_to(project)
        if body.settings is not None:
            lib().update_settings(body.settings)
        job = engine.submit("sync", "sync", {"count": len(urls)}, urls=urls) if urls else None
        return {"project": project, "job": job}

    @app.post("/api/projects/{project_id}/open")
    async def open_project(project_id: str) -> dict[str, Any]:
        project = projects.get(project_id)
        if project is None:
            raise HTTPException(404, "project_not_found")
        await switch_to(project)
        return {"project": project}

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: str, body: ProjectUpdate) -> dict[str, Any]:
        is_current = projects.current()["id"] == project_id
        if body.music is not None and is_current:
            require_idle()
        try:
            project = projects.update(project_id, name=body.name, music=body.music)
        except projects.ProjectError as exc:
            raise project_error(exc) from exc
        if is_current and body.music is not None:
            await switch_to(project)
        else:
            bus.emit({"type": "project"})
        return {"project": project}

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, bool]:
        try:
            projects.remove(project_id)
        except projects.ProjectError as exc:
            raise project_error(exc) from exc
        return {"ok": True}

    @app.post("/api/pick-folder")
    async def choose_folder(body: FolderRequest) -> dict[str, str | None]:
        if not folder_picker_available():
            raise HTTPException(501, "folder_picker_unavailable")
        return {"path": await asyncio.to_thread(pick_folder, body.initial)}

    @app.get("/api/deps")
    async def get_deps() -> dict[str, Any]:
        return await asyncio.to_thread(deps.status)

    @app.get("/api/diagnostics")
    async def get_diagnostics(network: bool = False) -> dict[str, str]:
        events = [diagnostics.format_event(event, stamp=True) for event in list(bus.logs)[-40:]]
        return {"text": await asyncio.to_thread(diagnostics.report, network, events)}

    @app.post("/api/deps/install")
    async def install(body: InstallRequest) -> dict[str, str]:
        return {"job": engine.submit("deps", "deps", {"action": body.action}, action=body.action)}

    @app.post("/api/playlists")
    async def add_playlists(body: UrlsRequest) -> dict[str, Any]:
        urls, rejected = split_urls(body.urls)
        if not urls:
            raise HTTPException(400, "invalid_playlist_url")
        job = engine.submit("sync", "sync", {"count": len(urls)}, urls=urls, follow_up=body.follow_up)
        return {"job": job, "rejected": rejected}

    @app.post("/api/playlists/sync")
    async def sync_playlists(body: SyncRequest) -> dict[str, str]:
        known = [p["id"] for p in lib().data["playlists"]]
        ids = known if body.playlist_ids is None else [i for i in body.playlist_ids if i in known]
        if not ids:
            raise HTTPException(404, "playlist_not_found")
        if len(ids) == 1:
            label, params = "refresh", {"name": lib().playlist(ids[0])["name"]}
        else:
            label, params = "refresh_all", {"count": len(ids)}
        return {"job": engine.submit("sync", label, params, playlist_ids=ids, follow_up=body.follow_up)}

    @app.delete("/api/tracks/{key}")
    async def remove_single(key: str) -> dict[str, bool]:
        _, tr = track_or_404(key)
        require_track_idle(tr)
        if not lib().remove_single(key):
            raise HTTPException(400, "not_a_single")
        bus.emit({"type": "library"})
        return {"ok": True}

    @app.delete("/api/playlists/{pid}")
    async def delete_playlist(pid: str) -> dict[str, bool]:
        require_idle()
        if not lib().playlist(pid):
            raise HTTPException(404, "playlist_not_found")
        lib().remove_playlist(pid)
        bus.emit({"type": "library"})
        return {"ok": True}

    @app.post("/api/process")
    async def process(body: ProcessRequest) -> dict[str, str]:
        flags = {k: bool(body.flags.get(k)) for k in FLAGS}
        if not any(flags[k] for k in FLAGS if k != "force_fix"):
            raise HTTPException(400, "no_action")
        if body.playlist_id and not lib().playlist(body.playlist_id):
            raise HTTPException(404, "playlist_not_found")
        require_tracks_idle(body.track_ids)
        return {
            "job": engine.submit(
                "process",
                body.label,
                None,
                playlist_id=body.playlist_id,
                track_ids=body.track_ids,
                only=body.only,
                flags=flags,
            )
        }

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> dict[str, bool]:
        return {"ok": engine.cancel(job_id)}

    @app.put("/api/settings")
    async def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        errors = settings_mod.validate(body, lib().settings)
        if errors:
            raise HTTPException(422, {"fields": errors})
        result = lib().update_settings(body)
        bus.emit({"type": "settings", "settings": result})
        engine.rescan_soon()
        return result

    @app.post("/api/settings/reset")
    async def reset_settings() -> dict[str, Any]:
        result = lib().reset_settings()
        bus.emit({"type": "settings", "settings": result})
        engine.rescan_soon()
        return result

    @app.post("/api/tags")
    async def fix_tags(body: ScopeRequest) -> dict[str, str]:
        if body.playlist_id and not lib().playlist(body.playlist_id):
            raise HTTPException(404, "playlist_not_found")
        require_tracks_idle(body.track_ids)
        items = engine.select(body.playlist_id, body.track_ids, "files")
        count = sum(1 for _, tr in items if (tr.get("check") or {}).get("tags_missing"))
        if not count:
            raise HTTPException(400, "no_tags_missing")
        job = engine.submit("tags", "tags", {"count": count}, playlist_id=body.playlist_id, track_ids=body.track_ids)
        return {"job": job}

    @app.post("/api/export")
    async def export(body: ExportRequest) -> dict[str, Any]:
        known = [p["id"] for p in lib().data["playlists"]]
        ids = known if body.playlist_ids is None else [i for i in body.playlist_ids if i in known]
        if not ids:
            raise HTTPException(404, "playlist_not_found")
        if body.mode == "music":

            def write() -> int:
                with lib().lock:
                    written = 0
                    for pid in ids:
                        pl = lib().playlist(pid)
                        written += engine.write_playlist_file(pl, force=True)
                    lib().save()
                    return written

            written = await asyncio.to_thread(write)
            if body.reveal:
                folder = lib().folder(lib().playlist(ids[0])) if len(ids) == 1 else paths().downloads
                if folder.exists():
                    fileops.reveal(folder)
            return {"written": written, "job": None}
        error = export_target_error(body.target, paths().downloads)
        if error:
            raise HTTPException(422, {"fields": {"target": {"code": error, "params": {}}}})
        target = str(Path(body.target.strip()).expanduser().resolve())
        params = {"count": len(ids), "folder": target}
        job = engine.submit(
            "export", "export", params, playlist_ids=ids, target=target, number=body.number, reveal=body.reveal
        )
        return {"job": job}

    @app.post("/api/tracks/{key}/proposal/apply")
    async def apply_proposal(key: str) -> dict[str, str]:
        _, tr = track_or_404(key)
        require_track_idle(tr)
        if not tr.get("proposal"):
            raise HTTPException(404, "proposal_not_found")
        return {"job": engine.submit("apply", "apply", {"name": tr["name"]}, key=key)}

    @app.post("/api/tracks/{key}/proposal/reject")
    async def reject_proposal(key: str) -> dict[str, bool]:
        pl, tr = track_or_404(key)
        require_track_idle(tr)
        if not tr.get("proposal"):
            raise HTTPException(404, "proposal_not_found")
        tr["rejected_ids"].append(tr["proposal"]["id"])
        tr["proposal"] = None
        engine.refresh_notes(tr)
        engine.touch(pl, tr)
        lib().save()
        return {"ok": True}

    @app.post("/api/tracks/{key}/restore")
    async def restore(key: str, body: RestoreRequest) -> dict[str, str]:
        _, tr = track_or_404(key)
        require_track_idle(tr)
        if body.index >= len(tr["backups"]):
            raise HTTPException(404, "backup_not_found")
        return {"job": engine.submit("restore", "restore", {"name": tr["name"]}, key=key, index=body.index)}

    @app.get("/api/audio/{key}")
    async def audio(
        key: str,
        request: Request,
        src: Literal["file", "start", "match", "preview", "proposal", "proposal_match", "backup"] = "file",
        index: int = 0,
    ) -> Response:
        _, tr = track_or_404(key)
        verify = tr.get("verify") or {}
        if src == "preview":
            preview = verify.get("ref")
            file = paths().previews / preview["file"] if preview else None
            if file is None or not file.exists():
                preview = await asyncio.to_thread(sources.get_preview, tr, lib().settings)
                file = paths().previews / preview["file"] if preview else None
            if file is None or not file.exists():
                raise HTTPException(404, "preview_unavailable")
            return await ranged_file(file, request)
        if src in ("proposal", "proposal_match"):
            proposal = tr.get("proposal") or {}
            file = paths().candidates / proposal["audio"] if proposal.get("audio") else None
            if file is None or not file.exists():
                raise HTTPException(404, "proposal_audio_unavailable")
            if src == "proposal":
                return await ranged_file(file, request)
            data = await asyncio.to_thread(analysis.segment_mp3, file, float(proposal.get("at") or 0), 30)
            return Response(data, media_type="audio/mpeg")
        if src == "backup":
            if not 0 <= index < len(tr["backups"]):
                raise HTTPException(404, "backup_not_found")
            file = paths().data / tr["backups"][index]["file"]
            if not file.exists():
                raise HTTPException(404, "backup_file_missing")
            return await ranged_file(file, request)
        file = lib().abs_file(tr)
        if file is None:
            raise HTTPException(404, "file_missing")
        if src == "match":
            data = await asyncio.to_thread(
                analysis.segment_mp3, file, float(verify.get("at", 0)), float(verify.get("window", 30))
            )
            return Response(data, media_type="audio/mpeg")
        if src == "start":
            return Response(await asyncio.to_thread(analysis.segment_mp3, file, 0, 30), media_type="audio/mpeg")
        return await ranged_file(file, request)

    @app.get("/api/cover/{key}")
    async def cover(key: str) -> Response:
        _, tr = track_or_404(key)
        cached = paths().covers / f"{tr['id']}.jpg"
        headers = {"Cache-Control": "max-age=86400"}
        if cached.exists():
            return FileResponse(cached, media_type="image/jpeg", headers=headers)
        file = lib().abs_file(tr)
        data = await asyncio.to_thread(analysis.cover_jpeg, file) if file else b""
        if not data:
            raise HTTPException(404, "cover_unavailable")
        cached.write_bytes(data)
        return Response(data, media_type="image/jpeg", headers=headers)

    @app.post("/api/open")
    async def open_in_file_manager(body: OpenRequest) -> dict[str, bool]:
        if body.track_id:
            _, tr = track_or_404(body.track_id)
            target = lib().abs_file(tr)
        elif body.playlist_id:
            pl = lib().playlist(body.playlist_id)
            target = lib().folder(pl) if pl else None
        else:
            target = paths().downloads
        if target is None or not target.exists():
            raise HTTPException(404, "path_not_found")
        try:
            fileops.reveal(target)
        except OSError as exc:
            raise HTTPException(500, "open_failed") from exc
        return {"ok": True}

    return app

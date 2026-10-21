from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, analysis, config, deps, diagnostics, fileops, runtime
from .config import configure

LOOPBACK = ("127.0.0.1", "localhost", "::1")
SETUP_MESSAGES = {
    "command": "$ {text}",
    "output": "{text}",
    "command_failed": "Command failed: {error}",
    "ffmpeg_ready": "FFmpeg with chromaprint is already installed.",
    "ffmpeg_not_on_path": "FFmpeg was installed but is not on PATH yet. Open a new terminal and run setup again.",
    "ffmpeg_no_chromaprint": "This FFmpeg build has no chromaprint support. Install a full build (see README).",
}


def run_setup() -> int:
    from . import deps

    def log(level: str, code: str, **params: str) -> None:
        prefix = "  ! " if level in ("warn", "error") else "    "
        print(prefix + SETUP_MESSAGES.get(code, code).format(**params))

    print("SpotCheck: checking dependencies\n")
    if not runtime.packaged():
        deps.ensure_spotdl(log)
    state = deps.status(check_updates=False)
    for item in state["items"]:
        mark = "ok     " if item["installed"] else "missing"
        print(f"  [{mark}] {item['name']:<14} {item['version'] or ''}")
    print()
    present = {i["key"]: i["installed"] for i in state["items"]}
    if not runtime.packaged():
        if not (present["ffmpeg"] and present["chromaprint"]):
            print("Installing FFmpeg with chromaprint support...")
            deps.install("ffmpeg", log)
        if not present["deno"]:
            print("Downloading Deno...")
            deps.install("deno", log)
        state = deps.status(check_updates=False)
    required = {"spotdl", "yt-dlp", "ffmpeg", "chromaprint"}
    ready = all(i["installed"] for i in state["items"] if i["key"] in required)
    print("\n" + ("All set." if ready else "Some dependencies are missing; see the Dependencies panel in the app."))
    return 0 if ready else 1


def running_instance(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/state", timeout=2) as r:
            return "library" in json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return False


def instance_file() -> Path:
    return config.paths().home / "instance.json"


def active_port(host: str, preferred: int) -> int | None:
    ports = [preferred]
    try:
        stored = json.loads(instance_file().read_text(encoding="utf-8")).get("port")
    except (OSError, ValueError, AttributeError):
        stored = None
    if isinstance(stored, int) and stored != preferred:
        ports.append(stored)
    return next((port for port in ports if running_instance(host, port)), None)


def remember_instance(port: int) -> None:
    with contextlib.suppress(OSError):
        fileops.write_json(instance_file(), {"pid": os.getpid(), "port": port})


def forget_instance() -> None:
    try:
        if json.loads(instance_file().read_text(encoding="utf-8")).get("pid") == os.getpid():
            instance_file().unlink()
    except (OSError, ValueError, AttributeError):
        pass


def request_quit(host: str, port: int) -> int | None:
    request = urllib.request.Request(
        f"http://{host}:{port}/api/quit", method="POST", data=b"{}", headers={"X-SpotCheck": "1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return int(json.loads(r.read().decode("utf-8")).get("pid") or 0)
    except (OSError, ValueError):
        return None


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def quit_running(host: str, port: int, timeout: float = 15) -> bool:
    pid = request_quit(host, port)
    if pid is None:
        return not running_instance(host, port)
    deadline = time.monotonic() + timeout
    while pid and process_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def free_port(host: str, preferred: int, attempts: int = 20) -> int | None:
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    return None


def check_playlist(url: str) -> int:
    from .engine import Bus, Engine
    from .library import Library

    bus = Bus()
    name, songs = Engine(Library(), bus).load_playlist(url)
    for event in bus.logs:
        print(diagnostics.format_event(event))
    print(f"{name}: {len(songs)} tracks")
    return 0 if songs else 1


def check_files(lib, every_file: bool) -> int:
    from .engine import TERMINAL_STATES, Bus, Engine
    from .library import track_key

    engine = Engine(lib, Bus())
    keys = [track_key(pl, tr) for pl, tr in engine.select(only="files")] if every_file else lib.changed_tracks()
    if not keys:
        print("No files need a check.", file=sys.stderr)
        return 0
    flags = {"validate": True, "verify": True}
    job_id = engine.submit("process", "check", {"count": len(keys)}, track_ids=keys, flags=flags)
    job = engine.jobs[job_id]
    shown, reported = -1, time.monotonic()
    try:
        while job["state"] not in TERMINAL_STATES:
            time.sleep(0.5)
            if job["done"] != shown and time.monotonic() - reported >= 5:
                shown, reported = job["done"], time.monotonic()
                print(f"Checked {job['done']} of {job['total']} files", file=sys.stderr)
    except KeyboardInterrupt:
        engine.cancel(job_id)
        while job["state"] not in TERMINAL_STATES:
            time.sleep(0.2)
        raise
    print(f"Checked {job['done']} of {job['total']} files", file=sys.stderr)
    return int(job["summary"].get("failed") or 0)


def readable_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace") if stream.isatty() else stream.reconfigure(encoding="utf-8")


def headless(args: argparse.Namespace) -> int:
    from . import projects, report, terms
    from .library import Library

    if active_port(args.host, args.port) is not None:
        print("SpotCheck is running. Close it first, or use the app.", file=sys.stderr)
        return 1
    if not terms.accepted():
        print("Open SpotCheck once and accept the terms of use first.", file=sys.stderr)
        return 1
    project = projects.current()
    projects.activate(project)
    lib = Library()
    lib.link_files()
    failed = 0
    if args.check:
        try:
            failed = check_files(lib, args.check == "all")
        except KeyboardInterrupt:
            return 130
    data = report.summary(lib, project)
    print(report.render(data, args.report or "text"))
    return 1 if failed else report.exit_code(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="spotcheck",
        description="Keep a local music library in order: load playlists, find their tracks and verify every file.",
    )
    parser.add_argument("--home", help="workspace directory (default: $SPOTCHECK_HOME or the current directory)")
    parser.add_argument(
        "--host", default="127.0.0.1", choices=LOOPBACK, help="local address to listen on (default: 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--setup", action="store_true", help="check and install missing dependencies, then exit")
    parser.add_argument("--quit", action="store_true", help="stop a running SpotCheck, then exit")
    parser.add_argument("--diagnostics", action="store_true", help="print a report about the installation, then exit")
    parser.add_argument(
        "--network", action="store_true", help="with --diagnostics, also test the connection to the services"
    )
    parser.add_argument("--check-playlist", metavar="URL", help="load one playlist, print what happened, then exit")
    parser.add_argument(
        "--check",
        nargs="?",
        const="changed",
        choices=("changed", "all"),
        help="check and verify new and changed files (or all files) of the current project without the app, "
        "then print the report",
    )
    parser.add_argument(
        "--report",
        nargs="?",
        const="text",
        choices=("text", "json", "csv"),
        help="print the state of the current project as text, JSON or CSV, then exit; "
        "the exit code is 2 when a file is wrong or needs review",
    )
    parser.add_argument("--packaged", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"spotcheck {__version__}")
    args = parser.parse_args()

    if args.packaged:
        home = Path(args.home or runtime.user_home()).expanduser().resolve()
        runtime.prepare(True, home)
        paths = configure(home, first_music=None if args.home else runtime.user_music() / "My library")
        runtime.log_to_file(paths.home)
    else:
        runtime.prepare(False)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        configure(args.home)
    readable_output()
    if args.quit:
        running = active_port(args.host, args.port)
        raise SystemExit(0 if running is None or quit_running(args.host, running) else 1)
    if args.diagnostics:
        print(diagnostics.report(network=args.network))
        raise SystemExit(0 if deps.status(check_updates=False)["ready"] else 1)
    if args.check_playlist:
        raise SystemExit(check_playlist(args.check_playlist))
    if args.setup:
        raise SystemExit(run_setup())
    if args.check or args.report:
        if not args.packaged:
            logging.getLogger().setLevel(logging.WARNING)
        raise SystemExit(headless(args))

    running = active_port(args.host, args.port)
    if running is not None:
        url = f"http://{args.host}:{running}/"
        print(f"SpotCheck is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    port = free_port(args.host, args.port)
    if port is None:
        raise SystemExit(f"Ports {args.port}-{args.port + 19} are all in use. Choose another one with --port.")

    import uvicorn

    from .server import create_app

    url = f"http://{args.host}:{port}/"
    app = create_app()
    settings = {"log_level": "warning", "log_config": None, "timeout_graceful_shutdown": 2}
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, **settings))
    logging.getLogger("spotcheck").info("Start of SpotCheck %s: %s", __version__, diagnostics.report())
    app.state.shutdown = lambda: setattr(server, "should_exit", True)
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"SpotCheck {__version__} is running at {url} (press Ctrl+C to stop)")
    remember_instance(port)
    try:
        server.run()
    finally:
        forget_instance()
        analysis.stop_all()


if __name__ == "__main__":
    main()

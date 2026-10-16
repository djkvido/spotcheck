import logging
import subprocess
import urllib.error
from pathlib import Path

import pytest
from conftest import make_song

from spotcheck import deps, diagnostics, origins, runtime
from spotcheck.engine import Bus, Engine
from spotcheck.library import Library


@pytest.fixture()
def engine(workspace):
    lib = Library()
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])
    return Engine(lib, Bus())


def fake_status(ready=True):
    items = [
        {"key": "spotdl", "name": "spotDL", "installed": True, "version": "4.5.2", "path": None},
        {
            "key": "ffmpeg",
            "name": "FFmpeg",
            "installed": ready,
            "version": "8.1",
            "path": str(Path.home() / "bin" / "ffmpeg"),
        },
    ]
    return {"items": items, "ready": ready, "python": "3.12.0", "platform": "win32", "packaged": True}


def test_sanitize_hides_the_home_folder_and_the_user_name(monkeypatch):
    monkeypatch.setattr(diagnostics.getpass, "getuser", lambda: "someuser")
    text = diagnostics.sanitize(f"{Path.home()}\\Music and SomeUser")
    assert str(Path.home()) not in text
    assert "someuser" not in text.lower()
    assert "~" in text


def test_short_keeps_the_last_lines_and_limits_the_length():
    text = "\n".join(f"line {i}" for i in range(20))
    shortened = diagnostics.short(text, limit=200)
    assert "line 19" in shortened and "line 0" not in shortened
    assert len(diagnostics.short("x" * 1000, limit=50)) <= 53


def test_report_lists_components_and_flags_what_is_missing(workspace, monkeypatch):
    monkeypatch.setattr(deps, "status", lambda check_updates=True: fake_status(ready=False))
    text = diagnostics.report(events=["12:00:00 warn spotdl_failed code=1"])
    assert "SpotCheck " in text
    assert "FFmpeg" in text and "MISSING" in text
    assert "Ready: NO, missing: FFmpeg" in text
    assert "spotdl_failed" in text
    assert str(Path.home()) not in text


def test_report_probes_the_services_only_when_asked(workspace, monkeypatch):
    monkeypatch.setattr(deps, "status", lambda check_updates=True: fake_status())
    monkeypatch.setattr(diagnostics, "PROBES", (("Example", "https://example.invalid/"),))

    def refuse(request, timeout):
        raise urllib.error.URLError("blocked")

    monkeypatch.setattr(diagnostics.urllib.request, "urlopen", refuse)
    assert "Network:" not in diagnostics.report()
    text = diagnostics.report(network=True)
    assert "Network:" in text and "Example" in text and "FAILED" in text


def test_report_includes_the_tail_of_the_log_file(workspace, monkeypatch, tmp_path):
    log_file = tmp_path / "spotcheck.log"
    log_file.write_text("first\nsecond\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "_log_file", log_file)
    monkeypatch.setattr(deps, "status", lambda check_updates=True: fake_status())
    text = diagnostics.report()
    assert "second" in text and "Log file, last 2 lines" in text


def test_every_event_of_the_bus_is_written_to_the_log(caplog):
    bus = Bus()
    with caplog.at_level(logging.INFO):
        bus.log("warn", "spotdl_failed", url="https://example.com/x", exit_code=1, detail="boom")
    record = next(r for r in caplog.records if "spotdl_failed" in r.getMessage())
    assert record.levelno == logging.WARNING
    assert "exit_code=1" in record.getMessage() and "detail=boom" in record.getMessage()


def completed(returncode=1, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_a_failing_spotdl_is_reported_with_its_output(engine, monkeypatch):
    monkeypatch.setattr(
        "spotcheck.engine.analysis.run_streaming", lambda cmd, on_line, **k: completed(1, "HTTP 429 too many requests")
    )
    songs = engine.load_playlist("https://open.spotify.com/playlist/Z")[1]
    assert songs == []
    event = next(e for e in engine.bus.logs if e["code"] == "spotdl_failed")
    assert event["params"]["exit_code"] == 1
    assert "429" in event["params"]["detail"]


def test_a_spotdl_timeout_is_reported(engine, monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired("spotdl", 1)

    monkeypatch.setattr("spotcheck.engine.analysis.run_streaming", hang)
    assert engine.load_playlist("https://open.spotify.com/playlist/Z")[1] == []
    assert any(e["code"] == "spotdl_timeout" for e in engine.bus.logs)


def test_ytdlp_failures_are_reported_to_the_caller(monkeypatch):
    seen = []
    url = "https://www.youtube.com/playlist?list=PLx"
    monkeypatch.setattr(origins.analysis, "run_text", lambda *a, **k: completed(1, "", "ERROR: Sign in to confirm"))
    assert origins.load(url, ["yt-dlp"], on_error=lambda code, detail: seen.append((code, detail))) == (None, [], 0)
    assert seen[0][0] == "ytdlp_failed" and "Sign in" in seen[0][1]

    seen.clear()
    monkeypatch.setattr(origins.analysis, "run_text", lambda *a, **k: completed(0, "not json", ""))
    origins.load(url, ["yt-dlp"], on_error=lambda code, detail: seen.append(code))
    assert seen == ["ytdlp_bad_output"]

    seen.clear()
    monkeypatch.setattr(
        origins.analysis, "run_text", lambda *a, **k: completed(0, '{"title": "x", "entries": []}', "warning")
    )
    origins.load(url, ["yt-dlp"], on_error=lambda code, detail: seen.append(code))
    assert seen == ["ytdlp_empty"]


def test_the_diagnostics_endpoint_returns_the_report(workspace, monkeypatch):
    from fastapi.testclient import TestClient

    from spotcheck import terms
    from spotcheck.server import create_app

    monkeypatch.setattr(deps, "status", lambda check_updates=True: fake_status())
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as client:
        text = client.get("/api/diagnostics").json()["text"]
    assert text.startswith("SpotCheck ") and "Ready: yes" in text


def test_the_size_of_a_spotify_playlist_is_announced_while_it_is_still_loading(engine, monkeypatch):
    def fake(cmd, on_line, **kwargs):
        on_line("Processing query: https://open.spotify.com/playlist/Z" + chr(10))
        on_line("Found 49 songs in Today's Top Hits (Playlist)" + chr(10))
        on_line("Found 49 songs in Today's Top Hits (Playlist)" + chr(10))
        return completed(1, "")

    monkeypatch.setattr("spotcheck.engine.analysis.run_streaming", fake)
    engine.load_playlist("https://open.spotify.com/playlist/Z")
    found = [e for e in engine.bus.logs if e["code"] == "playlist_found"]
    assert len(found) == 1
    assert found[0]["params"] == {"name": "Today's Top Hits", "count": 49}


def test_streaming_passes_every_line_and_stops_a_hanging_process():
    import sys

    from spotcheck import analysis

    seen = []
    result = analysis.run_streaming([sys.executable, "-c", "print('one'); print('two')"], seen.append, timeout=30)
    assert result.returncode == 0 and [line.strip() for line in seen] == ["one", "two"]
    with pytest.raises(subprocess.TimeoutExpired):
        analysis.run_streaming([sys.executable, "-c", "import time; time.sleep(60)"], seen.append, timeout=1)

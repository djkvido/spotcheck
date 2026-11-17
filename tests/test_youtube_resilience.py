import subprocess
import threading
import time
from pathlib import Path

import pytest
from conftest import make_song
from fastapi.testclient import TestClient

from spotcheck import analysis, deps, origins, sources, terms, ytdlp
from spotcheck.engine import Bus, Engine
from spotcheck.errors import Cancelled
from spotcheck.library import Library
from spotcheck.server import create_app

HEADERS = {"X-SpotCheck": "1"}
VIDEO = "https://www.youtube.com/watch?v=EoJmhRJO6Hk"
PLAYLIST = "https://www.youtube.com/playlist?list=PLx"
BOT = "ERROR: [youtube] EoJmhRJO6Hk: Sign in to confirm you're not a bot. Use --cookies-from-browser"
AGE = "ERROR: [youtube] EoJmhRJO6Hk: Sign in to confirm your age. This video may be inappropriate for some users."
INFO = '{"id": "EoJmhRJO6Hk", "title": "Artist - Song", "duration": 40, "channel": "Artist"}'
LIST = '{"title": "Mix", "entries": [{"id": "a1", "title": "Artist - One", "duration": 200, "url": "https://www.youtube.com/watch?v=a1"}]}'


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture()
def quiet(monkeypatch):
    waits = []
    monkeypatch.setattr(analysis, "pause", waits.append)
    monkeypatch.setattr(origins, "identify", lambda artist, title, duration: None)
    monkeypatch.setattr(origins, "IDENTIFY_DELAY", 0)
    return waits


def answer(monkeypatch, *results):
    calls = []
    queue = list(results)

    def run(*a, **k):
        calls.append(a)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(analysis, "run_text", run)
    return calls


def test_a_bot_check_is_retried_and_the_song_still_loads(monkeypatch, quiet):
    answer(monkeypatch, completed(1, "", BOT), completed(0, INFO, ""))
    songs = origins.load_track(VIDEO, ["yt-dlp"])
    assert [s["song_id"] for s in songs] == ["yt-EoJmhRJO6Hk"]
    assert quiet == [ytdlp.RETRIES[0]]


def test_a_lasting_bot_check_gets_its_own_explanation(monkeypatch, quiet):
    seen = []
    calls = answer(monkeypatch, completed(1, "", BOT))
    assert origins.load_track(VIDEO, ["yt-dlp"], on_error=lambda code, detail: seen.append(code)) == []
    assert seen == ["youtube_blocked"] and quiet == list(ytdlp.RETRIES) and len(calls) == 3


def test_while_a_site_is_blocking_further_requests_are_not_retried(monkeypatch, quiet):
    answer(monkeypatch, completed(1, "", BOT))
    origins.load_track(VIDEO, ["yt-dlp"])
    quiet.clear()
    calls = answer(monkeypatch, completed(1, "", BOT))
    origins.load_track(VIDEO, ["yt-dlp"])
    assert len(calls) == 1 and quiet == []
    answer(monkeypatch, completed(0, INFO, ""))
    origins.load_track(VIDEO, ["yt-dlp"])
    assert not ytdlp.cooling()


def test_age_restricted_and_private_videos_are_not_retried(monkeypatch, quiet):
    for stderr in (AGE, "ERROR: Video unavailable. This video is private"):
        seen = []
        calls = answer(monkeypatch, completed(1, "", stderr))
        origins.load_track(VIDEO, ["yt-dlp"], on_error=lambda code, detail, found=seen: found.append(code))
        assert len(calls) == 1 and seen == ["ytdlp_failed"] and quiet == []


def test_a_refused_download_points_to_an_update(monkeypatch, quiet):
    seen = []
    answer(monkeypatch, completed(1, "", "ERROR: unable to download video data: HTTP Error 403: Forbidden"))
    origins.load_track(VIDEO, ["yt-dlp"], on_error=lambda code, detail: seen.append(code))
    assert seen == ["ytdlp_forbidden"] and quiet == []


def test_a_link_without_a_song_is_not_called_a_playlist(monkeypatch, quiet):
    seen = []
    answer(monkeypatch, completed(0, '{"title": "x"}', ""))
    origins.load_track(VIDEO, ["yt-dlp"], on_error=lambda code, detail: seen.append(code))
    assert seen == ["ytdlp_track_empty"]


def test_a_playlist_with_one_blocked_entry_keeps_the_others(monkeypatch, quiet):
    calls = answer(monkeypatch, completed(1, LIST, "ERROR: [youtube] zz: HTTP Error 429: Too Many Requests"))
    name, songs, _ = origins.load(PLAYLIST, ["yt-dlp"])
    assert name == "Mix" and [s["song_id"] for s in songs] == ["yt-a1"]
    assert len(calls) == 1 and quiet == []


def test_cancelling_interrupts_the_pause_between_tries():
    def worker():
        try:
            analysis.pause(30)
        except Cancelled:
            outcome.append("cancelled")

    outcome = []
    thread = threading.Thread(target=worker, name="spotcheck-job_0")
    started = time.monotonic()
    thread.start()
    time.sleep(0.2)
    analysis.stop_all(jobs_only=True)
    thread.join(5)
    assert outcome == ["cancelled"] and time.monotonic() - started < 5


def single(workspace):
    lib = Library()
    pl = lib.upsert_playlist(VIDEO, "Singles", [make_song("yt-EoJmhRJO6Hk", origin="youtube", url=VIDEO)])["playlist"]
    return Engine(lib, Bus()), pl


def test_a_blocked_download_is_retried(workspace, monkeypatch):
    engine, pl = single(workspace)
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    monkeypatch.setattr(engine, "cover_bytes", lambda tr: None)
    monkeypatch.setattr("spotcheck.acquire.tags.write", lambda *a, **k: None)
    calls = []

    def run(cmd, timeout=None, cwd=None):
        calls.append(cmd)
        if len(calls) < 2:
            return completed(1, "", BOT)
        (Path(cwd) / "Artist - Song.mp3").write_bytes(b"ID3")
        return completed(0, "", "")

    monkeypatch.setattr(analysis, "run_text", run)
    path = engine.ytdlp_fetch(pl, pl["tracks"][0], VIDEO)
    assert path is not None and path.name == "Artist - Song.mp3" and len(calls) == 2
    assert "--socket-timeout" in calls[0]


def test_a_lasting_download_block_is_explained_once(workspace, monkeypatch):
    engine, pl = single(workspace)
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    monkeypatch.setattr(analysis, "run_text", lambda *a, **k: completed(1, "", BOT))
    for _ in range(3):
        assert engine.ytdlp_fetch(pl, pl["tracks"][0], VIDEO) is None
    assert [e["code"] for e in engine.bus.logs] == ["youtube_blocked"]


def test_a_blocked_spotdl_download_is_reported(workspace, monkeypatch):
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])["playlist"]
    engine = Engine(lib, Bus())
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    monkeypatch.setattr(analysis, "run_text", lambda *a, **k: completed(1, BOT, ""))
    assert engine.spotdl_fetch(pl, pl["tracks"][0]) is None
    assert [e["code"] for e in engine.bus.logs] == ["youtube_blocked"]


def test_reference_downloads_are_retried(workspace, monkeypatch):
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    calls = []

    def run(cmd, timeout=None, cwd=None):
        calls.append(cmd)
        if len(calls) == 2:
            (workspace.candidates / "abc.webm").write_bytes(b"x" * 20_000)
            return completed(0, "", "")
        return completed(1, "", BOT)

    monkeypatch.setattr(analysis, "run_text", run)
    assert sources.fetch_candidate({"id": "abc", "url": VIDEO}, ["yt-dlp"]).name == "abc.webm"


def test_ytdlp_updates_are_offered_only_for_newer_versions(monkeypatch):
    monkeypatch.setattr(deps, "_installed_version", lambda package: "2026.8.19")
    update = deps.ytdlp_update("2026.10.26")
    assert update["current"] == "2026.8.19" and update["latest"] == "2026.10.26" and "packaged" in update
    assert deps.ytdlp_update("2026.08.19") is None
    assert deps.ytdlp_update(None) is None


def test_the_same_link_is_not_queued_twice(workspace, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(origins, "load_track", lambda *a, **k: release.wait(10) and [])
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as client:
        first = client.post("/api/playlists", json={"urls": VIDEO}, headers=HEADERS).json()
        time.sleep(0.2)
        second = client.post("/api/playlists", json={"urls": VIDEO}, headers=HEADERS).json()
        other = client.post("/api/playlists", json={"urls": VIDEO, "follow_up": True}, headers=HEADERS).json()
        release.set()
    assert first["job"] == second["job"] != other["job"]


def test_a_blocked_playlist_that_prints_null_is_retried_and_explained(monkeypatch, quiet):
    seen = []
    calls = answer(monkeypatch, completed(1, "null\n", BOT))
    assert origins.load(PLAYLIST, ["yt-dlp"], on_error=lambda code, detail: seen.append(code)) == (None, [], 0)
    assert seen == ["youtube_blocked"] and len(calls) == 3 and ytdlp.cooling()


def test_a_song_title_never_looks_like_a_block():
    noise = completed(1, "LookupError: No results found for song: Band - Try Again Later\n", "")
    assert not ytdlp.blocked(ytdlp.error_lines(noise))
    real = completed(1, "AudioProviderError: YT-DLP download error - ERROR: Sign in to confirm you're not a bot\n", "")
    assert ytdlp.blocked(ytdlp.error_lines(real))
    assert not ytdlp.blocked(ytdlp.output(completed(1, '{"description": "try again later"}', "")))


def test_an_update_is_not_queued_twice(workspace, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(deps, "install", lambda action, log: release.wait(10))
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as client:
        first = client.post("/api/deps/install", json={"action": "update-python"}, headers=HEADERS).json()
        time.sleep(0.2)
        second = client.post("/api/deps/install", json={"action": "update-python"}, headers=HEADERS).json()
        release.set()
    assert first["job"] == second["job"]

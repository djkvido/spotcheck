import asyncio
import sys

import pytest
from conftest import make_song
from fastapi.testclient import TestClient

from spotcheck import sources, terms
from spotcheck.engine import Bus
from spotcheck.library import Library
from spotcheck.server import apple_script_folder, create_app

HEADERS = {"X-SpotCheck": "1"}


@pytest.fixture()
def client(workspace):
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as c:
        yield c


def add_track_with_file(workspace, content):
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", [make_song("t1", "One")])["playlist"]
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    (folder / "Artist - One.mp3").write_bytes(content)
    pl["tracks"][0]["file"] = "Mix/Artist - One.mp3"
    lib.save()


def test_audio_is_streamed_whole_or_by_range(workspace):
    content = bytes(range(256)) * 400
    add_track_with_file(workspace, content)
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as client:
        whole = client.get("/api/audio/A:t1")
        assert whole.status_code == 200 and whole.content == content
        part = client.get("/api/audio/A:t1", headers={"Range": "bytes=10-19"})
        assert part.status_code == 206 and part.content == content[10:20]
        assert part.headers["content-range"] == f"bytes 10-19/{len(content)}"
        assert client.get("/api/audio/A:t1", headers={"Range": f"bytes={len(content) + 5}-"}).status_code == 416


def test_only_secure_addresses_are_fetched():
    with pytest.raises(ValueError, match="secure"):
        sources.http_get("http://example.com/cover.jpg", binary=True)
    with pytest.raises(ValueError, match="secure"):
        sources.http_get("file:///etc/passwd", binary=True)


def test_a_slow_viewer_gets_a_resync_instead_of_an_endless_queue():
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    for n in range(3):
        Bus.deliver(queue, {"type": "track", "n": n})
    assert queue.qsize() == 1
    assert queue.get_nowait() == {"type": "resync"}


def test_the_folder_dialog_on_macos_is_given_a_safe_start_folder():
    command = apple_script_folder('/Users/a "b"\\c')
    assert command[:2] == ["osascript", "-e"]
    assert 'POSIX file "/Users/a \\"b\\"\\\\c"' in command[2]


def test_spotcheck_only_listens_on_this_computer(monkeypatch, capsys):
    from spotcheck import __main__

    monkeypatch.setattr(sys, "argv", ["spotcheck", "--host", "0.0.0.0", "--no-browser"])
    with pytest.raises(SystemExit):
        __main__.main()
    assert "invalid choice" in capsys.readouterr().err


def test_many_selected_tracks_are_checked_quickly(client):
    lib = Library()
    songs = [make_song(f"t{i}", f"Song {i}") for i in range(300)]
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "Big", songs)
    client.post("/api/projects/default/open", headers=HEADERS)
    keys = [f"A:t{i}" for i in range(300)]
    response = client.post("/api/process", json={"flags": {"validate": True}, "track_ids": keys}, headers=HEADERS)
    assert response.status_code == 200
    unknown = client.post("/api/process", json={"flags": {"validate": True}, "track_ids": ["A:nope"]}, headers=HEADERS)
    assert unknown.status_code == 404

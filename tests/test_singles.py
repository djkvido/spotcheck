import pytest
from conftest import make_song
from fastapi.testclient import TestClient

from spotcheck import origins, terms
from spotcheck.engine import Bus, Engine
from spotcheck.library import SINGLES_ID, Library
from spotcheck.server import create_app, split_urls

HEADERS = {"X-SpotCheck": "1"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://open.spotify.com/track/4yOn1TEcfsKHUJCL2h1r8I?si=abc", ("spotify", "4yOn1TEcfsKHUJCL2h1r8I")),
        ("https://open.spotify.com/intl-cs/track/4yOn1TEcfsKHUJCL2h1r8I", ("spotify", "4yOn1TEcfsKHUJCL2h1r8I")),
        ("https://www.youtube.com/watch?v=Y79K-XyMOA0", ("youtube", "yt-Y79K-XyMOA0")),
        ("https://youtu.be/Y79K-XyMOA0?t=5", ("youtube", "yt-Y79K-XyMOA0")),
        ("https://www.youtube.com/watch?v=Y79K-XyMOA0&list=RDY79K-XyMOA0&start_radio=1", ("youtube", "yt-Y79K-XyMOA0")),
        ("https://soundcloud.com/artist/some-track?si=1", ("soundcloud", "sc-artist-some-track")),
        ("https://artist.bandcamp.com/track/some-track", ("bandcamp", "bc-artist-some-track")),
    ],
)
def test_links_to_one_song_are_recognised(url, expected):
    assert origins.classify_track(url) == expected
    assert origins.classify(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy",
        "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
        "https://www.youtube.com/watch?v=Y79K-XyMOA0&list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
        "https://soundcloud.com/artist/sets/my-set",
        "https://artist.bandcamp.com/album/some-album",
        "https://www.youtube.com/playlist?list=RDY79K-XyMOA0",
        "not a link",
    ],
)
def test_playlists_and_other_links_are_not_taken_for_songs(url):
    assert origins.classify_track(url) is None


def test_song_links_are_cleaned_to_their_canonical_form():
    assert (
        origins.canonical_track_url("https://open.spotify.com/track/abc123?si=x")
        == "https://open.spotify.com/track/abc123"
    )
    assert (
        origins.canonical_track_url("https://www.youtube.com/watch?v=Y79K-XyMOA0&list=RDY79K-XyMOA0&start_radio=1")
        == "https://www.youtube.com/watch?v=Y79K-XyMOA0"
    )
    assert (
        origins.canonical_track_url("https://youtu.be/Y79K-XyMOA0?t=5") == "https://www.youtube.com/watch?v=Y79K-XyMOA0"
    )
    assert origins.canonical_track_url("https://soundcloud.com/a/b?si=1#x") == "https://soundcloud.com/a/b"


def test_split_urls_accepts_songs_and_playlists_and_removes_duplicates():
    text = (
        "https://open.spotify.com/track/abc123?si=1 https://open.spotify.com/track/abc123 "
        "https://open.spotify.com/album/xyz789 nonsense"
    )
    urls, rejected = split_urls(text)
    assert urls == ["https://open.spotify.com/track/abc123", "https://open.spotify.com/album/xyz789"]
    assert rejected == ["nonsense"]


@pytest.fixture()
def library(workspace):
    return Library()


def test_songs_are_collected_in_one_singles_list(library):
    change = library.add_singles([make_song("a1", "One"), make_song("a2", "Two")])
    assert change["new"] and len(change["added"]) == 2
    pl = change["playlist"]
    assert (pl["id"], pl["origin"], pl["name"], pl["folder"]) == (SINGLES_ID, "singles", "Singles", "Singles")
    assert [t["number"] for t in pl["tracks"]] == [1, 2]
    again = library.add_singles([make_song("a2", "Two"), make_song("a3", "Three")])
    assert not again["new"] and [t["id"] for t in again["added"]] == ["a3"]
    assert [t["id"] for t in library.playlist(SINGLES_ID)["tracks"]] == ["a1", "a2", "a3"]


def test_a_song_can_be_removed_from_singles_but_not_from_a_playlist(library):
    library.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("p1")])
    library.add_singles([make_song("a1"), make_song("a2")])
    assert not library.remove_single("A:p1")
    assert library.remove_single(f"{SINGLES_ID}:a1")
    tracks = library.playlist(SINGLES_ID)["tracks"]
    assert [t["id"] for t in tracks] == ["a2"] and tracks[0]["number"] == 1


def test_singles_are_never_updated_from_a_source(library):
    library.add_singles([make_song("a1")])
    library.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("p1")])
    for pl in library.data["playlists"]:
        pl["synced"] = 1.0
    assert library.due_for_sync(1) == ["A"]


@pytest.fixture()
def engine(workspace):
    return Engine(Library(), Bus())


def test_adding_a_song_link_puts_it_into_singles(engine, monkeypatch):
    song = make_song("4yOn1TEcfsKHUJCL2h1r8I", "Feel This Moment", "Pitbull")
    monkeypatch.setattr(engine, "load_track", lambda url: [song])
    job = {"state": "running", "done": 0, "total": 0, "current": "", "summary": {}, "_cancel": False}
    engine._job_sync(job, urls=["https://open.spotify.com/track/4yOn1TEcfsKHUJCL2h1r8I"])
    assert job["summary"]["playlists"] == 1 and job["summary"]["failed"] == 0
    pl = engine.lib.playlist(SINGLES_ID)
    assert [t["name"] for t in pl["tracks"]] == ["Feel This Moment"]
    assert any(e["code"] == "track_added" for e in engine.bus.logs)


def test_a_song_that_cannot_be_loaded_is_reported(engine, monkeypatch):
    monkeypatch.setattr(engine, "load_track", lambda url: [])
    job = {"state": "running", "done": 0, "total": 0, "current": "", "summary": {}, "_cancel": False}
    engine._job_sync(job, urls=["https://www.youtube.com/watch?v=Y79K-XyMOA0"])
    assert job["summary"]["failed"] == 1
    assert engine.lib.playlist(SINGLES_ID) is None
    assert any(e["code"] == "single_failed" for e in engine.bus.logs)


def test_refreshing_everything_skips_the_singles_list(engine, monkeypatch):
    engine.lib.add_singles([make_song("a1")])
    seen = []
    monkeypatch.setattr(engine, "load_track", lambda url: seen.append(url) or [])
    monkeypatch.setattr(engine, "load_playlist", lambda url: seen.append(url) or ("x", []))
    job = {"state": "running", "done": 0, "total": 0, "current": "", "summary": {}, "_cancel": False}
    engine._job_sync(job, playlist_ids=[SINGLES_ID])
    assert seen == [] and job["total"] == 0


def test_the_api_refuses_to_remove_an_unknown_song(workspace):
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as client:
        assert client.delete("/api/tracks/singles:none", headers=HEADERS).status_code == 404

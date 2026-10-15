import json

import pytest
from conftest import make_song

from spotcheck.library import Library, compute_status, folder_name, playlist_id, track_key


@pytest.mark.parametrize(
    ("track", "expected"),
    [
        ({"file": None}, "missing"),
        ({"file": "a.mp3"}, "unchecked"),
        ({"file": "a.mp3", "verify": {"verdict": "MATCH"}, "check": {"verdict": "OK"}}, "ok"),
        ({"file": "a.mp3", "verify": {"verdict": "MATCH"}, "check": {"verdict": "FAIL"}}, "review"),
        ({"file": "a.mp3", "verify": {"verdict": "UNSURE"}, "check": {"verdict": "OK"}}, "review"),
        ({"file": "a.mp3", "verify": {"verdict": "MISMATCH"}, "check": {"verdict": "OK"}}, "bad"),
        ({"file": "a.mp3", "verify": {"verdict": "NO_PREVIEW"}, "check": {"verdict": "WARN"}}, "partial"),
        ({"file": "a.mp3", "verify": {"verdict": "NO_PREVIEW"}, "check": {"verdict": "FAIL"}}, "bad"),
    ],
)
def test_compute_status(track, expected):
    assert compute_status(track) == expected


def test_playlist_id_and_folder_name():
    assert playlist_id("https://open.spotify.com/playlist/4yPkzUC1Rfm7kNsyDDV5re?si=x") == "4yPkzUC1Rfm7kNsyDDV5re"
    assert playlist_id("https://open.spotify.com/album/abc123") == "abc123"
    assert folder_name('Dance Club - Latino  - Bachata / "Best"') == "Dance Club - Latino - Bachata Best"
    assert folder_name("bachata hits. ") == "bachata hits"


def test_same_track_in_two_playlists_is_addressed_by_key(workspace):
    lib = Library()
    a = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])["playlist"]
    b = lib.upsert_playlist("https://open.spotify.com/playlist/B", "B", [make_song("t1")])["playlist"]
    assert lib.find_track(track_key(b, b["tracks"][0]))[0]["id"] == "B"
    assert lib.find_track(track_key(a, a["tracks"][0]))[0]["id"] == "A"


def test_updating_a_playlist_reports_changes_and_keeps_results(workspace):
    lib = Library()
    first = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1"), make_song("t2")])
    assert first["new"] and len(first["added"]) == 2
    first["playlist"]["tracks"][0]["verify"] = {"verdict": "MATCH", "ber": 0.02}
    change = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1"), make_song("t3")])
    pl = change["playlist"]
    assert not change["new"]
    assert [t["id"] for t in change["added"]] == ["t3"]
    assert [t["id"] for t in change["removed"]] == ["t2"]
    assert [t["id"] for t in pl["tracks"]] == ["t1", "t3"]
    assert pl["tracks"][0]["verify"]["ber"] == 0.02


def test_duplicate_tracks_in_a_playlist_are_listed_once(workspace):
    lib = Library()
    change = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1"), make_song("t1")])
    assert [t["id"] for t in change["playlist"]["tracks"]] == ["t1"]


def test_renamed_playlist_keeps_its_folder(workspace):
    lib = Library()
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "Salsa", [make_song("t1")])
    change = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Salsa 2026", [make_song("t1")])
    pl = change["playlist"]
    assert (change["old_name"], pl["name"]) == ("Salsa", "Salsa 2026")
    assert lib.folder(pl) == workspace.downloads / "Salsa"


def test_new_playlist_reuses_a_folder_that_differs_only_in_punctuation(workspace):
    (workspace.downloads / "Dance Club - Latino - Bachata").mkdir()
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Dance Club - Latino  - Bachata", [])["playlist"]
    assert pl["folder"] == "Dance Club - Latino - Bachata"


def test_temporary_files_are_never_linked(workspace):
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1", name="Song")])["playlist"]
    folder = workspace.downloads / "A"
    folder.mkdir()
    (folder / "Artist - Song.spotcheck-trim.mp3").write_bytes(b"x")
    lib.link_files()
    assert pl["tracks"][0]["file"] is None


def test_corrupt_library_is_preserved_not_overwritten(workspace):
    workspace.library_file.write_text("{ not json", encoding="utf-8")
    lib = Library()
    assert lib.data["playlists"] == []
    corrupt = list(workspace.data.glob("library.corrupt-*.json"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == "{ not json"


def test_library_round_trip(workspace):
    lib = Library()
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])
    reloaded = Library()
    assert reloaded.data["playlists"][0]["tracks"][0]["id"] == "t1"
    assert json.loads(workspace.library_file.read_text(encoding="utf-8"))["settings"]["accept_ber"] == 0.3


def test_link_files_does_not_give_one_file_to_two_tracks(workspace):
    lib = Library()
    songs = [
        make_song("t1", name="España Cani (Paso Doble / 60 bpm)", artist="Orchestra"),
        make_song("t2", name="España Cani ((Paso Doble / 60 Bpm))", artist="Orchestra"),
    ]
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Passo Doble", songs)["playlist"]
    folder = workspace.downloads / "Passo Doble"
    folder.mkdir()
    (folder / "Orchestra - España Cani (Paso Doble 60 bpm).mp3").write_bytes(b"x")
    pl["tracks"][1]["file"] = "Passo Doble/Orchestra - España Cani (Paso Doble 60 bpm).mp3"
    lib.link_files()
    files = [t["file"] for t in pl["tracks"]]
    assert files == [None, "Passo Doble/Orchestra - España Cani (Paso Doble 60 bpm).mp3"]

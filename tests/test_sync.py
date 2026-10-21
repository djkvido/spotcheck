import time

import pytest
from conftest import make_song

from spotcheck import sources
from spotcheck.engine import JOB_HISTORY, Bus, Engine
from spotcheck.library import Library


@pytest.fixture()
def engine(workspace):
    return Engine(Library(), Bus())


def job():
    return {"done": 0, "total": 0, "current": "", "_cancel": False, "summary": {}}


def test_names_in_any_script_are_kept_for_matching():
    assert sources.norm("Кино - Группа крови") == "кино группа крови"
    assert sources.norm("Beyoncé / Halo (Live)") == "beyonce halo live"
    assert sources.norm("Straße_Name") == "strasse name"
    assert sources.norm("") == ""


def test_files_with_non_latin_names_are_linked_to_the_right_track(workspace):
    lib = Library()
    songs = [make_song("t1", "Группа крови", "Кино"), make_song("t2", "Звезда по имени Солнце", "Кино")]
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Кино", songs)["playlist"]
    folder = workspace.downloads / pl["folder"]
    folder.mkdir()
    (folder / "Кино - Звезда по имени Солнце.mp3").write_bytes(b"x")
    (folder / "Кино - Группа крови.mp3").write_bytes(b"y")
    lib.link_files()
    assert [t["file"].split("/")[-1] for t in pl["tracks"]] == [
        "Кино - Группа крови.mp3",
        "Кино - Звезда по имени Солнце.mp3",
    ]


def test_two_playlists_with_the_same_name_get_their_own_folders(workspace):
    lib = Library()
    a = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Favorites", [make_song("t1")])["playlist"]
    b = lib.upsert_playlist("https://open.spotify.com/playlist/B", "Favorites", [make_song("t2")])["playlist"]
    assert (a["folder"], b["folder"]) == ("Favorites", "Favorites (2)")


def test_one_failing_link_does_not_stop_the_others(engine, monkeypatch):
    def load(url):
        if url.endswith("/B"):
            raise ValueError("broken answer")
        return "List " + url[-1], [make_song("t" + url[-1])]

    monkeypatch.setattr(engine, "load_playlist", load)
    engine._emit_job = lambda job: None
    current = job()
    urls = [f"https://open.spotify.com/playlist/{x}" for x in "ABC"]
    engine._job_sync(current, urls=urls)
    assert current["summary"] == {"playlists": 2, "added": 0, "removed": 0, "failed": 1}
    assert [p["id"] for p in engine.lib.data["playlists"]] == ["A", "C"]
    assert any(e["code"] == "sync_error" for e in engine.bus.logs)


def test_a_failing_playlist_is_retried_later_and_later(engine, monkeypatch):
    engine.lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])
    pl = engine.lib.playlist("A")
    pl["synced"] = time.time() - 25 * 3600
    monkeypatch.setattr(engine, "load_playlist", lambda url: ("A", []))
    engine._emit_job = lambda job: None
    engine._job_sync(job(), playlist_ids=["A"])
    assert pl["sync_failed"]["count"] == 1
    assert engine.lib.due_for_sync(24) == []
    pl["sync_failed"]["t"] -= 901
    assert engine.lib.due_for_sync(24) == ["A"]
    engine._job_sync(job(), playlist_ids=["A"])
    assert pl["sync_failed"]["count"] == 2
    pl["sync_failed"]["t"] -= 901
    assert engine.lib.due_for_sync(24) == []
    monkeypatch.setattr(engine, "load_playlist", lambda url: ("A", [make_song("t1")]))
    engine._job_sync(job(), playlist_ids=["A"])
    assert "sync_failed" not in pl
    assert engine.lib.due_for_sync(24) == []


def test_unreadable_spotdl_output_counts_as_a_failure(engine, monkeypatch, workspace):
    import subprocess

    def fake(cmd, on_line, **kwargs):
        out = cmd[cmd.index("--save-file") + 1]
        with open(out, "w", encoding="utf-8") as handle:
            handle.write("[{")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("spotcheck.engine.analysis.run_streaming", fake)
    assert engine.load_playlist("https://open.spotify.com/playlist/Z") == ("Playlist", [])
    assert not list(workspace.tmp.glob("sync-*.spotdl"))


def test_a_name_conflict_is_not_retried_every_scan(engine, workspace):
    lib = engine.lib
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", [make_song("t1", "One")])["playlist"]
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    (folder / "Artist - One.mp3").write_bytes(b"x")
    (folder / "001 - Artist - One.mp3").write_bytes(b"other")
    pl["tracks"][0]["file"] = "Mix/Artist - One.mp3"
    lib.update_settings({"number_files": True, "write_playlists": False})
    assert engine.needs_organize()
    engine.organize()
    assert not engine.needs_organize()


def test_a_playlist_file_that_cannot_be_written_is_not_retried_every_scan(engine, workspace, monkeypatch):
    lib = engine.lib
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", [make_song("t1", "One")])
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    (folder / "Artist - One.mp3").write_bytes(b"x")
    lib.data["playlists"][0]["tracks"][0]["file"] = "Mix/Artist - One.mp3"

    def refuse(pl, force=False):
        raise PermissionError("read-only")

    monkeypatch.setattr(engine, "write_playlist_file", refuse)
    assert engine.needs_organize()
    engine.organize()
    assert not engine.needs_organize()


def test_files_stay_linked_while_the_music_folder_is_unavailable(workspace):
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", [make_song("t1", "One")])["playlist"]
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    (folder / "Artist - One.mp3").write_bytes(b"x")
    lib.link_files()
    assert pl["tracks"][0]["file"] == "Mix/Artist - One.mp3"
    (folder / "Artist - One.mp3").unlink()
    folder.rmdir()
    assert lib.link_files() is False
    assert pl["tracks"][0]["file"] == "Mix/Artist - One.mp3"


def test_old_finished_jobs_are_forgotten(engine, monkeypatch):
    monkeypatch.setattr(engine.queue, "put", lambda job_id: None)
    first = engine.submit("organize", "organize")
    engine.jobs[first]["state"] = "done"
    for _ in range(JOB_HISTORY + 5):
        engine.jobs[engine.submit("organize", "organize")]["state"] = "done"
    assert first not in engine.jobs
    assert len(engine.order) == len(engine.jobs) == JOB_HISTORY

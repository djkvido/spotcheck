import pytest
from conftest import make_song

from spotcheck import analysis, tags
from spotcheck.engine import Bus, Engine, JobError
from spotcheck.library import Library


@pytest.fixture()
def engine(workspace):
    lib = Library()
    songs = [make_song("t1", "One", duration=180), make_song("t2", "Two", duration=200), make_song("t3", "Three")]
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", songs)
    engine = Engine(lib, Bus())
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    for tr in lib.data["playlists"][0]["tracks"][:2]:
        (folder / f"Artist - {tr['name']}.mp3").write_bytes(tr["name"].encode() * 100)
        tr["file"] = f"Mix/Artist - {tr['name']}.mp3"
    return engine


def files(workspace):
    return sorted(p.name for p in (workspace.downloads / "Mix").iterdir())


def test_numbering_follows_the_playlist_and_can_be_undone(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    engine.lib.update_settings({"number_files": True, "write_playlists": False})
    assert engine.needs_organize()
    assert engine.organize()["renamed"] == 2
    assert files(workspace) == ["001 - Artist - One.mp3", "002 - Artist - Two.mp3"]
    assert pl["tracks"][0]["file"] == "Mix/001 - Artist - One.mp3"
    assert not engine.needs_organize()

    pl["tracks"].reverse()
    engine.organize()
    assert files(workspace) == ["002 - Artist - Two.mp3", "003 - Artist - One.mp3"]

    engine.lib.update_settings({"number_files": False})
    engine.organize()
    assert files(workspace) == ["Artist - One.mp3", "Artist - Two.mp3"]
    assert not any(tr["numbered"] for tr in pl["tracks"])


def test_a_name_that_only_looks_numbered_is_left_alone(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    folder = workspace.downloads / "Mix"
    (folder / "Artist - One.mp3").rename(folder / "112 - Cupid.mp3")
    pl["tracks"][0]["file"] = "Mix/112 - Cupid.mp3"
    engine.lib.update_settings({"write_playlists": False})
    assert engine.planned_names(pl) == []


def test_numbered_files_are_still_recognised(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    folder = workspace.downloads / "Mix"
    (folder / "Artist - One.mp3").rename(folder / "007 - Artist - One.mp3")
    assert engine.lib.find_file(pl, pl["tracks"][0]).name == "007 - Artist - One.mp3"


def test_playlist_file_lists_downloaded_tracks_in_order(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    assert engine.organize()["playlists"] == 1
    text = (workspace.downloads / "Mix" / "Mix.m3u8").read_bytes().decode("utf-8")
    assert text == (
        "#EXTM3U\r\n#PLAYLIST:Mix\r\n"
        "#EXTINF:180,Artist - One\r\nArtist - One.mp3\r\n"
        "#EXTINF:200,Artist - Two\r\nArtist - Two.mp3\r\n"
    )
    assert pl["m3u"] == "Mix.m3u8"
    assert not engine.needs_organize()

    engine.lib.update_settings({"write_playlists": False})
    engine.organize()
    assert (workspace.downloads / "Mix" / "Mix.m3u8").exists()


def test_export_copies_playlists_with_numbers_and_a_playlist_file(engine, workspace, tmp_path):
    target = tmp_path / "usb"
    job = {"done": 0, "total": 0, "_cancel": False, "summary": {}, "current": ""}
    engine._emit_job = lambda job: None
    engine._job_export(job, ["A"], str(target), number=True, reveal=False)
    exported = sorted(p.name for p in (target / "Mix").iterdir())
    assert exported == ["001 - Artist - One.mp3", "002 - Artist - Two.mp3", "Mix.m3u8"]
    assert "001 - Artist - One.mp3" in (target / "Mix" / "Mix.m3u8").read_text(encoding="utf-8")
    assert job["summary"] == {"playlists": 1, "files": 2, "copied": 2, "skipped": 0}

    job = {"done": 0, "total": 0, "_cancel": False, "summary": {}, "current": ""}
    engine._job_export(job, ["A"], str(target), number=True, reveal=False)
    assert job["summary"]["skipped"] == 2


def test_export_stops_when_the_target_is_too_small(engine, tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(1, 1, 1))
    engine._emit_job = lambda job: None
    job = {"done": 0, "total": 0, "_cancel": False, "summary": {}, "current": ""}
    with pytest.raises(JobError, match="not_enough_space"):
        engine._job_export(job, ["A"], str(tmp_path / "usb"), number=False, reveal=False)


def test_replacement_with_another_format_removes_the_old_file(engine, workspace, monkeypatch):
    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][0]
    old = workspace.downloads / "Mix" / "Artist - One.mp3"

    def fetch(pl, tr, url=None):
        work = workspace.tmp / "dl"
        work.mkdir()
        new = work / "Artist - One.opus"
        new.write_bytes(b"opus" * 100)
        return new

    monkeypatch.setattr(engine, "fetch_audio", fetch)
    monkeypatch.setattr(engine, "check_and_verify", lambda pl, tr, path: None)
    path = engine.replace_with(pl, tr, old, {"id": "abc", "url": "https://www.youtube.com/watch?v=abcdefghijk"})
    assert path.name == "Artist - One.opus"
    assert not old.exists()
    assert tr["file"] == "Mix/Artist - One.opus"
    assert (workspace.data / tr["backups"][0]["file"]).read_bytes() == b"One" * 100


def test_a_verified_twin_is_copied_instead_of_downloaded(engine, workspace, monkeypatch):
    first = engine.lib.data["playlists"][0]
    first["tracks"][0]["status"] = "ok"
    second = engine.lib.upsert_playlist("https://open.spotify.com/playlist/B", "Other", [make_song("t1", "One")])
    pl = second["playlist"]
    tr = pl["tracks"][0]
    monkeypatch.setattr(engine, "check_and_verify", lambda pl, tr, path: None)
    monkeypatch.setattr(engine, "acceptable", lambda tr: True)
    path = engine.copy_twin(pl, tr)
    assert path == workspace.downloads / "Other" / "Artist - One.mp3"
    assert path.read_bytes() == b"One" * 100
    assert tr["file"] == "Other/Artist - One.mp3"


def test_quality_decides_between_equal_candidates(engine):
    st = engine.st
    low = {"verdict": "OK", "extra": 0.0, "cutoff_measured": True, "cutoff": 13.0, "notes": [{"code": "low_quality"}]}
    current = {"verify": {"verdict": "MATCH", "ber": 0.1}, "check": low}
    assert engine.needs_fix(current)
    assert engine.is_better({"ber": 0.1, "diff": 0.5, "cutoff_measured": True, "cutoff": 19.8}, current)
    assert not engine.is_better({"ber": 0.1, "diff": 0.5, "cutoff_measured": True, "cutoff": 13.5}, current)
    assert st["min_cutoff_khz"] == 15.0

    ranked = [
        {"ber": 0.10, "cutoff_measured": True, "cutoff": 16.0},
        {"ber": 0.12, "cutoff_measured": True, "cutoff": None},
        {"ber": 0.08, "cutoff_measured": False, "cutoff": None},
    ]
    assert max(ranked, key=engine.spectrum_rank)["ber"] == 0.12


@pytest.mark.skipif(not analysis.ffmpeg_path(), reason="FFmpeg is not installed")
def test_missing_tags_are_filled_in_without_touching_present_ones(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][0]
    path = workspace.downloads / "Mix" / "Artist - One.mp3"
    analysis.run_text(
        [analysis.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", str(path)]
    )
    tags.write(path, {"title": "My own title"})
    tr["check"] = {"tags_missing": tags.missing(path), "notes": []}
    engine._emit_job = lambda job: None
    job = {"done": 0, "total": 0, "_cancel": False, "summary": {}, "current": ""}
    engine._job_tags(job, track_ids=["A:t1"])
    assert job["summary"] == {"tracks": 1, "fixed": 1, "failed": 0}
    assert tags.missing(path) == ["cover"]
    assert tr["check"]["tags_missing"] == ["cover"]
    assert tr["check"]["sig"] == engine.lib.signature(path)
    f = __import__("mutagen").File(str(path))
    assert str(f.tags["TIT2"]) == "My own title"
    assert str(f.tags["TPE1"]) == "Artist"


def test_a_chosen_source_is_fetched_with_ytdlp_when_spotdl_fails(engine, monkeypatch):
    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][2]
    calls = []
    monkeypatch.setattr(engine, "spotdl_fetch", lambda pl, tr, url=None: calls.append(("spotdl", url)))
    monkeypatch.setattr(engine, "ytdlp_fetch", lambda pl, tr, url: calls.append(("yt-dlp", url)) or "file")
    assert engine.fetch_audio(pl, tr) is None
    assert engine.fetch_audio(pl, tr, url="https://www.youtube.com/watch?v=abcdefghijk") == "file"
    assert calls == [
        ("spotdl", None),
        ("spotdl", "https://www.youtube.com/watch?v=abcdefghijk"),
        ("yt-dlp", "https://www.youtube.com/watch?v=abcdefghijk"),
    ]

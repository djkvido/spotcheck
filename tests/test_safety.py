import json
import threading

import pytest
from conftest import make_song

from spotcheck import fileops, projects
from spotcheck.engine import Bus, Engine, JobError
from spotcheck.library import Library


@pytest.fixture()
def engine(workspace):
    lib = Library()
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", [make_song("t1", "One"), make_song("t2", "Two")])
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    tr = lib.data["playlists"][0]["tracks"][0]
    (folder / "Artist - One.mp3").write_bytes(b"current" * 100)
    tr["file"] = "Mix/Artist - One.mp3"
    engine = Engine(lib, Bus())
    engine.check_and_verify = lambda pl, tr, path: None
    return engine


def staged_download(workspace, name, content):
    work = workspace.tmp / f"dl-{content.decode()}"
    work.mkdir()
    path = work / name
    path.write_bytes(content * 100)
    return path


def test_two_downloads_with_the_same_name_never_overwrite_each_other(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    first = staged_download(workspace, "Artist - Song.mp3", b"first")
    second = staged_download(workspace, "Artist - Song.mp3", b"second")
    placed = []
    barrier = threading.Barrier(2)

    def place(tmp):
        barrier.wait()
        placed.append(engine.place_new(pl, tmp))

    threads = [threading.Thread(target=place, args=(p,)) for p in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(p.name for p in placed) == ["Artist - Song (2).mp3", "Artist - Song.mp3"]
    assert sorted(p.read_bytes()[:6] for p in placed) == [b"firstf", b"second"]


def test_a_failed_placement_leaves_no_empty_file(engine, workspace, monkeypatch):
    pl = engine.lib.data["playlists"][0]
    tmp = staged_download(workspace, "Artist - New.mp3", b"new")

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(fileops, "install", broken)
    with pytest.raises(OSError):
        engine.place_new(pl, tmp)
    assert not (workspace.downloads / "Mix" / "Artist - New.mp3").exists()


def make_backup(engine, workspace, content=b"older"):
    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][0]
    path = workspace.downloads / "Mix" / "Artist - One.mp3"
    original = path.read_bytes()
    path.write_bytes(content * 100)
    entry = engine.backup(tr, path, "replace")
    path.write_bytes(original)
    return pl, tr, entry


def test_restore_swaps_the_files_and_keeps_the_current_one(engine, workspace):
    pl, tr, entry = make_backup(engine, workspace)
    engine.restore_backup(pl, tr, entry)
    path = workspace.downloads / "Mix" / "Artist - One.mp3"
    assert path.read_bytes() == b"older" * 100
    assert not (workspace.data / entry["file"]).exists()
    assert [b["reason"] for b in tr["backups"]] == ["before_restore"]
    assert (workspace.data / tr["backups"][0]["file"]).read_bytes() == b"current" * 100
    saved = json.loads(workspace.library_file.read_text(encoding="utf-8"))
    assert saved["playlists"][0]["tracks"][0]["backups"][0]["reason"] == "before_restore"


def test_a_failed_restore_keeps_the_backup_and_the_current_file(engine, workspace, monkeypatch):
    pl, tr, entry = make_backup(engine, workspace)

    def locked(src, dst):
        raise fileops.FileLockedError(dst)

    monkeypatch.setattr(fileops, "install_copy", locked)
    with pytest.raises(JobError, match="file_locked"):
        engine.restore_backup(pl, tr, entry)
    assert tr["backups"] == [entry]
    assert (workspace.data / entry["file"]).read_bytes() == b"older" * 100
    assert (workspace.downloads / "Mix" / "Artist - One.mp3").read_bytes() == b"current" * 100
    assert len(list((workspace.backups / tr["id"]).iterdir())) == 1


def test_a_failed_restore_job_does_not_leave_the_track_busy(engine, workspace, monkeypatch):
    tr = make_backup(engine, workspace)[1]

    def broken(pl, tr, chosen):
        raise OSError("boom")

    monkeypatch.setattr(engine, "restore_backup", broken)
    job = {"done": 0, "total": 0, "current": "", "_cancel": False, "summary": {}}
    with pytest.raises(OSError):
        engine._job_restore(job, "A:t1", 0)
    assert tr["step"] is None


def test_the_library_falls_back_to_its_previous_copy(workspace):
    lib = Library()
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])
    lib.upsert_playlist("https://open.spotify.com/playlist/B", "B", [make_song("t2")])
    assert fileops.previous_path(workspace.library_file).exists()
    workspace.library_file.write_text("{ broken", encoding="utf-8")
    reloaded = Library()
    assert [p["id"] for p in reloaded.data["playlists"]] == ["A"]
    assert list(workspace.data.glob("library.corrupt-*.json"))


def test_a_library_of_the_wrong_shape_is_not_loaded(workspace):
    workspace.library_file.write_text(json.dumps({"playlists": [{"name": "x"}]}), encoding="utf-8")
    assert Library().data["playlists"] == []


def test_a_locked_library_is_not_mistaken_for_a_corrupt_one(workspace, monkeypatch):
    Library().upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])
    monkeypatch.setattr(fileops, "RETRY_DELAYS", (0.0,))
    real = type(workspace.library_file).read_text

    def locked(self, *args, **kwargs):
        if self.name.startswith("library.json"):
            raise PermissionError("locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(workspace.library_file), "read_text", locked)
    with pytest.raises(PermissionError):
        Library()
    monkeypatch.undo()
    assert not list(workspace.data.glob("library.corrupt-*.json"))
    assert Library().data["playlists"][0]["id"] == "A"


def test_the_project_list_survives_a_damaged_file(workspace):
    first = projects.load()
    projects.create("Second", str(workspace.home / "music2"))
    workspace.projects_file.write_text("", encoding="utf-8")
    registry = projects.load()
    assert [p["id"] for p in registry["projects"]] == [first["projects"][0]["id"]]
    assert json.loads(workspace.projects_file.read_text(encoding="utf-8")) == registry

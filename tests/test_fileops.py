import os

import pytest

from spotcheck import fileops


def test_replace_retries_while_the_target_is_locked(tmp_path, monkeypatch):
    src, dst = tmp_path / "new.mp3", tmp_path / "song.mp3"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")
    real_replace = os.replace
    attempts = []

    def flaky_replace(a, b):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(5, "Access is denied")
        real_replace(a, b)

    monkeypatch.setattr(fileops.os, "replace", flaky_replace)
    monkeypatch.setattr(fileops, "RETRY_DELAYS", (0, 0, 0))
    fileops.replace(src, dst)
    assert dst.read_bytes() == b"new"
    assert len(attempts) == 3


def test_install_reports_a_locked_file_and_leaves_it_untouched(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    src, dst = work / "download.mp3", tmp_path / "song.mp3"
    src.write_bytes(b"new")
    dst.write_bytes(b"old")

    def locked(a, b):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(fileops.os, "replace", locked)
    monkeypatch.setattr(fileops, "RETRY_DELAYS", (0,))
    with pytest.raises(fileops.FileLockedError):
        fileops.install(src, dst)
    assert dst.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["song.mp3"]


def test_leftovers_are_recognised_and_removed(tmp_path):
    temp = fileops.temp_path(tmp_path / "Artist - Song.mp3", "trim")
    temp.write_bytes(b"x")
    (tmp_path / "Artist - Song.mp3").write_bytes(b"x")
    assert fileops.is_temp(temp)
    assert fileops.remove_leftovers(tmp_path) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["Artist - Song.mp3"]

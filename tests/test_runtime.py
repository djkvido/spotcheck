import os
import sys

from spotcheck import runtime


def test_environment_for_child_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    runtime.prepare(False)
    entries = os.environ["PYTHONPATH"].split(os.pathsep)
    assert entries == [str(runtime.spotdl_dir())]
    assert "" not in entries

    runtime.prepare(True, tmp_path / "home")
    assert runtime.packaged()
    assert runtime.packages_dir() == tmp_path / "home" / "packages"
    assert sys.path[0] == str(runtime.packages_dir())
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == str(runtime.packages_dir())
    runtime.prepare(False)


def test_user_folders_are_outside_the_installation():
    assert runtime.user_home().name == "SpotCheck"
    assert runtime.user_music() == runtime.user_music().home() / "Music" / "SpotCheck"

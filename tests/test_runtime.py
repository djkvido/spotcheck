import os
import subprocess
import sys

import pytest

from spotcheck import analysis, runtime


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


def test_helper_programs_run_with_the_console_python(tmp_path, monkeypatch):
    launcher = tmp_path / "SpotCheck.exe"
    launcher.write_bytes(b"")
    monkeypatch.setattr(runtime.sys, "executable", str(launcher))
    monkeypatch.setattr(runtime.os, "name", "nt")
    assert runtime.python() == str(launcher)
    (tmp_path / "python.exe").write_bytes(b"")
    assert runtime.python() == str(tmp_path / "python.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_processes_start_with_a_hidden_window():
    info = analysis.hidden_window()
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW and info.wShowWindow == 0

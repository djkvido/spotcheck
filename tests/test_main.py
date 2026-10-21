import os
import subprocess
import sys

from spotcheck.__main__ import process_alive


def test_process_alive_tells_a_running_process_from_a_finished_one():
    assert process_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not process_alive(child.pid)


def test_a_second_start_finds_spotcheck_on_the_port_it_moved_to(workspace, monkeypatch):
    from spotcheck import __main__

    listening = {8767}
    monkeypatch.setattr(__main__, "running_instance", lambda host, port: port in listening)
    assert __main__.active_port("127.0.0.1", 8765) is None
    __main__.remember_instance(8767)
    assert __main__.active_port("127.0.0.1", 8765) == 8767
    listening.add(8765)
    assert __main__.active_port("127.0.0.1", 8765) == 8765
    __main__.forget_instance()
    assert not __main__.instance_file().exists()


def test_quit_creates_nothing_in_the_current_folder(tmp_path, monkeypatch):
    import pytest

    from spotcheck import __main__

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPOTCHECK_HOME", raising=False)
    monkeypatch.setattr(sys, "argv", ["spotcheck", "--quit"])
    monkeypatch.setattr(__main__, "running_instance", lambda host, port: False)
    with pytest.raises(SystemExit) as stop:
        __main__.main()
    assert stop.value.code == 0
    assert list(tmp_path.iterdir()) == []

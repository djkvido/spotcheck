import os
import subprocess
import sys

from spotcheck.__main__ import process_alive


def test_process_alive_tells_a_running_process_from_a_finished_one():
    assert process_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not process_alive(child.pid)

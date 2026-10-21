import subprocess
import sys
import threading
import time

import pytest

from spotcheck import analysis
from spotcheck.__main__ import process_alive

PARENT = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def wait_until_dead(pid: int, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_a_timeout_stops_the_whole_process_tree():
    seen = []
    with pytest.raises(subprocess.TimeoutExpired):
        analysis.run_streaming([sys.executable, "-c", PARENT], seen.append, timeout=3)
    grandchild = int(seen[0])
    assert wait_until_dead(grandchild)
    assert not analysis._active


def test_run_text_gives_no_input_and_respects_the_timeout():
    result = analysis.run_text([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout=30)
    assert result.stdout.strip() == "''"
    with pytest.raises(subprocess.TimeoutExpired):
        analysis.run_bin([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
    assert not analysis._active


def test_stop_all_ends_running_processes():
    seen = []
    result = {}

    def run():
        result["value"] = analysis.run_streaming([sys.executable, "-c", PARENT], seen.append, timeout=60)

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 20
    while not seen and time.monotonic() < deadline:
        time.sleep(0.1)
    assert analysis.stop_all() == 1
    worker.join(20)
    assert not worker.is_alive()
    assert result["value"].returncode != 0
    assert wait_until_dead(int(seen[0]))

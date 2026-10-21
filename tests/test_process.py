import subprocess
import sys
import threading
import time

import pytest

from spotcheck import analysis
from spotcheck.__main__ import process_alive
from spotcheck.errors import Cancelled

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
        try:
            analysis.run_streaming([sys.executable, "-c", PARENT], seen.append, timeout=60)
        except analysis.ProcessStopped as exc:
            result["stopped"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 20
    while not seen and time.monotonic() < deadline:
        time.sleep(0.1)
    assert analysis.stop_all() == 1
    worker.join(20)
    assert not worker.is_alive()
    assert isinstance(result["stopped"], Cancelled)
    assert wait_until_dead(int(seen[0]))


def test_cancelling_a_job_leaves_other_processes_alone():
    seen = {"job": [], "other": []}
    results = {}

    def run(name):
        try:
            analysis.run_streaming([sys.executable, "-c", PARENT], seen[name].append, timeout=60)
            results[name] = "finished"
        except analysis.ProcessStopped:
            results[name] = "stopped"
        except subprocess.TimeoutExpired:
            results[name] = "timeout"

    job = threading.Thread(target=run, args=("job",), name="spotcheck-job_0")
    other = threading.Thread(target=run, args=("other",), name="request")
    job.start()
    other.start()
    deadline = time.monotonic() + 20
    while not (seen["job"] and seen["other"]) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert analysis.stop_all(jobs_only=True) == 1
    job.join(20)
    assert results == {"job": "stopped"}
    assert analysis.stop_all() == 1
    other.join(20)
    assert results["other"] == "stopped"

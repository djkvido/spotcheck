from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable

from . import analysis

BLOCKED = re.compile(
    r"confirm you.?re not a bot|sign in to confirm(?! your age)|HTTP Error 429|too many requests|rate.?limit|"
    r"try again later",
    re.I,
)
FORBIDDEN = re.compile(
    r"HTTP Error 403|Requested format is not available|nsig extraction failed|Signature extraction failed",
    re.I,
)
LOGIN = re.compile(
    r"login required|log in to|you need to log in|requires? authentication|only available for registered users|"
    r"this content isn.t available right now|private video|video is private|content is not available|"
    r"empty media response",
    re.I,
)
ERROR_LINE = re.compile(r"\bERROR\b|Error:|AudioProviderError|DownloadError")
NOT_FOUND = re.compile(r"LookupError|No results found")
RETRIES = (4.0, 12.0)
COOLDOWN = 300.0
NETWORK = ["--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3"]

_lock = threading.Lock()
_state = {"blocked": 0.0, "reported": 0.0}


def blocked(text: str | None) -> bool:
    return bool(text and BLOCKED.search(text)) and not login_required(text)


def login_required(text: str | None) -> bool:
    return bool(text and LOGIN.search(text))


def forbidden(text: str | None) -> bool:
    return bool(text and FORBIDDEN.search(text))


def output(result: subprocess.CompletedProcess | None) -> str:
    return result.stderr if result is not None and isinstance(result.stderr, str) else ""


def error_lines(result: subprocess.CompletedProcess | None) -> str:
    if result is None:
        return ""
    text = "\n".join(part for part in (result.stderr, result.stdout) if isinstance(part, str))
    return "\n".join(line for line in text.splitlines() if ERROR_LINE.search(line) and not NOT_FOUND.search(line))


def cooling() -> bool:
    with _lock:
        return bool(_state["blocked"]) and time.monotonic() - _state["blocked"] < COOLDOWN


def should_report() -> bool:
    with _lock:
        now = time.monotonic()
        if _state["reported"] and now - _state["reported"] < COOLDOWN:
            return False
        _state["reported"] = now
        return True


def reset() -> None:
    with _lock:
        _state.update(blocked=0.0, reported=0.0)


def run(
    cmd: list[str],
    timeout: float,
    done: Callable[[subprocess.CompletedProcess], bool],
    cwd: str | None = None,
    text: Callable[[subprocess.CompletedProcess | None], str] = output,
    shared: bool = True,
) -> subprocess.CompletedProcess:
    delays = () if shared and cooling() else RETRIES
    result = None
    for delay in (*delays, None):
        result = analysis.run_text(cmd, timeout=timeout, cwd=cwd)
        if done(result):
            if shared:
                with _lock:
                    _state["blocked"] = 0.0
            return result
        if delay is None or not blocked(text(result)):
            break
        analysis.pause(delay)
    if shared and blocked(text(result)):
        with _lock:
            _state["blocked"] = time.monotonic()
    return result

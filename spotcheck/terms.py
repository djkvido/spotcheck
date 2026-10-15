from __future__ import annotations

import json
import time

from .config import paths

VERSION = "1"
URLS = {
    "en": "https://github.com/djkvido/spotcheck/blob/main/TERMS.md",
    "cs": "https://github.com/djkvido/spotcheck/blob/main/TERMS.cs.md",
}


def _file():
    return paths().home / "consent.json"


def accepted() -> bool:
    try:
        return json.loads(_file().read_text(encoding="utf-8")).get("terms") == VERSION
    except (OSError, ValueError, AttributeError):
        return False


def accept() -> None:
    record = {"terms": VERSION, "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    _file().write_text(json.dumps(record, indent=2), encoding="utf-8")


def public() -> dict:
    return {"version": VERSION, "accepted": accepted(), "urls": URLS}

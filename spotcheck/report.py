from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from .library import Library, track_key

STATUS_NAMES = {
    "ok": "verified",
    "partial": "no preview",
    "review": "needs review",
    "bad": "wrong",
    "missing": "missing",
    "unchecked": "not checked",
}
ATTENTION = ("bad", "review", "missing")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def summary(lib: Library, project: dict[str, Any]) -> dict[str, Any]:
    totals = dict.fromkeys(STATUS_NAMES, 0)
    playlists, problems = [], []
    with lib.lock:
        for pl in lib.data["playlists"]:
            counts = dict.fromkeys(STATUS_NAMES, 0)
            for tr in pl["tracks"]:
                status = tr.get("status") if tr.get("status") in STATUS_NAMES else "unchecked"
                counts[status] += 1
                totals[status] += 1
                if status in ATTENTION:
                    problems.append(
                        {
                            "key": track_key(pl, tr),
                            "playlist": pl["name"],
                            "artist": tr["artist"],
                            "title": tr["name"],
                            "status": status,
                            "findings": [n["code"] for n in tr.get("notes") or []],
                            "file": tr.get("file"),
                        }
                    )
            playlists.append({"id": pl["id"], "name": pl["name"], "tracks": len(pl["tracks"]), "statuses": counts})
    rank = {status: n for n, status in enumerate(ATTENTION)}
    problems.sort(key=lambda p: (rank[p["status"]], p["playlist"].casefold(), p["artist"].casefold()))
    return {
        "project": project["name"],
        "music": project["music"],
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tracks": sum(totals.values()),
        "statuses": totals,
        "playlists": playlists,
        "problems": problems,
    }


def counts_text(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {STATUS_NAMES[status]}" for status, n in counts.items() if n) or "no tracks"


def as_text(data: dict[str, Any]) -> str:
    lines = [
        f"SpotCheck report for {data['project']} ({data['created']})",
        f"Music folder: {data['music']}",
        f"{data['tracks']} tracks: {counts_text(data['statuses'])}",
    ]
    if data["playlists"]:
        lines += ["", "Playlists:"]
        lines += [f"  {pl['name']}: {pl['tracks']} tracks, {counts_text(pl['statuses'])}" for pl in data["playlists"]]
    if data["problems"]:
        lines += ["", f"Needs attention ({len(data['problems'])}):"]
        for p in data["problems"]:
            findings = f" ({', '.join(p['findings'])})" if p["findings"] else ""
            lines.append(f"  [{STATUS_NAMES[p['status']]}] {p['playlist']}: {p['artist']} - {p['title']}{findings}")
    return "\n".join(lines)


def plain_cell(value: str) -> str:
    return f"'{value}" if value.startswith(FORMULA_START) else value


def as_csv(data: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["playlist", "artist", "title", "status", "findings", "file"])
    for p in data["problems"]:
        status, findings = STATUS_NAMES[p["status"]], " ".join(p["findings"])
        cells = [p["playlist"], p["artist"], p["title"], status, findings, p["file"] or ""]
        writer.writerow([plain_cell(c) for c in cells])
    return out.getvalue().rstrip("\n")


def render(data: dict[str, Any], fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    if fmt == "csv":
        return as_csv(data)
    return as_text(data)


def exit_code(data: dict[str, Any]) -> int:
    return 2 if data["statuses"]["bad"] or data["statuses"]["review"] else 0

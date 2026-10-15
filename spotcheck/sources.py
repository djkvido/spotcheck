from __future__ import annotations

import json
import re
import string
import subprocess
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp
from rapidfuzz import fuzz

from . import __version__, analysis
from .config import paths

USER_AGENT = {"User-Agent": f"SpotCheck/{__version__}"}
DANCE_WORDS = (
    r"(?:bpm|samba|rumba|cha\s*-?\s*cha(?:\s*-?\s*cha)?|chachacha|paso\s*doble|pasodoble|jive|tempo|"
    r"sb|merengue|salsa|bachata)"
)
NO_PREVIEW_TTL = 7 * 86400
ITUNES_DELAY = 3.2

YOUTUBE_ID = re.compile(r"(?:[?&]v=|youtu\.be/)([A-Za-z0-9_-]{11})")
SOUNDCLOUD_ID = re.compile(r"soundcloud(?:%3A|:)tracks(?:%3A|:)(\d+)|soundcloud\.com/tracks/(\d+)")
SOURCE_URL = re.compile(r"https?://(?:www\.|music\.|api\.)?(?:youtube\.com|youtu\.be|soundcloud\.com)/\S+")

_index_lock = threading.Lock()
_itunes_lock = threading.Lock()
_ytmusic_lock = threading.Lock()
_ytmusic = None


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean_title(name: str) -> str:
    t = re.sub(r"\(\(?[^()]*" + DANCE_WORDS + r"[^()]*\)?\)", " ", name, flags=re.I)
    t = re.sub(r"\[[^\]]*" + DANCE_WORDS + r"[^\]]*\]", " ", t, flags=re.I)
    t = re.sub(r"\s+-\s+[^-]*" + DANCE_WORDS + r"[^-]*$", " ", t, flags=re.I)
    t = re.sub(r"\b\d{2,3}\s*bpm\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -/()[]") or name


def dance_hint(list_name: str) -> str:
    last = re.split(r"\s+-\s+", re.sub(r"\s+", " ", list_name or ""))[-1].lower()
    return last if re.search(DANCE_WORDS, last) else ""


def words_pattern(words: str) -> re.Pattern[str]:
    try:
        return re.compile(r"\b(" + words + r")\b", re.I)
    except re.error:
        return re.compile(r"(?!)")


def http_get(url: str, binary: bool = False, timeout: float = 25) -> Any:
    req = urllib.request.Request(url, headers=USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else json.loads(data.decode("utf-8"))


def source_key(url: str | None) -> str | None:
    if not url:
        return None
    m = YOUTUBE_ID.search(url)
    if m:
        return m.group(1)
    m = SOUNDCLOUD_ID.search(url)
    if m:
        return "sc:" + (m.group(1) or m.group(2))
    return None


def file_source(path: Path) -> dict | None:
    m = SOURCE_URL.search(analysis.tag_text(path))
    key = source_key(m.group(0)) if m else None
    if key is None:
        return None
    url = m.group(0) if key.startswith("sc:") else f"https://www.youtube.com/watch?v={key}"
    return {"id": key, "url": url}


def _deezer(song: dict, errors: list[str]) -> list[dict]:
    title = clean_title(song["name"])
    queries = [
        f'artist:"{song["artist"]}" track:"{title}"',
        f"{song['artist']} {song['name']}",
        f"{song['artist']} {title}",
    ]
    found: list[dict] = []
    for q in dict.fromkeys(queries):
        try:
            data = http_get("https://api.deezer.com/search?limit=10&q=" + urllib.parse.quote(q))
        except Exception as exc:
            errors.append(f"Deezer: {exc}")
            continue
        if "error" in data:
            errors.append(f"Deezer: {data['error']}")
            continue
        time.sleep(0.15)
        found += [
            {
                "src": "Deezer",
                "title": it.get("title", ""),
                "artist": (it.get("artist") or {}).get("name", ""),
                "duration": it.get("duration", 0),
                "preview": it.get("preview"),
                "link": it.get("link", ""),
            }
            for it in data.get("data", [])
        ]
    return found


def _itunes(song: dict, errors: list[str]) -> list[dict]:
    title = clean_title(song["name"])
    found: list[dict] = []
    with _itunes_lock:
        for q in dict.fromkeys([f"{song['artist']} {title}", f"{song['artist']} {song['name']}"]):
            try:
                data = http_get(
                    "https://itunes.apple.com/search?media=music&entity=song&limit=10&term=" + urllib.parse.quote(q)
                )
            except Exception as exc:
                errors.append(f"iTunes: {exc}")
                continue
            time.sleep(ITUNES_DELAY)
            found += [
                {
                    "src": "iTunes",
                    "title": it.get("trackName", ""),
                    "artist": it.get("artistName", ""),
                    "duration": (it.get("trackTimeMillis") or 0) / 1000,
                    "preview": it.get("previewUrl"),
                    "link": it.get("trackViewUrl", ""),
                }
                for it in data.get("results", [])
            ]
    return found


def pick_preview(song: dict, items: list[dict], st: dict) -> dict | None:
    not_original = words_pattern(st["not_original_words"])
    want_full, want_clean = norm(song["name"]), norm(clean_title(song["name"]))
    artists = song.get("artists") or [song["artist"]]
    best: tuple[tuple[float, float], dict] | None = None
    for it in items:
        if not it.get("preview"):
            continue
        if not_original.search(it["title"]) and not not_original.search(song["name"]):
            continue
        title = norm(it["title"])
        title_score = max(fuzz.token_set_ratio(want_full, title), fuzz.token_set_ratio(want_clean, title))
        artist_score = max(fuzz.partial_ratio(norm(a), norm(it["artist"])) for a in artists)
        delta = abs(it["duration"] - song["duration"])
        if title_score >= 70 and artist_score >= 60 and delta <= 5:
            key = (delta, -title_score)
            if best is None or key < best[0]:
                best = (key, it)
    return best[1] if best else None


def _index_path() -> Path:
    return paths().previews / "index.json"


def _load_index() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _store_index_entry(track_id: str, entry: dict) -> None:
    with _index_lock:
        index = _load_index()
        index[track_id] = entry
        _index_path().write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


def get_preview(song: dict, st: dict, force: bool = False) -> dict | None:
    track_id = song["id"]
    with _index_lock:
        hit = _load_index().get(track_id)
    if hit and not force:
        if hit.get("file") and (paths().previews / hit["file"]).exists():
            return hit
        if hit.get("none") and time.time() - hit.get("t", 0) < NO_PREVIEW_TTL:
            return None

    errors: list[str] = []
    item = None
    if st.get("use_deezer", True):
        item = pick_preview(song, _deezer(song, errors), st)
    if item is None and st.get("use_itunes", True):
        item = pick_preview(song, _itunes(song, errors), st)

    result = None
    if item:
        is_aac = item["src"] == "iTunes" or ".m4a" in item["preview"].split("?")[0]
        name = re.sub(r"[^A-Za-z0-9]", "", track_id) + (".m4a" if is_aac else ".mp3")
        try:
            (paths().previews / name).write_bytes(http_get(item["preview"], binary=True))
            result = {
                "src": item["src"],
                "title": item["title"],
                "artist": item["artist"],
                "link": item["link"],
                "file": name,
            }
        except Exception as exc:
            errors.append(f"preview download: {exc}")
    if result is None and errors:
        return None
    _store_index_entry(track_id, result or {"none": True, "t": time.time()})
    return result


class _QueryFormatter(string.Formatter):
    def get_field(self, field_name, args, kwargs):
        if not field_name.isidentifier():
            raise KeyError(field_name)
        return kwargs[field_name], field_name


_formatter = _QueryFormatter()


def build_queries(song: dict, st: dict) -> list[str]:
    context = {
        "artist": song["artist"],
        "title": clean_title(song["name"]),
        "name": song["name"],
        "album": song.get("album", ""),
        "hint": dance_hint(song.get("list_name", "")),
    }
    queries: list[str] = []
    for template in st.get("queries", []):
        if "{hint}" in template and not context["hint"]:
            continue
        try:
            q = re.sub(r"\s+", " ", _formatter.format(template, **context)).strip()
        except (KeyError, IndexError, ValueError):
            continue
        if q and q not in queries:
            queries.append(q)
    return queries


def search_youtube_music(query: str, limit: int = 5) -> list[dict]:
    global _ytmusic
    try:
        with _ytmusic_lock:
            if _ytmusic is None:
                from ytmusicapi import YTMusic

                _ytmusic = YTMusic()
            results = _ytmusic.search(query, filter="songs", limit=limit)
    except Exception:
        return []
    return [
        {
            "id": r["videoId"],
            "url": f"https://www.youtube.com/watch?v={r['videoId']}",
            "title": r.get("title") or "",
            "channel": ", ".join(a.get("name", "") for a in r.get("artists") or []),
            "album": (r.get("album") or {}).get("name", ""),
            "duration": r.get("duration_seconds"),
            "source": "YouTube Music",
            "official": True,
        }
        for r in results[:limit]
        if r.get("videoId")
    ]


def _ytdlp_search(prefix: str, query: str, limit: int) -> list[dict]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"{prefix}{limit}:{query}", download=False).get("entries") or []
    except Exception:
        return []


def search_youtube(query: str, limit: int = 10) -> list[dict]:
    found = []
    for e in _ytdlp_search("ytsearch", query, limit):
        if not e or not e.get("id"):
            continue
        channel = e.get("channel") or e.get("uploader") or ""
        found.append(
            {
                "id": e["id"],
                "url": f"https://www.youtube.com/watch?v={e['id']}",
                "title": e.get("title") or "",
                "channel": channel,
                "duration": e.get("duration"),
                "source": "YouTube",
                "official": channel.lower().endswith("- topic"),
            }
        )
    return found


def search_soundcloud(query: str, limit: int = 8) -> list[dict]:
    found = []
    for e in _ytdlp_search("scsearch", query, limit):
        url = (e or {}).get("url")
        key = source_key(url)
        if not key:
            continue
        found.append(
            {
                "id": key,
                "url": url,
                "page": e.get("webpage_url") or url,
                "title": e.get("title") or "",
                "channel": e.get("uploader") or "",
                "duration": e.get("duration"),
                "source": "SoundCloud",
                "official": False,
            }
        )
    return found


SEARCHERS: dict[str, tuple[Callable[[str], list[dict]], int | None]] = {
    "youtube-music": (search_youtube_music, 2),
    "youtube": (search_youtube, None),
    "soundcloud": (search_soundcloud, 2),
}


def score_candidate(song: dict, cand: dict, bad: re.Pattern[str]) -> float:
    title, channel = cand["title"], cand["channel"]
    score = -abs(cand["duration"] - song["duration"])
    score += fuzz.token_set_ratio(norm(song["name"]), norm(title)) / 50.0
    if norm(song["artist"]) and norm(song["artist"]) in norm(f"{title} {channel}"):
        score += 2
    if cand.get("official"):
        score += 3
    if cand.get("album") and norm(cand["album"]) == norm(song.get("album", "")):
        score += 1
    if bad.search(title) and not bad.search(song["name"]):
        score -= 6
    if cand["source"] == "SoundCloud":
        score -= 1
    return score


def find_candidates(song: dict, exclude: set[str], st: dict) -> list[dict]:
    bad = words_pattern(st["bad_source_words"])
    tolerance = float(st["dur_tolerance"])
    queries = build_queries(song, st)
    seen: dict[str, dict] = {}
    for source in st["candidate_sources"]:
        search, query_count = SEARCHERS[source]
        for q in queries if query_count is None else queries[:query_count]:
            for cand in search(q):
                length = cand.get("duration")
                if length is None or cand["id"] in seen or cand["id"] in exclude:
                    continue
                if abs(length - song["duration"]) > tolerance:
                    continue
                cand["diff"] = round(length - song["duration"], 1)
                cand["score"] = round(score_candidate(song, cand, bad), 2)
                seen[cand["id"]] = cand
    return sorted(seen.values(), key=lambda c: -c["score"])[: int(st["max_candidates"])]


def _candidate_stem(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


def cached_candidate(key: str) -> Path | None:
    stem = _candidate_stem(key)
    for path in paths().candidates.glob(f"{stem}.*"):
        if path.suffix not in (".part", ".ytdl", ".tmp") and path.stat().st_size > 10_000:
            return path
    return None


def fetch_candidate(cand: dict, ytdlp_cmd: list[str]) -> Path | None:
    cached = cached_candidate(cand["id"])
    if cached is not None:
        return cached
    cmd = [
        *ytdlp_cmd,
        "--no-playlist",
        "-q",
        "--no-warnings",
        "-f",
        "bestaudio/best",
        "--ffmpeg-location",
        analysis.ffmpeg(),
        "-o",
        str(paths().candidates / f"{_candidate_stem(cand['id'])}.%(ext)s"),
        "--",
        cand["url"],
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=240, creationflags=analysis.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return cached_candidate(cand["id"])

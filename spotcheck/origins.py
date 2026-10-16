from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from rapidfuzz import fuzz

from . import analysis, sources

SPOTIFY = re.compile(r"^https://open\.spotify\.com/(?:intl-[a-z]+/)?(?:playlist|album)/([A-Za-z0-9]+)")
YOUTUBE = re.compile(
    r"^https://(?:www\.|m\.|music\.)?youtube\.com/(?:playlist|watch|browse)\?\S*?\blist=([A-Za-z0-9_-]+)"
)
SOUNDCLOUD = re.compile(r"^https://(?:www\.|m\.)?soundcloud\.com/([A-Za-z0-9_-]+)/sets/([A-Za-z0-9_-]+)")
BANDCAMP = re.compile(r"^https://([a-z0-9-]+)\.bandcamp\.com/album/([A-Za-z0-9_-]+)")
YOUTUBE_PRIVATE = ("LL", "WL", "LM", "RD")

TOPIC = re.compile(r"\s*-\s*topic$", re.I)
CHANNEL_NOISE = re.compile(r"\s*(?:vevo|official(?:\s+channel)?|music)$", re.I)
TITLE_NOISE = re.compile(
    r"\s*[(\[](?:[^)\]]*\b(?:official|lyrics?|audio|video|visuali[sz]er|hd|hq|4k|mv|clip)\b[^)\]]*)[)\]]", re.I
)
MATCH_TITLE = 75
MATCH_ARTIST = 60
MATCH_LENGTH = 45.0
IDENTIFY_DELAY = 0.12


def classify(url: str) -> tuple[str, str] | None:
    url = url.strip()
    m = SPOTIFY.match(url)
    if m:
        return "spotify", m.group(1)
    m = YOUTUBE.match(url)
    if m:
        if m.group(1).startswith(YOUTUBE_PRIVATE):
            return None
        return "youtube", "yt-" + m.group(1)
    m = SOUNDCLOUD.match(url)
    if m:
        return "soundcloud", f"sc-{m.group(1)}-{m.group(2)}"
    m = BANDCAMP.match(url)
    if m:
        return "bandcamp", f"bc-{m.group(1)}-{m.group(2)}"
    return None


def canonical_url(url: str) -> str:
    found = classify(url)
    if found and found[0] == "youtube":
        list_id = found[1][3:]
        music = urllib.parse.urlparse(url.strip()).hostname == "music.youtube.com"
        host = "music.youtube.com" if music else "www.youtube.com"
        return f"https://{host}/playlist?list={urllib.parse.quote(list_id)}"
    return url.strip()


def split_title(title: str, channel: str) -> tuple[str, str]:
    clean = re.sub(r"\s+", " ", TITLE_NOISE.sub("", title or "")).strip() or (title or "").strip()
    channel = (channel or "").strip()
    if TOPIC.search(channel):
        return TOPIC.sub("", channel).strip(), clean
    m = re.match(r"^(.+?)\s+[-\N{EN DASH}\N{EM DASH}]\s+(.+)$", clean)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return CHANNEL_NOISE.sub("", channel).strip() or channel, clean


def identify(artist: str, title: str, duration: float) -> dict[str, Any] | None:
    query = f"{artist} {sources.clean_title(title)}".strip()
    try:
        data = sources.http_get("https://api.deezer.com/search?limit=10&q=" + urllib.parse.quote(query))
    except Exception:
        return None
    want_title, want_artist = sources.norm(title), sources.norm(artist)
    best: tuple[tuple[float, float], dict] | None = None
    for item in data.get("data", []) if isinstance(data, dict) else []:
        found_title = sources.norm(item.get("title", ""))
        found_artist = sources.norm((item.get("artist") or {}).get("name", ""))
        title_score = fuzz.token_set_ratio(want_title, found_title)
        artist_score = fuzz.partial_ratio(want_artist, found_artist) if want_artist else 0
        delta = abs(float(item.get("duration") or 0) - duration) if duration else 0.0
        if title_score < MATCH_TITLE or artist_score < MATCH_ARTIST or delta > MATCH_LENGTH:
            continue
        key = (-(title_score + artist_score), delta)
        if best is None or key < best[0]:
            best = (key, item)
    if best is None:
        return None
    item = best[1]
    album = item.get("album") or {}
    return {
        "title": item.get("title") or title,
        "artist": (item.get("artist") or {}).get("name") or artist,
        "album": album.get("title") or "",
        "duration": float(item.get("duration") or 0),
        "cover_url": album.get("cover_xl") or album.get("cover_big"),
        "link": item.get("link"),
    }


def _entry_url(entry: dict, origin: str) -> str | None:
    if origin == "youtube" and entry.get("id"):
        return f"https://www.youtube.com/watch?v={entry['id']}"
    return entry.get("webpage_url") or entry.get("url")


def _thumbnail(entry: dict, origin: str) -> str | None:
    if origin == "youtube" and entry.get("id"):
        return f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"
    return entry.get("thumbnail")


def parse_entry(entry: dict) -> tuple[str, str]:
    artists = entry.get("artists") or ([entry["artist"]] if entry.get("artist") else None)
    if artists and entry.get("track"):
        return ", ".join(artists), entry["track"]
    return split_title(entry.get("title") or "", entry.get("channel") or entry.get("uploader") or "")


def entry_song(entry: dict, origin: str, list_name: str, position: int, official: dict | None) -> dict[str, Any] | None:
    url = _entry_url(entry, origin)
    if not url or not entry.get("id"):
        return None
    artist, title = parse_entry(entry)
    duration = float(entry.get("duration") or 0)
    prefix = {"youtube": "yt", "soundcloud": "sc", "bandcamp": "bc"}[origin]
    song = {
        "song_id": f"{prefix}-{entry['id']}",
        "url": url,
        "name": title,
        "artist": artist,
        "artists": [artist],
        "album_name": entry.get("album") or "",
        "duration": duration,
        "cover_url": _thumbnail(entry, origin),
        "year": None,
        "list_name": list_name,
        "list_position": position,
        "origin": origin,
        "source_url": url,
        "source_duration": duration,
        "official": None,
    }
    if official:
        song.update(
            {
                "name": official["title"],
                "artist": official["artist"],
                "artists": [official["artist"]],
                "album_name": official["album"] or song["album_name"],
                "duration": official["duration"] or duration,
                "cover_url": official["cover_url"] or song["cover_url"],
                "official": official,
            }
        )
    return song


def is_snippet(entry: dict) -> bool:
    url = urllib.parse.urlparse(str(entry.get("url") or ""))
    host = url.hostname or ""
    soundcloud_cdn = host == "sndcdn.com" or host.endswith(".sndcdn.com")
    return "/preview/" in url.path or ((entry.get("duration") or 0) == 30.0 and soundcloud_cdn)


def load(
    url: str,
    ytdlp_cmd: list[str],
    timeout: float = 600,
    on_error: Callable[[str, str], None] | None = None,
) -> tuple[str | None, list[dict], int]:
    origin = (classify(url) or ("", ""))[0]
    cmd = [*ytdlp_cmd, "-J", "--no-warnings", "--ignore-errors"]
    if origin == "youtube":
        cmd.append("--flat-playlist")

    def fail(code: str, detail: str = "") -> tuple[None, list, int]:
        if on_error:
            on_error(code, detail)
        return None, [], 0

    try:
        r = analysis.run_text([*cmd, "--", url], timeout=timeout)
    except subprocess.TimeoutExpired:
        return fail("ytdlp_timeout", f"{timeout:.0f} s")
    except (OSError, subprocess.SubprocessError) as exc:
        return fail("ytdlp_error", str(exc))
    if not r.stdout.strip():
        return fail("ytdlp_failed", r.stderr or f"exit code {r.returncode}")
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return fail("ytdlp_bad_output", r.stdout[-400:])
    if not isinstance(info, dict) or not info.get("entries"):
        return fail("ytdlp_empty", r.stderr)
    name = re.sub(r"^Album - ", "", info.get("title") or "") or "Playlist"
    songs, unavailable = [], 0
    for position, entry in enumerate(info["entries"], 1):
        if not entry or entry.get("availability") in ("needs_auth", "subscriber_only", "premium_only"):
            unavailable += 1
            continue
        if origin == "soundcloud" and is_snippet(entry):
            unavailable += 1
            continue
        if origin == "youtube" and entry.get("title") in ("[Private video]", "[Deleted video]"):
            unavailable += 1
            continue
        artist, title = parse_entry(entry)
        official = identify(artist, title, float(entry.get("duration") or 0))
        time.sleep(IDENTIFY_DELAY)
        song = entry_song(entry, origin, name, position, official)
        if song:
            songs.append(song)
    return name, songs, unavailable

from __future__ import annotations

import copy
import re
from string import Formatter
from typing import Any

AUDIO_PROVIDERS = ("youtube-music", "youtube", "soundcloud", "bandcamp", "piped")
CANDIDATE_SOURCES = ("youtube-music", "youtube", "soundcloud")
CHOICES: dict[str, tuple[str, ...]] = {
    "audio_format": ("mp3", "opus", "m4a"),
    "mp3_bitrate": ("320k", "256k", "192k", "128k"),
}

DEFAULTS: dict[str, Any] = {
    "accept_ber": 0.30,
    "unsure_ber": 0.40,
    "strong_ber": 0.15,
    "auto_replace": False,
    "keep_backups": 3,
    "trim_silence": True,
    "min_silence": 1.0,
    "keep_lead": 0.3,
    "keep_tail": 0.5,
    "min_bitrate": 96,
    "min_cutoff_khz": 15.0,
    "audio_format": "mp3",
    "mp3_bitrate": "320k",
    "number_files": False,
    "write_playlists": True,
    "candidate_sources": ["youtube-music", "youtube", "soundcloud"],
    "max_candidates": 6,
    "dur_tolerance": 6.0,
    "exact_diff": 2.5,
    "use_deezer": True,
    "use_itunes": True,
    "audio_providers": ["youtube-music", "youtube"],
    "queries": [
        "{artist} {title}",
        "{artist} - {title} (Official Audio)",
        "{title} {artist} topic",
        "{artist} {name}",
        "{title}",
        "{artist} {title} {hint}",
        "{title} {hint}",
    ],
    "bad_source_words": (
        "live|cover|tutorial|lesson|reaction|karaoke|instrumental|acoustic|slowed|sped up|"
        "nightcore|8d|remake|piano version|choreograph\\w*|performance|show"
    ),
    "not_original_words": (
        "live|piano version|piano|karaoke|cover|tribute|instrumental|acoustic version|"
        "made famous|originally performed|in the style of"
    ),
    "auto_check": True,
    "auto_sync_hours": 24,
    "auto_download_new": True,
    "threads": 3,
}

BOUNDS: dict[str, tuple[float, float]] = {
    "accept_ber": (0.02, 0.48),
    "unsure_ber": (0.05, 0.50),
    "strong_ber": (0.0, 0.40),
    "keep_backups": (1, 20),
    "min_bitrate": (32, 320),
    "min_cutoff_khz": (10.0, 20.0),
    "min_silence": (0.2, 30),
    "keep_lead": (0, 5),
    "keep_tail": (0, 5),
    "max_candidates": (1, 25),
    "dur_tolerance": (0.5, 60),
    "exact_diff": (0.2, 30),
    "threads": (1, 8),
    "auto_sync_hours": (0, 168),
}


QUERY_FIELDS = ("artist", "title", "name", "album", "hint")


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, list):
        items = value if isinstance(value, list) else str(value).splitlines()
        return [str(x).strip() for x in items if str(x).strip()]
    return str(value)


def merge(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = defaults()
    result.update({k: v for k, v in current.items() if k in DEFAULTS})
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        try:
            result[key] = _coerce(key, value)
        except (TypeError, ValueError):
            continue
    return sanitize(result)


def sanitize(st: dict[str, Any]) -> dict[str, Any]:
    for key, (lo, hi) in BOUNDS.items():
        st[key] = type(DEFAULTS[key])(min(max(st[key], lo), hi))
    st["unsure_ber"] = max(st["unsure_ber"], st["accept_ber"])
    st["strong_ber"] = min(st["strong_ber"], st["accept_ber"])
    for key, allowed in (("audio_providers", AUDIO_PROVIDERS), ("candidate_sources", CANDIDATE_SOURCES)):
        st[key] = [p for p in dict.fromkeys(st[key]) if p in allowed] or list(DEFAULTS[key])
    if not st["queries"]:
        st["queries"] = list(DEFAULTS["queries"])
    if not (st["use_deezer"] or st["use_itunes"]):
        st["use_deezer"] = True
    for key, allowed in CHOICES.items():
        if st[key] not in allowed:
            st[key] = DEFAULTS[key]
    return st


def _query_error(template: str) -> dict[str, Any] | None:
    try:
        fields = [f for _, f, _, _ in Formatter().parse(template) if f is not None]
    except ValueError:
        return {"code": "query_syntax", "params": {"query": template}}
    unknown = [f for f in fields if f not in QUERY_FIELDS]
    if unknown:
        return {"code": "query_unknown_field", "params": {"query": template, "field": unknown[0]}}
    if not fields:
        return {"code": "query_no_field", "params": {"query": template}}
    return None


def validate(changes: dict[str, Any], current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    errors: dict[str, dict[str, Any]] = {}
    values = {**current}
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        try:
            values[key] = _coerce(key, value)
        except (TypeError, ValueError):
            errors[key] = {"code": "not_a_number", "params": {}}
    for key, (lo, hi) in BOUNDS.items():
        if key not in errors and not lo <= values[key] <= hi:
            errors[key] = {"code": "out_of_range", "params": {"min": lo, "max": hi}}
    if not errors.keys() & {"accept_ber", "unsure_ber"} and values["unsure_ber"] < values["accept_ber"]:
        errors["unsure_ber"] = {"code": "unsure_below_accept", "params": {}}
    if not errors.keys() & {"accept_ber", "strong_ber"} and values["strong_ber"] > values["accept_ber"]:
        errors["strong_ber"] = {"code": "strong_above_accept", "params": {}}
    for key in ("bad_source_words", "not_original_words"):
        pattern = str(values[key]).strip()
        if not pattern:
            errors[key] = {"code": "required", "params": {}}
            continue
        try:
            re.compile(pattern, re.I)
        except re.error as exc:
            errors[key] = {"code": "invalid_regex", "params": {"error": str(exc)}}
    for key, allowed in (("audio_providers", AUDIO_PROVIDERS), ("candidate_sources", CANDIDATE_SOURCES)):
        if not [p for p in values[key] if p in allowed]:
            errors[key] = {"code": "select_one", "params": {}}
    if not (values["use_deezer"] or values["use_itunes"]):
        errors["use_deezer"] = {"code": "preview_source_required", "params": {}}
    for key, allowed in CHOICES.items():
        if values[key] not in allowed:
            errors[key] = {"code": "invalid_choice", "params": {}}
    queries = values["queries"]
    if not queries:
        errors["queries"] = {"code": "required", "params": {}}
    else:
        problem = next((e for e in map(_query_error, queries) if e), None)
        if problem:
            errors["queries"] = problem
        elif all("{hint}" in q for q in queries):
            errors["queries"] = {"code": "query_needs_plain", "params": {}}
    return errors


def meta() -> dict[str, Any]:
    return {
        "bounds": {k: list(v) for k, v in BOUNDS.items()},
        "audio_providers": list(AUDIO_PROVIDERS),
        "candidate_sources": list(CANDIDATE_SOURCES),
        "query_fields": list(QUERY_FIELDS),
        "choices": {k: list(v) for k, v in CHOICES.items()},
        "defaults": defaults(),
    }

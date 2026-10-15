import pytest

from spotcheck import settings, sources
from spotcheck.sources import (
    build_queries,
    clean_title,
    dance_hint,
    norm,
    pick_preview,
    source_key,
    words_pattern,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sunny Morning (Samba / 51 Bpm)", "Sunny Morning"),
        ("Little Steps ((Chachacha / 31 Bpm))", "Little Steps"),
        ("Carnival Lights [Samba - 51BPM]", "Carnival Lights"),
        ("Slow Rain - Rumba / 24 BPM", "Slow Rain"),
        ("Weekend - Sb 51BPM", "Weekend"),
        ("Paper Boats", "Paper Boats"),
        ("Afterglow - 2020 Remix", "Afterglow - 2020 Remix"),
    ],
)
def test_clean_title_removes_dance_annotations(raw, expected):
    assert clean_title(raw) == expected


def test_norm_strips_diacritics_and_punctuation():
    assert norm("España Cañí \N{EN DASH} (Paso Doble)") == "espana cani paso doble"


def test_dance_hint_uses_last_part_of_playlist_name():
    assert dance_hint("Dance Club - Latino - Samba") == "samba"
    assert dance_hint("Dance Club - Street Dance") == ""


def test_invalid_user_pattern_matches_nothing():
    assert words_pattern("live|(").search("live version") is None


def test_build_queries_skips_hint_and_unsafe_templates():
    st = settings.defaults()
    st["queries"] = ["{artist} {title}", "{title} {hint}", "{artist.__class__}", "{}", "{unknown}"]
    song = {"artist": "Coral Avenue", "name": "Carnival Lights [Samba - 51BPM]", "list_name": "Club - Street Dance"}
    assert build_queries(song, st) == ["Coral Avenue Carnival Lights"]


def test_build_queries_includes_hint_when_available():
    st = settings.defaults()
    st["queries"] = ["{title} {hint}"]
    song = {"artist": "X", "name": "Sunny Morning (Samba / 51 Bpm)", "list_name": "Dance Club - Latino - Samba"}
    assert build_queries(song, st) == ["Sunny Morning samba"]


def test_pick_preview_prefers_matching_studio_version():
    song = {
        "id": "1",
        "name": "Rockin' Robin",
        "artist": "Michael Jackson",
        "artists": ["Michael Jackson"],
        "duration": 152,
    }
    items = [
        {"title": "Rockin' Robin (Live at the Forum, 1972)", "artist": "Jackson 5", "duration": 152, "preview": "a"},
        {"title": "Rockin' Robin", "artist": "Michael Jackson", "duration": 170, "preview": "b"},
        {"title": "Rockin' Robin", "artist": "Michael Jackson", "duration": 151, "preview": "c"},
    ]
    assert pick_preview(song, items, settings.defaults())["preview"] == "c"


def test_pick_preview_returns_none_without_a_close_match():
    song = {"id": "1", "name": "Faith", "artist": "Stevie Wonder", "artists": ["Stevie Wonder"], "duration": 162}
    items = [{"title": "Faith (Piano Version)", "artist": "Piano Gaga", "duration": 162, "preview": "x"}]
    assert pick_preview(song, items, settings.defaults()) is None


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://www.youtube.com/watch?v=zn-KzelImiI", "zn-KzelImiI"),
        ("https://music.youtube.com/watch?v=zn-KzelImiI&list=x", "zn-KzelImiI"),
        ("https://youtu.be/zn-KzelImiI", "zn-KzelImiI"),
        ("https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A1224670246", "sc:1224670246"),
        ("https://example.com/track", None),
        (None, None),
    ],
)
def test_source_key(url, key):
    assert source_key(url) == key


def fake_search(results):
    return lambda query: [dict(r) for r in results]


def test_find_candidates_merges_sources_and_ranks_official_audio_first(monkeypatch):
    song = {"name": "Yo Vengo De Cuba", "artist": "FredyClan", "album": "Yo Vengo De Cuba", "duration": 271}
    official = {
        "id": "a",
        "url": "u",
        "title": "Yo Vengo de Cuba",
        "channel": "FredyClan",
        "album": "Yo Vengo De Cuba",
        "duration": 271,
        "source": "YouTube Music",
        "official": True,
    }
    upload = {
        "id": "b",
        "url": "u",
        "title": "Yo Vengo De Cuba",
        "channel": "random",
        "duration": 272,
        "source": "YouTube",
        "official": False,
    }
    soundcloud = {
        "id": "sc:1",
        "url": "u",
        "title": "Yo Vengo De Cuba",
        "channel": "x",
        "duration": 271,
        "source": "SoundCloud",
        "official": False,
    }
    too_long = {
        "id": "c",
        "url": "u",
        "title": "Yo Vengo De Cuba",
        "channel": "x",
        "duration": 300,
        "source": "YouTube",
        "official": False,
    }
    monkeypatch.setitem(sources.SEARCHERS, "youtube-music", (fake_search([official]), 2))
    monkeypatch.setitem(sources.SEARCHERS, "youtube", (fake_search([official, upload, too_long]), None))
    monkeypatch.setitem(sources.SEARCHERS, "soundcloud", (fake_search([soundcloud]), 2))
    found = sources.find_candidates(song, exclude=set(), st=settings.defaults())
    assert [c["id"] for c in found] == ["a", "b", "sc:1"]
    assert found[0]["diff"] == 0


def test_find_candidates_respects_exclusions_and_enabled_sources(monkeypatch):
    song = {"name": "Song", "artist": "Artist", "duration": 200}
    item = {
        "id": "a",
        "url": "u",
        "title": "Song",
        "channel": "Artist",
        "duration": 200,
        "source": "YouTube",
        "official": False,
    }
    monkeypatch.setitem(sources.SEARCHERS, "youtube", (fake_search([item]), None))
    monkeypatch.setitem(sources.SEARCHERS, "soundcloud", (fake_search([dict(item, id="sc:9")]), 2))
    st = settings.merge(settings.defaults(), {"candidate_sources": ["youtube", "soundcloud"]})
    assert [c["id"] for c in sources.find_candidates(song, exclude={"a"}, st=st)] == ["sc:9"]
    st = settings.merge(settings.defaults(), {"candidate_sources": ["youtube"]})
    assert sources.find_candidates(song, exclude={"a"}, st=st) == []

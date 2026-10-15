import pytest

from spotcheck import origins, sources


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd", ("spotify", "37i9dQZF1DX0XUsuxWHRQd")),
        ("https://open.spotify.com/intl-de/album/4aawyAB9vmqN3uQ7FjRGTy", ("spotify", "4aawyAB9vmqN3uQ7FjRGTy")),
        ("https://www.youtube.com/playlist?list=PL8nmKh79kiCx8", ("youtube", "yt-PL8nmKh79kiCx8")),
        ("https://music.youtube.com/playlist?list=OLAK5uy_mYGp", ("youtube", "yt-OLAK5uy_mYGp")),
        ("https://www.youtube.com/watch?v=fOoFshzL0k0&list=PL8nmKh79kiCx8&index=2", ("youtube", "yt-PL8nmKh79kiCx8")),
        ("https://soundcloud.com/the-band/sets/the-ep", ("soundcloud", "sc-the-band-the-ep")),
        ("https://artist.bandcamp.com/album/first-light", ("bandcamp", "bc-artist-first-light")),
    ],
)
def test_supported_links(url, expected):
    assert origins.classify(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=fOoFshzL0k0",
        "https://www.youtube.com/playlist?list=LL",
        "https://www.youtube.com/watch?v=x&list=RDfOoFshzL0k0",
        "https://soundcloud.com/the-band/a-track",
        "http://open.spotify.com/playlist/abc",
        "spotify:playlist:abc",
    ],
)
def test_unsupported_links(url):
    assert origins.classify(url) is None


def test_a_video_inside_a_playlist_becomes_the_playlist_link():
    assert (
        origins.canonical_url("https://www.youtube.com/watch?v=fOoFshzL0k0&list=PL8nmK&index=2")
        == "https://www.youtube.com/playlist?list=PL8nmK"
    )
    assert origins.canonical_url("https://music.youtube.com/watch?v=a&list=OLAK5") == (
        "https://music.youtube.com/playlist?list=OLAK5"
    )


@pytest.mark.parametrize(
    ("title", "channel", "expected"),
    [
        ("Propuesta Indecente", "Romeo Santos - Topic", ("Romeo Santos", "Propuesta Indecente")),
        (
            "Romeo Santos \N{EN DASH} Propuesta Indecente (Official Video)",
            "RomeoSantosVEVO",
            ("Romeo Santos", "Propuesta Indecente"),
        ),
        ("Hozier - Take Me To Church [Lyrics]", "Lyrics Hub", ("Hozier", "Take Me To Church")),
        ("Medicina De Amor", "Raulin Rodriguez", ("Raulin Rodriguez", "Medicina De Amor")),
        ("Dos Locos (Audio)", "Monchy & Alexandra Official", ("Monchy & Alexandra", "Dos Locos")),
    ],
)
def test_artist_and_title_from_a_video(title, channel, expected):
    assert origins.split_title(title, channel) == expected


def test_identify_prefers_the_matching_release(monkeypatch):
    results = {
        "data": [
            {"title": "Medicina De Amor (Live)", "artist": {"name": "Somebody Else"}, "duration": 360, "album": {}},
            {
                "title": "Medicina De Amor",
                "artist": {"name": "Raulín Rodríguez"},
                "duration": 358,
                "link": "https://www.deezer.com/track/1",
                "album": {"title": "Grandes Exitos", "cover_xl": "https://cover"},
            },
        ]
    }
    monkeypatch.setattr(sources, "http_get", lambda url, **kw: results)
    found = origins.identify("Raulin Rodriguez", "Medicina De Amor", 362)
    assert found["artist"] == "Raulín Rodríguez"
    assert found["duration"] == 358
    assert found["album"] == "Grandes Exitos"
    assert origins.identify("Raulin Rodriguez", "Medicina De Amor", 200) is None


def test_entries_become_songs_with_official_metadata():
    entry = {"id": "fOoFshzL0k0", "title": "Hombre No Muere de Pena", "channel": "Teodoro Reyes", "duration": 235}
    official = {
        "title": "Hombre No Muere De Pena",
        "artist": "Teodoro Reyes",
        "album": "Lo Mejor",
        "duration": 234.0,
        "cover_url": "https://cover",
        "link": "https://www.deezer.com/track/2",
    }
    song = origins.entry_song(entry, "youtube", "bachata hits.", 1, official)
    assert song["song_id"] == "yt-fOoFshzL0k0"
    assert song["url"] == "https://www.youtube.com/watch?v=fOoFshzL0k0"
    assert song["duration"] == 234.0
    assert song["source_duration"] == 235.0
    assert song["album_name"] == "Lo Mejor"
    assert song["origin"] == "youtube"

    plain = origins.entry_song(entry, "youtube", "bachata hits.", 1, None)
    assert plain["name"] == "Hombre No Muere de Pena"
    assert plain["duration"] == 235.0
    assert plain["cover_url"] == "https://i.ytimg.com/vi/fOoFshzL0k0/hqdefault.jpg"


def test_soundcloud_previews_are_recognised():
    assert origins.is_snippet({"url": "https://cf-preview-media.sndcdn.com/preview/0/30/x.mp3", "duration": 30.0})
    assert not origins.is_snippet({"url": "https://playback.media-streaming.soundcloud.cloud/x", "duration": 205.0})

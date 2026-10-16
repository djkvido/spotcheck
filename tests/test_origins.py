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
    ],
)
def test_links_that_are_not_playlists_are_not_taken_for_playlists(url):
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


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("open.spotify.com/track/4yOn1TEcfsKHUJCL2h1r8I", "https://open.spotify.com/track/4yOn1TEcfsKHUJCL2h1r8I"),
        (
            "http://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        ),
        ("spotify:track:4yOn1TEcfsKHUJCL2h1r8I", "https://open.spotify.com/track/4yOn1TEcfsKHUJCL2h1r8I"),
        ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"),
        ("<https://youtu.be/Y79K-XyMOA0>", "https://youtu.be/Y79K-XyMOA0"),
        ("www.youtube.com/watch?v=Y79K-XyMOA0", "https://www.youtube.com/watch?v=Y79K-XyMOA0"),
        ("https://www.youtube.com/shorts/Y79K-XyMOA0", "https://www.youtube.com/watch?v=Y79K-XyMOA0"),
        ("artist.bandcamp.com/album/first-light", "https://artist.bandcamp.com/album/first-light"),
    ],
)
def test_pasted_links_are_completed_before_they_are_recognised(pasted, expected):
    assert origins.normalize(pasted) == expected
    assert origins.classify(pasted) or origins.classify_track(pasted)


def test_a_link_without_the_scheme_is_accepted_and_stored_in_its_full_form():
    from spotcheck.server import split_urls

    urls, rejected = split_urls("open.spotify.com/track/abc123 spotify:album:xyz789 example.com/page")
    assert urls == ["https://open.spotify.com/track/abc123", "https://open.spotify.com/album/xyz789"]
    assert rejected == ["example.com/page"]


@pytest.mark.parametrize(
    ("title", "clean"),
    [
        ("PIZZA DANCE (Oficiálny videoklip)", "PIZZA DANCE"),
        ("Song name [Oficiální video]", "Song name"),
        ("Song name (Official Video)", "Song name"),
        ("Song name (Live at Wembley)", "Song name (Live at Wembley)"),
    ],
)
def test_czech_and_slovak_video_suffixes_are_removed_from_titles(title, clean):
    assert origins.split_title(title, "Channel")[1] == clean

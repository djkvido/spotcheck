import pytest

from spotcheck import config


@pytest.fixture()
def workspace(tmp_path):
    return config.configure(tmp_path)


def make_song(song_id="t1", name="Song", artist="Artist", duration=200, **extra):
    song = {
        "song_id": song_id,
        "url": f"https://open.spotify.com/track/{song_id}",
        "name": name,
        "artist": artist,
        "artists": [artist],
        "album_name": "Album",
        "duration": duration,
        "list_name": "Playlist",
    }
    song.update(extra)
    return song

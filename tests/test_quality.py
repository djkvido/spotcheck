import pytest

from spotcheck import analysis, tags

needs_ffmpeg = pytest.mark.skipif(not analysis.ffmpeg_path(), reason="FFmpeg is not installed")

YOUTUBE_AAC = [-28, -29, -31, -32, -34, -36, -37, -39, -42, -47, -75, -82, -84, -85, -85, -86, -87, -87, -88, -90, -91]
YOUTUBE_OPUS = [-27, -29, -30, -31, -32, -33, -34, -35, -36, -37, -38, -39, -40, -41, -43, -46, -49, -56, -75, -86, -88]
MP3_128_SOFT = [-45, -46, -48, -50, -52, -54, -57, -61, -64, -70, -78, -83, -85, -85, -86, -86, -87, -88, -89, -90, -92]
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_cutoff_is_found_where_the_spectrum_falls_off():
    assert analysis.find_cutoff(YOUTUBE_AAC, -12) == 15.75
    assert analysis.find_cutoff(YOUTUBE_OPUS, -12) == 19.75
    assert 15.5 <= analysis.find_cutoff(MP3_128_SOFT, -14) <= 16.5


def test_full_range_natural_roll_off_and_nothing_above_the_bands():
    assert analysis.find_cutoff([-20 - 0.3 * i for i in range(21)], -12) is None
    gentle = [-50 - 2.5 * i for i in range(21)]
    assert analysis.find_cutoff(gentle, -12) is None
    assert analysis.find_cutoff([-90.0] * 21, -12) == 11.0


def render(path, source, seconds, *codec):
    result = analysis.run_text(
        [analysis.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", source, "-t", str(seconds), *codec, str(path)]
    )
    assert result.returncode == 0, result.stderr
    return path


@needs_ffmpeg
def test_cutoff_of_rendered_audio(tmp_path):
    noise = "anoisesrc=color=pink:sample_rate=44100:amplitude=0.4"
    full = render(tmp_path / "full.wav", noise, 30)
    assert analysis.cutoff_khz(full, 30) == (True, None)
    cut = render(tmp_path / "cut.wav", noise + ",firequalizer=gain='if(gt(f,16000),-120,0)':delay=0.1", 30)
    measured, cutoff = analysis.cutoff_khz(cut, 30)
    assert measured
    assert cutoff == pytest.approx(16.0, abs=0.5)


FORMATS = {
    "mp3": ("-c:a", "libmp3lame", "-b:a", "128k"),
    "m4a": ("-c:a", "aac", "-b:a", "128k"),
    "opus": ("-c:a", "libopus", "-b:a", "96k"),
}


@needs_ffmpeg
@pytest.mark.parametrize("ext", FORMATS)
def test_tags_are_written_read_and_kept(tmp_path, ext):
    path = render(tmp_path / f"song.{ext}", "sine=frequency=440:sample_rate=48000", 5, *FORMATS[ext])
    assert tags.missing(path) == ["title", "artist", "album", "cover"]

    written = tags.write(path, {"title": "Song", "artist": "Artist", "album": "Album", "comment": "https://x"}, JPEG)
    assert set(written) == {"title", "artist", "album", "comment", "cover"}
    assert tags.missing(path) == []
    assert tags.cover(path) == JPEG

    assert tags.write(path, {"title": "Other"}) == []
    assert tags.write(path, {"title": "Other"}, overwrite=True) == ["title"]

    copy = render(tmp_path / f"copy.{ext}", "sine=frequency=440:sample_rate=48000", 5, *FORMATS[ext])
    tags.copy(path, copy)
    assert tags.missing(copy) == []
    assert tags.cover(copy) == JPEG


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["m4a", "opus"])
def test_trim_keeps_tags_of_mp4_and_ogg(tmp_path, ext):
    source = "sine=frequency=440:sample_rate=48000,volume=enable='lt(t,3)':volume=0"
    path = render(tmp_path / f"song.{ext}", source, 15, *FORMATS[ext])
    tags.write(path, {"title": "Song", "artist": "Artist", "album": "Album"}, JPEG)
    assert analysis.trim(path, 2.5, 12.0)
    assert analysis.duration(path) == pytest.approx(12.0, abs=0.2)
    assert tags.missing(path) == []
    assert tags.cover(path) == JPEG


@needs_ffmpeg
def test_codec_and_bitrate_of_opus(tmp_path):
    path = render(tmp_path / "song.opus", "anoisesrc=sample_rate=48000:amplitude=0.3", 10, *FORMATS["opus"])
    assert analysis.codec_name(path) == "Opus"
    assert 60 <= analysis.bitrate_kbps(path) <= 130


def test_unreadable_files_have_no_tags(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"not audio")
    assert tags.missing(path) is None
    assert tags.cover(path) == b""
    with pytest.raises(ValueError):
        tags.write(path, {"title": "x"})

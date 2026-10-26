import hashlib
import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spotcheck import analysis, codecs, convert, fileops, terms
from spotcheck.converter import Converter
from spotcheck.errors import JobError
from spotcheck.server import apple_script_files, create_app

HEADERS = {"X-SpotCheck": "1"}
I18N = Path(__file__).resolve().parents[1] / "spotcheck" / "static" / "i18n.js"

ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
 A..X.D opus                 Opus
 S..... srt                  SubRip subtitle (codec subrip)
"""
MUXERS = """Formats:
 D.. = Demuxing supported
 .E. = Muxing supported
 ---
  E  matroska        Matroska
 DE  mp4             MP4 (MPEG-4 Part 14)
  E  null            raw null video
"""
MUXER_HELP = """Muxer matroska [Matroska]:
    Common extensions: mkv.
    Mime type: video/x-matroska.
    Default video codec: h264.
    Default audio codec: vorbis.
"""


class Bus:
    def __init__(self):
        self.events = []
        self.logs = []

    def emit(self, event):
        self.events.append(event)

    def log(self, level, code, track=None, **params):
        self.logs.append((level, code, params))


def caps(
    encoders=("libx264", "aac", "libmp3lame", "flac", "libopus"), muxers=("mp4", "mp3", "flac", "opus", "matroska")
):
    return {
        "ffmpeg": "ffmpeg",
        "encoders": {name: {"kind": "audio", "codec": name, "experimental": False} for name in encoders},
        "muxers": dict.fromkeys(muxers, ""),
        "hardware": {"h264": None, "hevc": None, "av1": None},
    }


def info(video=True, audio=True, cover=None, codec="aac", duration=10.0):
    return {
        "duration": duration,
        "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30.0} if video else None,
        "audio": {"codec": codec, "sample_rate": 44100, "channels": 2} if audio else None,
        "cover": cover,
    }


def test_encoder_and_muxer_lists_are_parsed():
    found = convert.parse_encoders(ENCODERS)
    assert found["libx264"]["kind"] == "video" and found["libx264"]["codec"] == "h264"
    assert found["opus"]["experimental"] is True
    assert found["srt"]["kind"] == "subtitle"
    assert "=" not in found
    assert convert.parse_muxers(MUXERS) == {
        "matroska": "Matroska",
        "mp4": "MP4 (MPEG-4 Part 14)",
        "null": "raw null video",
    }
    assert convert.parse_muxer_help(MUXER_HELP) == {"ext": "mkv", "video": "h264", "audio": "vorbis"}


def test_muxer_lists_of_older_ffmpeg_are_parsed():
    older = """File formats:
 D. = Demuxing supported
 .E = Muxing supported
 --
  E 3g2             3GP2
 DE flac            raw FLAC
"""
    assert convert.parse_muxers(older) == {"3g2": "3GP2", "flac": "raw FLAC"}


def test_probe_output_separates_cover_art_from_video():
    data = {
        "format": {"duration": "200.5", "format_name": "mp3", "tags": {"TITLE": "Song"}},
        "streams": [
            {"index": 0, "codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2},
            {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ],
    }
    parsed = convert.parse_probe(data)
    assert parsed["video"] is None
    assert parsed["cover"] == {"index": 1, "codec": "mjpeg"}
    assert parsed["audio"]["codec"] == "mp3" and parsed["duration"] == 200.5
    assert parsed["tags"]["title"] == "Song"


def test_presets_are_offered_only_when_ffmpeg_has_their_encoders():
    available = {p["id"]: p["available"] for p in convert.public_presets(caps())}
    assert available["mp3"] and available["mp4_h264"] and available["flac"]
    assert not available["mp4_h265"] and not available["webm_vp9"]
    assert available["audio_copy"]


def test_every_preset_and_quality_has_a_label_in_both_languages():
    text = I18N.read_text(encoding="utf-8")
    en, cs = text.split("\n  cs: {", 1)
    keys = {f"convert.preset.{p.id}" for p in convert.PRESETS}
    keys |= {f"convert.q.{q.id}" for p in convert.PRESETS for q in p.qualities}
    keys |= {f"convert.name.{p.id}" for p in convert.PRESETS if not p.name}
    keys |= {f"convert.warn.{w}" for w in ("lossy_to_lossless", "no_upscale")}
    for language in (en, cs):
        assert sorted(k for k in keys if f'"{k}":' not in language) == []


def test_audio_command_keeps_tags_and_cover_art(tmp_path):
    target = convert.Target(preset="mp3", quality="high")
    cmd, hw = convert.build_command(
        "ffmpeg",
        tmp_path / "a.flac",
        tmp_path / "a.mp3",
        target,
        info(False, cover={"index": 1, "codec": "png"}, codec="flac"),
    )
    joined = " ".join(cmd)
    assert "-map_metadata 0" in joined and "-map 0:a:0" in joined and "-map 0:1" in joined
    assert "-c:v copy" in joined and "-disposition:v:0 attached_pic" in joined
    assert "-b:a 320k" in joined and cmd[-3:-1] == ["-f", "mp3"]
    assert hw is None


def test_audio_from_video_never_maps_the_picture(tmp_path):
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "v.mp4", tmp_path / "v.m4a", convert.Target(preset="m4a"), info()
    )
    assert "-vn" in cmd and "0:V:0" not in cmd


def test_video_command_uses_the_graphics_card_when_allowed(tmp_path):
    hw = {"h264": "h264_nvenc", "hevc": None, "av1": None}
    target = convert.Target(preset="mp4_h264", quality="small", resolution=720, fps=30)
    cmd, used = convert.build_command("ffmpeg", tmp_path / "v.mkv", tmp_path / "v.mp4", target, info(), hw)
    joined = " ".join(cmd)
    assert used == "h264_nvenc" and "-c:v h264_nvenc" in joined and "-cq 28" in joined
    assert "min(720,ih)" in joined and "fps=30" in joined and "format=yuv420p" in joined
    assert "-c:a aac" in joined and "+faststart" in joined
    cpu, used = convert.build_command(
        "ffmpeg", tmp_path / "v.mkv", tmp_path / "v.mp4", convert.Target(preset="mp4_h264", hardware=False), info(), hw
    )
    assert used is None and "libx264" in cpu


def test_trim_mute_and_copy_options(tmp_path):
    target = convert.Target(preset="mp4_h265", start=5.0, end=12.5, mute=True)
    cmd, _ = convert.build_command("ffmpeg", tmp_path / "v.mkv", tmp_path / "v.mp4", target, info())
    assert cmd[cmd.index("-ss") + 1] == "5.000" and cmd[cmd.index("-to") + 1] == "12.500"
    assert cmd.index("-ss") < cmd.index("-i") and "-an" in cmd and "0:a?" not in cmd
    remux, _ = convert.build_command(
        "ffmpeg", tmp_path / "v.mp4", tmp_path / "v.mkv", convert.Target(preset="mkv_copy"), info()
    )
    assert "-c copy" in " ".join(remux) and "0:s?" in remux and "-vf" not in remux


def test_gif_uses_a_palette(tmp_path):
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "v.mp4", tmp_path / "v.gif", convert.Target(preset="gif", quality="small"), info()
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "palettegen" in graph and "paletteuse" in graph and "fps=10" in graph and "320" in graph


def test_custom_target_uses_sensible_quality_settings(tmp_path):
    target = convert.Target(container="matroska", video="libx265", audio="flac", quality="high")
    cmd, _ = convert.build_command("ffmpeg", tmp_path / "v.mp4", tmp_path / "v.mkv", target, info())
    joined = " ".join(cmd)
    assert "-c:v libx265 -crf 20" in joined and "-c:a flac" in joined and "-b:a" not in joined
    assert cmd[-3:-1] == ["-f", "matroska"]
    audio_only = convert.Target(container="mp3", video="none", audio="libmp3lame", quality="small")
    cmd, _ = convert.build_command("ffmpeg", tmp_path / "v.mp4", tmp_path / "v.mp3", audio_only, info())
    assert "-vn" in cmd and "128k" in cmd


def test_targets_are_validated_against_the_ffmpeg_in_use():
    c = caps()
    convert.validate_target(convert.Target(preset="mp3", quality="balanced"), c)
    for target, code in [
        (convert.Target(preset="nope"), "unknown_preset"),
        (convert.Target(preset="mp4_h265"), "preset_unavailable"),
        (convert.Target(preset="mp3", quality="ultra"), "unknown_quality"),
        (convert.Target(container="matroska", video="-y", audio="aac"), "encoder_unavailable"),
        (convert.Target(container="avi", video="libx264"), "unknown_container"),
        (convert.Target(container="matroska", video="none", audio="none"), "nothing_to_convert"),
        (convert.Target(preset="mp3", resolution=123), "invalid_option"),
        (convert.Target(preset="mp3", start=20, end=10), "invalid_trim"),
        (convert.Target(preset="mp3", start=-1), "invalid_trim"),
    ]:
        with pytest.raises(JobError) as exc:
            convert.validate_target(target, c)
        assert exc.value.code == code


def test_sources_must_fit_the_target():
    with pytest.raises(JobError, match="no_video"):
        convert.check_source(info(video=False), convert.Target(preset="mp4_h264"))
    with pytest.raises(JobError, match="no_audio"):
        convert.check_source(info(audio=False), convert.Target(preset="mp3"))
    assert convert.output_format(convert.Target(preset="audio_copy"), info(codec="aac")) == ("m4a", "mp4")
    assert convert.output_format(convert.Target(preset="audio_copy"), info(codec="dts")) == ("mka", "matroska")


def test_lossy_to_lossless_conversion_is_flagged():
    assert convert.warnings(info(codec="mp3"), convert.Target(preset="flac")) == ["lossy_to_lossless"]
    assert convert.warnings(info(codec="flac"), convert.Target(preset="flac")) == []
    assert "no_upscale" in convert.warnings(info(), convert.Target(preset="mp4_h264", resolution=2160))


def test_progress_and_file_names():
    assert convert.progress_seconds({"out_time_us": "2500000"}) == 2.5
    assert convert.progress_seconds({"out_time_us": "N/A"}) is None
    assert convert.safe_filename('..\\evil/name<>:".wav') == "name____.wav"
    assert convert.safe_filename("   ") == "file"


def test_output_folders_are_checked(tmp_path):
    assert convert.output_folder(str(tmp_path / "new")) == tmp_path / "new"
    for raw, code in [("", "folder_required"), ("relative/path", "folder_not_absolute")]:
        with pytest.raises(JobError, match=code):
            convert.output_folder(raw)
    file = tmp_path / "file.txt"
    file.write_text("x")
    with pytest.raises(JobError, match="folder_not_directory"):
        convert.output_folder(str(file))


def test_reserved_names_never_overwrite(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"old")
    first = fileops.reserve(tmp_path / "song.mp3")
    second = fileops.reserve(tmp_path / "song.mp3")
    assert (first.name, second.name) == ("song (2).mp3", "song (3).mp3")
    fileops.release(first)
    assert not first.exists() and (tmp_path / "song.mp3").read_bytes() == b"old"


def test_apple_script_picks_several_files():
    cmd = apple_script_files('/Users/me/My "Music"')
    assert cmd[0] == "osascript" and "multiple selections allowed" in cmd[2]
    assert '\\"Music\\"' in cmd[2]


def fake_build(tmp_path, payload: bytes, name="ffmpeg-1.0-test/bin/ffmpeg.exe"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, payload)
        bundle.writestr(name.replace("ffmpeg.exe", "ffprobe.exe").replace("/ffmpeg", "/ffprobe"), payload)
        bundle.writestr("ffmpeg-1.0-test/LICENSE", "GPL")
        bundle.writestr("ffmpeg-1.0-test/doc/manual.html", "skip")
        bundle.writestr("ffmpeg-1.0-test/bin/ffplay.exe", "skip")
    data = buffer.getvalue()
    spec = {
        "version": "1.0",
        "provider": "test",
        "home": "https://example.com/",
        "license": "GPL-3.0-or-later",
        "archives": [
            {"url": "https://example.com/build.zip", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        ],
        "skip": ("ffplay.exe",),
    }
    return spec, data


class Response(io.BytesIO):
    def __init__(self, data, url="https://example.com/build.zip"):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def install_fake(monkeypatch, spec, data, url="https://example.com/build.zip"):
    monkeypatch.setattr(codecs, "build", lambda: spec)
    monkeypatch.setattr(codecs.urllib.request, "urlopen", lambda req, timeout: Response(data, url))
    monkeypatch.setattr(codecs, "EXE", ".exe")
    monkeypatch.setattr(codecs, "_verify", lambda folder, version: None)


def test_extended_codecs_are_verified_and_installed(workspace, monkeypatch):
    spec, data = fake_build(workspace.home, b"binary")
    install_fake(monkeypatch, spec, data)
    seen = []
    status = codecs.install(lambda done, total: seen.append((done, total)), lambda: False)
    folder = Path(status["folder"])
    assert status["installed"] and seen[-1] == (len(data), len(data))
    assert sorted(p.name for p in (folder / "bin").iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert (folder / "docs" / "LICENSE").exists() and (folder / "docs" / "SOURCE.txt").exists()
    assert codecs.ffmpeg_path() == str(folder / "bin" / "ffmpeg.exe")
    assert [p.name for p in codecs.root().iterdir()] == ["ffmpeg-1.0"]
    assert codecs.remove() is True and codecs.installed() is None


def test_a_tampered_download_is_rejected_and_cleaned_up(workspace, monkeypatch):
    spec, data = fake_build(workspace.home, b"binary")
    install_fake(monkeypatch, spec, data[:-1] + b"X")
    with pytest.raises(JobError, match="codecs_checksum"):
        codecs.install(lambda done, total: None, lambda: False)
    assert codecs.installed() is None and list(codecs.root().iterdir()) == []


def test_a_redirect_to_plain_http_is_refused(workspace, monkeypatch):
    spec, data = fake_build(workspace.home, b"binary")
    install_fake(monkeypatch, spec, data, url="http://example.com/build.zip")
    with pytest.raises(JobError, match="codecs_insecure"):
        codecs.install(lambda done, total: None, lambda: False)
    assert list(codecs.root().iterdir()) == []


def test_extended_codecs_are_offered_for_windows_and_apple_silicon():
    assert codecs.BUILDS["win32"]["archives"][0]["url"].startswith("https://github.com/GyanD/codexffmpeg/")
    assert all(re.fullmatch(r"[0-9a-f]{64}", a["sha256"]) for b in codecs.BUILDS.values() for a in b["archives"])


def test_queued_conversions_can_be_cancelled(workspace, monkeypatch, tmp_path):
    monkeypatch.setattr("spotcheck.converter.capabilities", lambda refresh=False: caps())
    bus = Bus()
    converter = Converter(bus)
    converter.workers = 0
    src = tmp_path / "a.flac"
    src.write_bytes(b"x")
    ids = converter.add([{"path": str(src), "info": info(False, codec="flac")}], {"preset": "mp3"}, tmp_path / "out")
    assert converter.busy()
    assert converter.cancel(ids[0]) is True
    assert converter.public()[0]["state"] == "cancelled" and not converter.busy()
    assert converter.clear() == 1 and converter.public() == []
    assert bus.events[0]["type"] == "convert"


needs_ffmpeg = pytest.mark.skipif(not analysis.ffmpeg_path(), reason="FFmpeg is not installed")


def make_tone(path: Path, seconds=3):
    cmd = [
        analysis.ffmpeg(),
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=f=440:d={seconds}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    assert analysis.run_text(cmd).returncode == 0


def wait_idle(converter, timeout=120):
    deadline = time.time() + timeout
    while converter.busy() and time.time() < deadline:
        time.sleep(0.1)
    assert not converter.busy()


@needs_ffmpeg
def test_real_conversion_writes_a_new_file_and_never_touches_the_source(workspace, tmp_path):
    src = tmp_path / "tone.wav"
    make_tone(src)
    (tmp_path / "tone.flac").write_bytes(b"existing")
    before = src.read_bytes()
    converter = Converter(Bus())
    entry = convert.describe(src)
    assert entry["info"]["audio"]["codec"] == "pcm_s16le" and 2.9 < entry["info"]["duration"] < 3.1
    converter.add([entry], {"preset": "flac"}, None)
    wait_idle(converter)
    item = converter.public()[0]
    assert item["state"] == "done", item
    out = Path(item["output"])
    assert out.name == "tone (2).flac" and (tmp_path / "tone.flac").read_bytes() == b"existing"
    assert convert.probe(out)["audio"]["codec"] == "flac"
    assert src.read_bytes() == before
    assert not [p for p in tmp_path.iterdir() if fileops.is_temp(p)]


@needs_ffmpeg
def test_a_failed_conversion_leaves_nothing_behind(workspace, tmp_path, monkeypatch):
    src = tmp_path / "tone.wav"
    make_tone(src, 1)
    monkeypatch.setattr(convert, "_custom_audio", lambda encoder, quality: ["-c:a", "definitely_not_a_codec"])
    converter = Converter(Bus())
    converter.add(
        [convert.describe(src)], {"container": "matroska", "video": "none", "audio": "flac"}, tmp_path / "out"
    )
    wait_idle(converter)
    item = converter.public()[0]
    assert item["state"] == "failed" and item["error"] == "convert_failed" and item["detail"]
    assert item["output"] is None and list((tmp_path / "out").iterdir()) == []


@needs_ffmpeg
def test_a_running_conversion_stops_when_cancelled(workspace, tmp_path, monkeypatch):
    src = tmp_path / "tone.wav"
    make_tone(src, 1)
    started = threading.Event()
    real = analysis.run_streaming

    def slow(cmd, on_line, timeout=None, cwd=None, on_start=None):
        slow_cmd = [
            cmd[0],
            "-hide_banner",
            "-nostdin",
            "-y",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "sine=d=60",
            "-f",
            "wav",
            cmd[-1],
        ]

        def hooked(proc):
            on_start(proc)
            started.set()

        return real(slow_cmd, on_line, timeout, cwd, hooked)

    monkeypatch.setattr(analysis, "run_streaming", slow)
    converter = Converter(Bus())
    ids = converter.add([convert.describe(src)], {"preset": "wav"}, tmp_path / "out")
    assert started.wait(30)
    converter.cancel(ids[0])
    wait_idle(converter, 30)
    assert converter.public()[0]["state"] == "cancelled"
    assert list((tmp_path / "out").iterdir()) == []


@pytest.fixture()
def client(workspace):
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as c:
        yield c


def test_converter_state_lists_presets_and_codecs(client):
    data = client.get("/api/convert").json()
    assert {p["id"] for p in data["presets"]} >= {"mp3", "flac", "mp4_h264", "gif"}
    assert set(data["codecs"]) >= {"available", "installed", "version", "job"}
    assert data["items"] == [] and data["default_folder"]


def test_missing_files_and_bad_targets_are_refused(client, tmp_path):
    response = client.post(
        "/api/convert", json={"files": [str(tmp_path / "nope.mp3")], "target": {"preset": "mp3"}}, headers=HEADERS
    )
    assert response.status_code == 409 and response.json()["detail"] == "file_not_found"
    assert client.post("/api/convert", json={"files": [], "target": {}}, headers=HEADERS).status_code == 422
    assert client.post("/api/convert", json={"files": ["x"], "target": {}}).status_code == 403


def test_an_uploaded_file_that_is_not_media_is_removed(client):
    response = client.post("/api/convert/upload?name=..%2Fjunk.mp3", content=b"not audio", headers=HEADERS)
    assert response.json()["error"] in ("not_media", "ffprobe_missing")
    assert not any(convert.inbox().iterdir())


@needs_ffmpeg
def test_dropped_files_are_converted_into_the_chosen_folder(client, tmp_path):
    src = tmp_path / "drop.wav"
    make_tone(src, 1)
    entry = client.post("/api/convert/upload?name=drop.wav", content=src.read_bytes(), headers=HEADERS).json()
    assert entry["error"] is None and Path(entry["path"]).parent.parent == convert.inbox()
    out = tmp_path / "converted"
    response = client.post(
        "/api/convert",
        json={"files": [entry["path"]], "target": {"preset": "wav", "quality": "24bit"}, "folder": str(out)},
        headers=HEADERS,
    )
    assert response.status_code == 200
    deadline = time.time() + 60
    while time.time() < deadline:
        items = client.get("/api/convert").json()["items"]
        if items and items[0]["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert items[0]["state"] == "done", items[0]
    assert (out / "drop.wav").exists()
    assert client.post("/api/convert/clear", headers=HEADERS).json() == {"removed": 1}
    assert not Path(entry["path"]).exists()


def test_codecs_cannot_be_removed_while_converting(client, monkeypatch):
    monkeypatch.setattr(Converter, "busy", lambda self: True)
    assert client.delete("/api/codecs", headers=HEADERS).status_code == 409


def test_state_file_of_the_build_is_valid_json(workspace):
    folder = codecs.root() / "ffmpeg-x"
    folder.mkdir(parents=True)
    (folder / "build.json").write_text("{broken", encoding="utf-8")
    assert codecs._marker(folder) is None
    (folder / "build.json").write_text(json.dumps({"version": "x"}), encoding="utf-8")
    assert codecs._marker(folder) == {"version": "x"}


def test_loudness_normalisation_keeps_the_sample_rate(tmp_path):
    source = info(False, codec="flac")
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "a.flac", tmp_path / "b.flac", convert.Target(preset="flac", normalize=True), source
    )
    assert cmd[cmd.index("-ar") + 1] == "44100" and "loudnorm" in cmd[cmd.index("-af") + 1]


def test_sample_rates_follow_what_the_encoder_accepts(tmp_path):
    hi_res = info(False, codec="flac")
    hi_res["audio"]["sample_rate"] = 96000
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "a.flac", tmp_path / "a.wma", convert.Target(preset="wma"), hi_res
    )
    assert cmd[cmd.index("-ar") + 1] == "48000"
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "a.flac", tmp_path / "a.flac", convert.Target(preset="flac"), hi_res
    )
    assert "-ar" not in cmd
    rates = {p["id"]: p["rates"] for p in convert.public_presets(caps())}
    assert rates["opus"] == [48000] and 96000 not in rates["mp3"] and 192000 in rates["flac"]
    with pytest.raises(JobError, match="invalid_option"):
        convert.validate_target(convert.Target(preset="opus", sample_rate=44100), caps())


def test_rewrapping_into_mkv_converts_phone_subtitles(tmp_path):
    source = {**info(), "subtitles": ["mov_text"]}
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "v.mp4", tmp_path / "v.mkv", convert.Target(preset="mkv_copy"), source
    )
    assert cmd[cmd.index("-c:s") + 1] == "srt"


def test_original_sound_keeps_the_cover_where_the_format_allows_it(tmp_path):
    source = info(False, cover={"index": 1, "codec": "mjpeg"}, codec="mp3")
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "a.mp3", tmp_path / "b.mp3", convert.Target(preset="audio_copy"), source
    )
    assert "-map" in cmd and "0:1" in cmd and "-c:a" in cmd
    cmd, _ = convert.build_command(
        "ffmpeg", tmp_path / "a.ogg", tmp_path / "b.ogg", convert.Target(preset="audio_copy"), source
    )
    assert "-vn" in cmd and "0:1" not in cmd


def test_wrong_value_types_and_hidden_containers_are_refused(tmp_path):
    for raw in ({"preset": []}, {"preset": "mp3", "normalize": "false"}, {"preset": "mp3", "start": "10"}):
        with pytest.raises(JobError, match="invalid_option"):
            convert.Target.from_dict(raw)
    assert convert.Target.from_dict({"preset": "mp3", "hardware": None}).hardware is True
    with pytest.raises(JobError, match="unknown_container"):
        convert.validate_target(convert.Target(container="hls", audio="aac"), caps(muxers=("hls",)))
    with pytest.raises(JobError, match="unknown_container"):
        convert.validate_target(convert.Target(container="mp4", audio="aac"), caps(), containers={"matroska"})
    assert convert.describe(Path("relative.mp3"))["error"] == "path_not_absolute"


@needs_ffmpeg
def test_a_locked_output_fails_cleanly(workspace, tmp_path, monkeypatch):
    src = tmp_path / "tone.wav"
    make_tone(src, 1)

    def locked(src_path, dst):
        raise fileops.FileLockedError(dst)

    monkeypatch.setattr(fileops, "replace", locked)
    converter = Converter(Bus())
    converter.add([convert.describe(src)], {"preset": "flac"}, tmp_path / "out")
    wait_idle(converter)
    item = converter.public()[0]
    assert item["state"] == "failed" and item["error"] == "file_locked" and item["output"] is None
    assert list((tmp_path / "out").iterdir()) == []


@needs_ffmpeg
def test_normalised_wma_from_a_cd_rate_file_converts(workspace, tmp_path):
    caps_now = convert.capabilities()
    if "wmav2" not in caps_now["encoders"]:
        pytest.skip("FFmpeg has no WMA encoder")
    src = tmp_path / "tone.wav"
    make_tone(src, 2)
    converter = Converter(Bus())
    converter.add([convert.describe(src)], {"preset": "wma", "normalize": True}, tmp_path / "out")
    wait_idle(converter)
    item = converter.public()[0]
    assert item["state"] == "done", item
    assert convert.probe(Path(item["output"]))["audio"]["sample_rate"] == 44100


def test_removed_uploads_are_deleted(client):
    entry = client.post("/api/convert/upload?name=a.wav", content=b"x" * 10, headers=HEADERS).json()
    folder = Path(entry["path"]).parent
    if folder.exists():
        assert client.post("/api/convert/discard", json={"paths": [entry["path"]]}, headers=HEADERS).json() == {
            "removed": 1
        }
    assert not folder.exists()
    outside = client.post("/api/convert/discard", json={"paths": [str(Path.home() / "x.wav")]}, headers=HEADERS)
    assert outside.json() == {"removed": 0}

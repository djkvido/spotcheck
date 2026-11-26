import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spotcheck import analysis, convert, downloader, origins, terms, ytdlp
from spotcheck.downloader import Downloader, Options
from spotcheck.errors import JobError
from spotcheck.server import create_app, image_type

HEADERS = {"X-SpotCheck": "1"}
VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


class Bus:
    def __init__(self):
        self.events = []
        self.logs = []

    def emit(self, event):
        self.events.append(event)

    def log(self, level, code, track=None, **params):
        self.logs.append((level, code, params))


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def joined(options, url=VIDEO):
    return " ".join(downloader.build_command(["yt-dlp"], options, url, Path("work"), "ffmpeg"))


def test_options_are_validated():
    assert Options.from_dict({"mode": "video", "container": "mkv", "height": 2160}).height == 2160
    for raw in (
        {"mode": "film"},
        {"container": "avi"},
        {"height": 999},
        {"height": "1080"},
        {"hdr": "yes"},
        {"audio": "aac"},
        {"bitrate": "999"},
        {"subtitles": "burn"},
        {"languages": ["en; rm -rf"]},
        {"languages": "en"},
        [],
    ):
        with pytest.raises(JobError, match="invalid_option"):
            Options.from_dict(raw)


def test_video_prefers_the_chosen_resolution_and_compatible_codecs():
    command = joined(Options(mode="video", container="mp4", height=1080))
    assert "-f bv*+ba/b" in command and "-S res:1080,fps,hdr:sdr,vcodec:h264,acodec:aac" in command
    assert "--merge-output-format mp4" in command and "--remux-video mp4" in command
    assert "--embed-thumbnail" in command and command.endswith(f"-- {VIDEO}")
    assert "-S res,fps,hdr" in joined(Options(mode="video", container="mkv", hdr=True))
    webm = joined(Options(mode="video", container="webm"))
    assert "vcodec:vp9,acodec:opus" in webm and "--embed-thumbnail" not in webm


def test_subtitles_are_embedded_or_kept_as_files():
    embed = joined(Options(mode="video", subtitles="embed", languages=["cs", "en"], auto_subtitles=True))
    assert "--write-subs --sub-langs cs,en --write-auto-subs --embed-subs" in embed
    files = joined(Options(mode="video", subtitles="files"))
    assert "--sub-langs all,-live_chat" in files and "--convert-subs srt" in files and "--embed-subs" not in files
    assert "--write-subs" not in joined(Options(mode="video"))


def test_sound_only_downloads():
    mp3 = joined(Options(mode="audio", audio="mp3", bitrate="192"))
    assert "-f ba/b -x --audio-format mp3 --audio-quality 192K --embed-thumbnail" in mp3
    flac = joined(Options(mode="audio", audio="flac"))
    assert "--audio-quality 0" in flac and "--embed-thumbnail" in flac
    assert "--embed-thumbnail" not in joined(Options(mode="audio", audio="ogg"))
    assert "--audio-format vorbis" in joined(Options(mode="audio", audio="ogg"))
    assert "--embed-thumbnail" not in joined(Options(mode="audio", audio="mp3", thumbnail=False))


def test_progress_and_phases_are_read():
    line = "SPOTCHECK downloading 524288 1048576 NA 2048.5 12"
    assert downloader.parse_progress(line) == {
        "status": "downloading",
        "done": 524288,
        "total": 1048576,
        "speed": 2048.5,
        "eta": 12,
    }
    assert downloader.parse_progress("SPOTCHECK downloading 10 NA 100 NA NA")["total"] == 100
    assert downloader.parse_progress("[download] 10% of 5MiB") is None
    assert downloader.phase_of('[Merger] Merging formats into "x.mp4"') == "merging"
    assert downloader.phase_of("[ExtractAudio] Destination: x.mp3") == "converting"
    assert downloader.phase_of("[download] Destination: x") is None


def test_resolution_uses_the_short_side_of_portrait_video():
    info = {
        "formats": [
            {"width": 1440, "height": 2560, "vcodec": "av01"},
            {"width": 720, "height": 1280, "vcodec": "h264"},
            {"vcodec": "none", "acodec": "aac"},
        ]
    }
    assert downloader._heights(info) == [1440, 720]


def probe_with(monkeypatch, payload, returncode=0, stderr=""):
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    monkeypatch.setattr(
        analysis,
        "run_text",
        lambda *a, **k: completed(returncode, json.dumps(payload) if payload is not None else "", stderr),
    )
    return downloader.probe


def test_a_single_video_is_described(monkeypatch):
    info = {
        "id": "abc",
        "title": "Title",
        "uploader": "Someone",
        "duration": 61,
        "extractor_key": "Youtube",
        "thumbnail": "https://i.ytimg.com/x.jpg",
        "formats": [
            {"height": 1080, "width": 1920, "vcodec": "avc1", "acodec": "none"},
            {"vcodec": "none", "acodec": "opus"},
        ],
        "subtitles": {"en": [], "cs": [], "live_chat": []},
        "automatic_captions": {"de": []},
    }
    result = probe_with(monkeypatch, info)(VIDEO)
    assert result["kind"] == "single" and result["heights"] == [1080] and result["audio"] and result["video"]
    assert result["subtitles"] == ["cs", "en"] and result["auto_subtitles"] == ["de"]
    assert downloader.thumbnail_url(result["probe"], 0) == "https://i.ytimg.com/x.jpg"
    assert downloader.thumbnail_url(result["probe"], 1) is None


def test_a_social_post_gets_a_clean_title(monkeypatch):
    info = {
        "id": "1829536811584559",
        "title": "22K reactions · 2.4K shares | Lovely song #music #reels | Chris Perry",
        "uploader": "Chris Perry",
        "extractor_key": "Facebook",
        "formats": [{"height": 1280, "width": 720, "vcodec": "h264", "acodec": "aac"}],
    }
    result = probe_with(monkeypatch, info)("https://www.facebook.com/reel/1829536811584559")
    assert result["title"] == "Lovely song" and result["heights"] == [720]


def test_a_playlist_lists_its_entries(monkeypatch):
    info = {
        "_type": "playlist",
        "title": "Mix",
        "entries": [
            {"id": "a", "title": "One", "url": "https://www.youtube.com/watch?v=a", "duration": 10},
            {"id": "b", "title": "[Private video]", "url": "https://www.youtube.com/watch?v=b"},
            {"id": "c", "title": "Three", "url": "file:///etc/passwd"},
            None,
        ],
    }
    result = probe_with(monkeypatch, info)("https://www.youtube.com/playlist?list=PLx")
    assert result["kind"] == "playlist" and [e["title"] for e in result["entries"]] == ["One"]


def test_probe_errors_are_explained(monkeypatch):
    for url, code in [("https://open.spotify.com/track/abc", "download_spotify"), ("ftp://x", "download_invalid_url")]:
        with pytest.raises(JobError, match=code):
            downloader.probe(url)
    with pytest.raises(JobError) as exc:
        probe_with(
            monkeypatch,
            None,
            1,
            "ERROR: [Instagram] x: Requested content is not available, rate-limit reached or login required",
        )("https://www.instagram.com/reel/x/")
    assert exc.value.code == "login_required"
    with pytest.raises(JobError, match="download_unsupported"):
        probe_with(monkeypatch, None, 1, "ERROR: Unsupported URL: https://example.com")("https://example.com")
    with pytest.raises(JobError, match="download_empty"):
        probe_with(monkeypatch, {"_type": "playlist", "entries": []})("https://www.youtube.com/playlist?list=PLx")


def fake_download(monkeypatch, write, returncode=0, lines=()):
    def run(cmd, on_line, timeout=None, cwd=None, on_start=None):
        workdir = Path(cmd[cmd.index("-P") + 1])
        for line in lines:
            on_line(line)
        write(workdir)
        return completed(returncode, "", "")

    monkeypatch.setattr(analysis, "run_streaming", run)
    monkeypatch.setattr(downloader, "tool", lambda: "ffmpeg")
    monkeypatch.setattr(downloader, "audio_formats", lambda: list(downloader.AUDIO_FORMATS))


def wait(fetcher, timeout=10):
    deadline = time.time() + timeout
    while fetcher.busy() and time.time() < deadline:
        time.sleep(0.05)
    assert not fetcher.busy()


def test_a_download_is_placed_without_overwriting(workspace, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "My video.mp4").write_bytes(b"old")
    lines = ["[info] jNQXAC9IVRw: Downloading 1 format(s): 299+140", "SPOTCHECK downloading 50 100 NA 10 5"]

    def write(workdir):
        (workdir / "My video [id].mp4").write_bytes(b"x" * 1000)
        (workdir / "My video [id].en.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")

    fake_download(monkeypatch, write, lines=lines)
    fetcher = Downloader(Bus())
    fetcher.add([{"url": VIDEO, "title": "My video"}], {"mode": "video", "subtitles": "files"}, out)
    wait(fetcher)
    item = fetcher.public()[0]
    assert item["state"] == "done" and Path(item["output"]).name == "My video (2).mp4"
    assert [Path(p).name for p in item["extra_files"]] == ["My video (2).en.srt"]
    assert (out / "My video.mp4").read_bytes() == b"old"
    assert not list((workspace.home / "download-work").iterdir())


def test_embedded_subtitles_leave_no_extra_files(workspace, monkeypatch, tmp_path):
    def write(workdir):
        (workdir / "Clip [id].mp4").write_bytes(b"x" * 100)
        (workdir / "Clip [id].en.vtt").write_bytes(b"WEBVTT")

    fake_download(monkeypatch, write)
    fetcher = Downloader(Bus())
    fetcher.add([{"url": VIDEO, "title": "Clip"}], {"mode": "video", "subtitles": "embed"}, tmp_path / "out")
    wait(fetcher)
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["Clip.mp4"]


def test_a_failed_download_explains_itself_and_cleans_up(workspace, monkeypatch, tmp_path):
    lines = ["ERROR: [youtube] x: Video unavailable. This video is private"]
    fake_download(monkeypatch, lambda workdir: (workdir / "partial.mp4").write_bytes(b"x"), returncode=1, lines=lines)
    fetcher = Downloader(Bus())
    fetcher.add([{"url": VIDEO, "title": "Clip"}], {"mode": "video"}, tmp_path / "out")
    wait(fetcher)
    item = fetcher.public()[0]
    assert item["state"] == "failed" and item["error"] == "login_required" and "private" in item["detail"]
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []
    assert not list((workspace.home / "download-work").iterdir())


def test_a_blocked_download_is_retried(workspace, monkeypatch, tmp_path):
    calls = []

    def run(cmd, on_line, timeout=None, cwd=None, on_start=None):
        calls.append(cmd)
        workdir = Path(cmd[cmd.index("-P") + 1])
        if len(calls) == 1:
            on_line("ERROR: [youtube] x: Sign in to confirm you're not a bot")
            return completed(1)
        (workdir / "Clip [id].mp4").write_bytes(b"x")
        return completed(0)

    monkeypatch.setattr(analysis, "run_streaming", run)
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    monkeypatch.setattr(downloader, "tool", lambda: "ffmpeg")
    fetcher = Downloader(Bus())
    fetcher.add([{"url": VIDEO, "title": "Clip"}], {"mode": "video"}, tmp_path / "out")
    wait(fetcher)
    assert fetcher.public()[0]["state"] == "done" and len(calls) == 2
    assert not ytdlp.cooling()


def test_a_running_download_can_be_cancelled(workspace, monkeypatch, tmp_path):
    started = threading.Event()

    def run(cmd, on_line, timeout=None, cwd=None, on_start=None):
        workdir = Path(cmd[cmd.index("-P") + 1])
        (workdir / "partial.mp4").write_bytes(b"x")
        proc = subprocess.Popen(["python", "-c", "import time; time.sleep(30)"])
        on_start(proc)
        started.set()
        proc.wait()
        return completed(proc.returncode)

    monkeypatch.setattr(analysis, "run_streaming", run)
    monkeypatch.setattr(downloader, "tool", lambda: "ffmpeg")
    fetcher = Downloader(Bus())
    ids = fetcher.add([{"url": VIDEO, "title": "Clip"}], {"mode": "video"}, tmp_path / "out")
    assert started.wait(10)
    assert fetcher.cancel(ids[0])
    wait(fetcher)
    assert fetcher.public()[0]["state"] == "cancelled"
    assert not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []
    assert not list((workspace.home / "download-work").iterdir())
    new_id = fetcher.retry(ids[0])
    assert new_id != ids[0] and fetcher.find(ids[0]) is None
    fetcher.cancel(new_id)
    wait(fetcher)
    assert fetcher.clear() == 1 and fetcher.public() == []


def test_unavailable_sound_formats_are_refused(workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "tool", lambda: "ffmpeg")
    monkeypatch.setattr(downloader, "audio_formats", lambda: ["mp3"])
    with pytest.raises(JobError, match="preset_unavailable"):
        Downloader(Bus()).add([{"url": VIDEO}], {"mode": "audio", "audio": "ogg"}, tmp_path)
    with pytest.raises(JobError, match="download_invalid_url"):
        Downloader(Bus()).add([{"url": "file:///etc/passwd"}], {"mode": "audio"}, tmp_path)


def test_social_links_are_recognised():
    cases = {
        "https://www.facebook.com/reel/1829536811584559": (
            "facebook",
            "fb-1829536811584559",
            "https://www.facebook.com/reel/1829536811584559",
        ),
        "facebook.com/watch/?v=123&ref=x": ("facebook", "fb-123", "https://www.facebook.com/watch/?v=123"),
        "https://m.facebook.com/cnn/videos/10155529876156509/": (
            "facebook",
            "fb-10155529876156509",
            "https://www.facebook.com/watch/?v=10155529876156509",
        ),
        "https://fb.watch/abcDEF12/": ("facebook", "fb-abcDEF12", "https://fb.watch/abcDEF12/"),
        "https://www.instagram.com/reels/Cop84x6u7CP/?igsh=x": (
            "instagram",
            "ig-Cop84x6u7CP",
            "https://www.instagram.com/reel/Cop84x6u7CP/",
        ),
        "instagram.com/p/BQ0eAlwhDrw/": ("instagram", "ig-BQ0eAlwhDrw", "https://www.instagram.com/p/BQ0eAlwhDrw/"),
        "https://www.instagram.com/marvelskies.fc/reel/CWqAgUZgCku/": (
            "instagram",
            "ig-CWqAgUZgCku",
            "https://www.instagram.com/reel/CWqAgUZgCku/",
        ),
    }
    for url, (origin, ident, canonical) in cases.items():
        assert origins.classify_track(url) == (origin, ident), url
        assert origins.canonical_track_url(url) == canonical, url
    assert origins.classify_track("https://www.instagram.com/someone/") is None
    assert origins.classify_track("https://www.facebook.com/groups/123") is None


def test_social_titles_are_cleaned():
    fb = {"id": "1", "title": "22K reactions · 2.4K shares | Song title here #tag @user | Chris", "uploader": "Chris"}
    assert origins.social_title(fb, "facebook") == ("Chris", "Song title here")
    ig = {
        "id": "x",
        "title": "Video by someone",
        "uploader": "Some One",
        "description": "\nFirst line #a https://t.co/x\nsecond",
    }
    assert origins.social_title(ig, "instagram") == ("Some One", "First line")
    long = {"id": "x", "title": "Reel by a", "description": "word " * 40}
    artist, title = origins.social_title(long, "instagram")
    assert artist == "Instagram" and len(title) <= origins.SOCIAL_TITLE_LIMIT + 1 and title.endswith("…")
    assert (
        origins.social_title({"id": "9", "title": "Video by a", "description": "#only #tags"}, "facebook")[1]
        == "Facebook 9"
    )


def test_a_social_post_without_duration_takes_it_from_the_file(monkeypatch):
    monkeypatch.setattr(analysis, "pause", lambda seconds: None)
    info = {"id": "x", "title": "Video by a", "uploader": "A", "description": "Nice clip", "formats": [{"url": "u"}]}
    monkeypatch.setattr(analysis, "run_text", lambda *a, **k: completed(0, json.dumps(info), ""))
    songs = origins.load_track("https://www.instagram.com/reel/x/", ["yt-dlp"])
    assert songs[0]["song_id"] == "ig-x" and songs[0]["name"] == "Nice clip" and songs[0]["duration"] == 0
    assert songs[0]["official"] is None


def test_thumbnails_are_served_only_as_images():
    assert image_type(b"\xff\xd8\xffdata") == "image/jpeg"
    assert image_type(b"RIFF1234WEBPVP8 ") == "image/webp"
    assert image_type(b"<html>") is None


@pytest.fixture()
def client(workspace):
    terms.accept()
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as c:
        yield c


def test_download_state_and_errors(client, monkeypatch):
    state = client.get("/api/download").json()
    assert state["containers"] == ["mp4", "mkv", "webm"] and 2160 in state["heights"] and state["items"] == []
    response = client.post("/api/download/probe", json={"url": "https://open.spotify.com/track/x"}, headers=HEADERS)
    assert response.status_code == 422 and response.json()["detail"] == "download_spotify"
    assert client.get("/api/download/thumb/nope/0").status_code == 404
    bad = client.post("/api/download", json={"entries": [{"url": VIDEO}], "options": {"mode": "x"}}, headers=HEADERS)
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_option"
    assert client.post("/api/download", json={"entries": [], "options": {}}, headers=HEADERS).status_code == 422
    relative = client.post(
        "/api/download", json={"entries": [{"url": VIDEO}], "options": {}, "folder": "rel"}, headers=HEADERS
    )
    assert relative.json()["detail"] == "folder_not_absolute"


def test_audio_formats_follow_the_ffmpeg_in_use(monkeypatch):
    monkeypatch.setattr(
        convert,
        "capabilities",
        lambda refresh=False: {"ffmpeg": "f", "encoders": {"libmp3lame": {}, "aac": {}, "flac": {}}},
    )
    assert downloader.audio_formats() == ["mp3", "m4a", "flac"]

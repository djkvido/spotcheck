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


def test_size_estimates_prefer_direct_formats_and_matching_codecs():
    info = {
        "duration": 100,
        "formats": [
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 1_600_000, "protocol": "https"},
            {"format_id": "251", "vcodec": "none", "acodec": "opus", "filesize": 1_400_000, "protocol": "https"},
            {
                "format_id": "137",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "width": 1920,
                "filesize": 20_000_000,
                "protocol": "https",
            },
            {
                "format_id": "248",
                "vcodec": "vp9",
                "acodec": "none",
                "height": 1080,
                "width": 1920,
                "filesize": 15_000_000,
                "protocol": "https",
            },
            {
                "format_id": "hls-1080",
                "vcodec": "avc1.640028",
                "acodec": "mp4a",
                "height": 1080,
                "width": 1920,
                "tbr": 9000,
                "protocol": "m3u8_native",
            },
            {
                "format_id": "22",
                "vcodec": "avc1.64001F",
                "acodec": "mp4a",
                "height": 720,
                "width": 1280,
                "tbr": 1000,
                "protocol": "https",
            },
        ],
    }
    found, audio = downloader.variants(info)
    assert audio == {"aac": 1_600_000, "opus": 1_400_000, "best": 1_600_000}
    full = found[0]
    assert full["height"] == 1080 and full["exact"] is True
    assert full["bytes"] == {"mp4": 21_600_000, "webm": 16_400_000, "mkv": 16_600_000}
    small = found[1]
    assert small["height"] == 720 and small["bytes"]["mp4"] == 12_500_000 and small["exact"] is False


def test_failures_are_named_and_details_lose_cookie_advice():
    assert downloader.failure("ERROR: Video unavailable") == "download_unavailable"
    assert downloader.failure("ERROR: The uploader has not made this video available in your country") == "download_geo"
    assert downloader.failure("ERROR: Requested format is not available") == "download_format"
    assert downloader.failure("ERROR: This live event will begin in 3 hours") == "download_live"
    assert downloader.failure("ERROR: Login required to view this post") == "login_required"
    assert downloader.failure("something strange") == "download_failed"
    detail = downloader.clean_detail(
        "ERROR: [instagram] x: Requested content is not available. Use --cookies-from-browser or --cookies\n"
        "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp\n"
    )
    assert detail == "ERROR: [instagram] x: Requested content is not available"


def test_webm_is_recoded_and_offered_only_with_its_encoders(monkeypatch):
    webm = joined(Options(mode="video", container="webm"))
    assert "--merge-output-format webm/mkv --recode-video webm" in webm and "--remux-video" not in webm
    assert "--limit-rate 2M" in " ".join(
        downloader.build_command(["yt-dlp"], Options(mode="video"), VIDEO, Path("w"), "ffmpeg", "2M")
    )
    assert "--limit-rate" not in " ".join(
        downloader.build_command(["yt-dlp"], Options(mode="video"), VIDEO, Path("w"), "ffmpeg", "9G")
    )
    caps = {"ffmpeg": "f", "encoders": {"libx264": {}, "aac": {}}}
    monkeypatch.setattr(convert, "capabilities", lambda refresh=False: caps)
    assert downloader.containers() == ["mp4", "mkv"]
    caps["encoders"].update({"libvpx-vp9": {}, "libopus": {}})
    assert downloader.containers() == ["mp4", "mkv", "webm"]


def test_paused_queue_waits_and_limit_is_checked(workspace, monkeypatch, tmp_path):
    fake_download(monkeypatch, lambda workdir: (workdir / "Clip [id].mp4").write_bytes(b"x"))
    bus = Bus()
    fetcher = Downloader(bus)
    assert fetcher.configure(paused=True, limit="5M") == {"paused": True, "limit": "5M"}
    fetcher.add([{"url": VIDEO, "title": "Clip"}], {"mode": "video"}, tmp_path / "out")
    time.sleep(0.5)
    assert fetcher.public()[0]["state"] == "queued"
    with pytest.raises(JobError, match="invalid_option"):
        fetcher.configure(limit="1T")
    assert fetcher.configure(paused=False, limit="none") == {"paused": False, "limit": None}
    wait(fetcher)
    assert fetcher.public()[0]["state"] == "done"
    assert {"type": "download_settings", "paused": True, "limit": "5M"} in bus.events


def test_entries_keep_their_own_options_and_duplicates_are_merged(workspace, monkeypatch, tmp_path):
    fake_download(monkeypatch, lambda workdir: None)
    monkeypatch.setattr(downloader, "containers", lambda: ["mp4", "mkv"])
    fetcher = Downloader(Bus())
    fetcher.configure(paused=True)
    other = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    ids = fetcher.add(
        [{"url": VIDEO}, {"url": other, "options": {"mode": "audio", "audio": "flac"}}, {"url": VIDEO}],
        {"mode": "video", "container": "mkv"},
        tmp_path,
    )
    assert ids[0] == ids[2] and len(set(ids)) == 2
    items = {i["url"]: i for i in fetcher.public()}
    assert items[VIDEO]["options"]["container"] == "mkv" and items[VIDEO]["ext"] == "mkv"
    assert items[other]["options"]["mode"] == "audio" and items[other]["ext"] == "flac"
    assert fetcher.add([{"url": VIDEO}], {"mode": "video", "container": "mkv"}, tmp_path) == [ids[0]]
    assert len(fetcher.add([{"url": VIDEO}], {"mode": "video", "container": "mp4"}, tmp_path)) == 1
    assert len(fetcher.public()) == 3
    with pytest.raises(JobError, match="preset_unavailable"):
        fetcher.add([{"url": VIDEO}], {"mode": "video", "container": "webm"}, tmp_path)
    fetcher.cancel_all()
    fetcher.close()


def test_the_real_quality_is_described(workspace, monkeypatch, tmp_path):
    fetcher = Downloader(Bus())
    item = {"notes": [], "extra_files": [], "actual": None}
    info = {"video": {"width": 1280, "height": 720, "codec": "h264"}, "audio": {"codec": "aac"}, "duration": 19}
    monkeypatch.setattr(convert, "probe", lambda path: info)
    fetcher._describe(item, Options(mode="video", height=1080, subtitles="embed"), tmp_path / "x.mp4")
    assert item["actual"] == {"height": 720, "vcodec": "h264", "acodec": "aac", "duration": 19}
    assert item["notes"] == ["lower_quality", "no_subtitles"]
    item = {"notes": [], "extra_files": ["a.srt"], "actual": None}
    fetcher._describe(item, Options(mode="video", height=720, subtitles="files"), tmp_path / "x.mp4")
    assert item["notes"] == []

    def broken(path):
        raise JobError("probe_failed")

    monkeypatch.setattr(convert, "probe", broken)
    item = {"notes": [], "extra_files": [], "actual": None}
    fetcher._describe(item, Options(mode="video"), tmp_path / "x.mp4")
    assert item["actual"] is None


def test_download_folder_space_and_settings_endpoints(client, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr("spotcheck.fileops.reveal", lambda path: opened.append(path))
    space = client.post("/api/download/space", json={"initial": str(tmp_path)}, headers=HEADERS).json()
    assert space["folder"] == str(tmp_path) and space["free"] > 0
    assert client.post("/api/download/space", json={"initial": "rel"}, headers=HEADERS).status_code == 422
    target = tmp_path / "new"
    assert client.post("/api/download/open-folder", json={"initial": str(target)}, headers=HEADERS).json()["ok"]
    assert target.is_dir() and opened == [target]
    settings = client.post("/api/download/settings", json={"paused": True, "limit": "1M"}, headers=HEADERS)
    assert settings.json() == {"paused": True, "limit": "1M"}
    assert client.get("/api/download").json()["paused"] is True
    bad = client.post("/api/download/settings", json={"limit": "7Q"}, headers=HEADERS)
    assert bad.status_code == 422
    client.post("/api/download/settings", json={"paused": False, "limit": "none"}, headers=HEADERS)
    assert client.post("/api/download/nope/open", headers=HEADERS).status_code == 404
    assert client.post("/api/download/nope/reveal", headers=HEADERS).status_code == 404
    assert client.post("/api/download/nope/cancel", headers=HEADERS).json() == {"ok": False}

import time

import pytest
from conftest import make_song

from spotcheck import terms
from spotcheck.engine import Bus, Engine
from spotcheck.library import Library


@pytest.fixture()
def engine(workspace):
    lib = Library()
    lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1"), make_song("t2"), make_song("t3")])
    return Engine(lib, Bus())


def track(verdict=None, ber=None, check_verdict="OK", extra=0.0):
    return {
        "verify": {"verdict": verdict, "ber": ber} if verdict else None,
        "check": {"verdict": check_verdict, "extra": extra, "diff": extra, "lead": 0, "tail": 0},
    }


def test_needs_fix(engine):
    assert engine.needs_fix(track("MISMATCH", 0.45))
    assert engine.needs_fix(track("UNSURE", 0.33))
    assert engine.needs_fix(track("MATCH", 0.05, "FAIL", extra=7.0))
    assert not engine.needs_fix(track("MATCH", 0.05, "WARN", extra=1.0))
    assert not engine.needs_fix(track("NO_PREVIEW", None, "OK"))


def test_acceptable(engine):
    assert engine.acceptable(track("MATCH", 0.1))
    assert not engine.acceptable(track("MATCH", 0.1, "FAIL", extra=6))
    assert engine.acceptable(track("NO_PREVIEW", None, "OK", extra=1.0))
    assert not engine.acceptable(track("NO_PREVIEW", None, "OK", extra=9.0))
    assert not engine.acceptable(track("UNSURE", 0.33))


def test_is_better_requires_a_clear_improvement(engine):
    current = track("UNSURE", 0.33)
    assert engine.is_better({"ber": 0.10, "diff": 0.5}, current)
    assert not engine.is_better({"ber": 0.30, "diff": 0.5}, current)


def test_matching_file_is_only_replaced_to_remove_an_intro(engine):
    with_intro = track("MATCH", 0.05, "FAIL", extra=8.0)
    assert engine.is_better({"ber": 0.08, "diff": 1.0}, with_intro)
    assert not engine.is_better({"ber": 0.08, "diff": 9.0}, with_intro)
    assert not engine.is_better({"ber": 0.01, "diff": 0.0}, track("MATCH", 0.05))


def test_select_by_key_and_filter(engine):
    pl = engine.lib.data["playlists"][0]
    pl["tracks"][1]["file"] = "A/x.mp3"
    pl["tracks"][2]["status"] = "bad"
    assert [t["id"] for _, t in engine.select(only="missing")] == ["t1", "t3"]
    assert [t["id"] for _, t in engine.select(only="problems")] == ["t3"]
    assert [t["id"] for _, t in engine.select(track_ids=["A:t2"])] == ["t2"]


def test_refresh_notes_explains_the_verdict(engine):
    silence = {"code": "lead_silence", "params": {"seconds": 3.0}}
    tr = {"check": {"notes": [silence]}, "verify": {"verdict": "UNSURE", "ber": 0.33}, "proposal": None}
    engine.refresh_notes(tr)
    assert tr["notes"] == [{"code": "unsure", "params": {"ber": 0.33}}, silence]


def test_refresh_notes_puts_a_proposal_first(engine):
    tr = {
        "check": {"notes": []},
        "verify": {"verdict": "NO_PREVIEW", "reason": "no_preview"},
        "proposal": {"ber": 0.04},
    }
    engine.refresh_notes(tr)
    assert [n["code"] for n in tr["notes"]] == ["proposal", "no_preview"]


def test_log_entries_carry_a_code_and_parameters():
    bus = Bus()
    bus.log("info", "trimmed", "t1", start=1.5, end=0.0)
    entry = bus.logs[-1]
    assert (entry["level"], entry["code"], entry["track"]) == ("info", "trimmed", "t1")
    assert entry["params"] == {"start": 1.5, "end": 0.0}


def test_failed_job_reports_codes(engine):
    job_id = engine.submit("apply", "apply", {"name": "Song t1"}, key="A:t1")
    job = engine.jobs[job_id]
    deadline = time.monotonic() + 5
    while job["finished"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert job["label"] == {"code": "apply", "params": {"name": "Song t1"}}
    assert (job["state"], job["summary"]["error"]) == ("failed", "proposal_not_found")
    assert engine.bus.logs[-1]["code"] == "proposal_not_found"


def test_explicit_search_for_one_track_always_runs_and_reports(engine, workspace, monkeypatch):
    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][0]
    folder = workspace.downloads / pl["folder"]
    folder.mkdir()
    (folder / "x.mp3").write_bytes(b"x")
    tr["file"] = f"{pl['folder']}/x.mp3"
    tr["check"] = {"verdict": "OK", "extra": 0, "notes": []}
    tr["verify"] = {"verdict": "NO_PREVIEW", "reason": "no_preview"}
    monkeypatch.setattr(engine, "improve", lambda job, pl, tr, path: "none")
    job = {"_outcomes": [], "_failed": 0, "_cancel": False, "current": "", "id": "j"}
    engine.process_track(job, pl, tr, {"fix": True, "force_fix": True})
    assert job["_outcomes"] == ["none"]
    engine.process_track(job, pl, tr, {"fix": True})
    assert job["_outcomes"] == ["none", "not_needed"]


def test_locked_file_is_reported_without_losing_backups(engine, workspace, monkeypatch):
    from spotcheck import analysis, fileops

    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][0]
    folder = workspace.downloads / pl["folder"]
    folder.mkdir()
    song = folder / "x.mp3"
    song.write_bytes(b"x")
    tr["file"] = f"{pl['folder']}/x.mp3"
    tr["check"] = {"verdict": "WARN", "lead": 4.0, "tail": 0.0, "file_len": 200.0, "notes": []}

    def locked(path, start, length):
        raise fileops.FileLockedError(path)

    monkeypatch.setattr(analysis, "trim", locked)
    with pytest.raises(fileops.FileLockedError):
        engine.maybe_trim(pl, tr, song, backup=True)
    assert tr["backups"] == []
    assert not any((workspace.backups / tr["id"]).iterdir())


def test_automation_checks_new_and_changed_files(engine, workspace):
    pl = engine.lib.data["playlists"][0]
    folder = workspace.downloads / pl["folder"]
    folder.mkdir()
    song = folder / "Artist - Song.mp3"
    song.write_bytes(b"x" * 100)
    tr = pl["tracks"][0]
    tr["file"] = f"{pl['folder']}/Artist - Song.mp3"
    assert engine.lib.changed_tracks() == ["A:t1"]
    tr["check"] = {"verdict": "OK"}
    assert engine.lib.changed_tracks() == ["A:t1"]
    tr["check"] = {"verdict": "OK", "cutoff_measured": True}
    assert engine.lib.changed_tracks() == []
    song.write_bytes(b"y" * 200)
    assert engine.lib.changed_tracks() == ["A:t1"]


def test_automation_waits_until_the_terms_are_accepted(engine, monkeypatch):
    engine.lib.data["playlists"][0]["synced"] = time.time() - 25 * 3600
    submitted = []
    monkeypatch.setattr(engine, "submit", lambda kind, label, *args, **kwargs: submitted.append(label) or label)
    assert engine.automate() == []
    terms.accept()
    assert engine.automate() == ["auto_sync"]


def test_automation_updates_playlists_on_schedule(engine):
    pl = engine.lib.data["playlists"][0]
    assert engine.lib.due_for_sync(24) == []
    pl["synced"] = time.time() - 25 * 3600
    assert engine.lib.due_for_sync(24) == ["A"]
    assert engine.lib.due_for_sync(0) == []


def test_loud_peaks_and_quiet_files_are_reported_without_changing_the_verdict(engine, workspace, monkeypatch):
    from spotcheck import analysis, tags

    song = workspace.downloads / "x.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(analysis, "decode_check", lambda path: (0, 200.0))
    monkeypatch.setattr(analysis, "duration", lambda path: 200.0)
    monkeypatch.setattr(analysis, "silence_edges", lambda path, length: (0.0, 0.0))
    monkeypatch.setattr(analysis, "head_volume", lambda path: -12.0)
    monkeypatch.setattr(analysis, "head_gap", lambda path, lead: None)
    monkeypatch.setattr(analysis, "bitrate_kbps", lambda path: 320)
    monkeypatch.setattr(analysis, "codec_name", lambda path: "MP3")
    monkeypatch.setattr(analysis, "cutoff_khz", lambda path, length: (True, None))
    monkeypatch.setattr(tags, "missing", lambda path: [])
    tr = engine.lib.data["playlists"][0]["tracks"][0]

    monkeypatch.setattr(analysis, "loudness", lambda path: {"lufs": -7.84, "lra": 4.0, "peak": 1.66})
    check = engine.do_check(tr, song)
    assert check["verdict"] == "OK"
    assert (check["lufs"], check["true_peak"], check["lra"]) == (-7.8, 1.7, 4.0)
    assert check["notes"] == [{"code": "clipping", "params": {"db": 1.7}}]

    monkeypatch.setattr(analysis, "loudness", lambda path: {"lufs": -27.0, "lra": 9.0, "peak": -9.0})
    assert engine.do_check(tr, song)["notes"] == [{"code": "quiet", "params": {"lufs": -27.0}}]

    monkeypatch.setattr(analysis, "loudness", lambda path: None)
    check = engine.do_check(tr, song)
    assert check["notes"] == [] and check["lufs"] is None


def test_a_job_is_finished_only_after_the_library_was_saved(engine, monkeypatch):
    order = []
    real_save = engine.lib.save

    def save():
        order.append(("save", engine.jobs[job_id]["state"]))
        real_save()

    monkeypatch.setattr(engine.lib, "save", save)
    job_id = engine.submit("organize", "organize")
    deadline = time.monotonic() + 10
    while engine.jobs[job_id]["finished"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert order[-1] == ("save", "running")
    assert engine.jobs[job_id]["state"] == "done"


def test_a_failing_save_does_not_stop_the_worker(engine, monkeypatch):
    def broken():
        raise OSError("disk full")

    monkeypatch.setattr(engine.lib, "save", broken)
    first = engine.submit("organize", "organize")
    second = engine.submit("organize", "organize")
    deadline = time.monotonic() + 10
    while engine.jobs[second]["finished"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert engine.jobs[first]["state"] == engine.jobs[second]["state"] == "done"
    assert any(e["code"] == "save_failed" for e in engine.bus.logs)


def test_a_check_cut_short_by_cancelling_is_not_kept(engine, workspace, monkeypatch):
    from spotcheck import analysis

    pl = engine.lib.data["playlists"][0]
    tr = pl["tracks"][0]
    folder = workspace.downloads / pl["folder"]
    folder.mkdir()
    (folder / "x.mp3").write_bytes(b"x")
    tr["file"] = f"{pl['folder']}/x.mp3"
    tr["check"] = {"verdict": "OK", "extra": 0, "notes": [], "cutoff_measured": True}

    def stopped(path):
        raise analysis.ProcessStopped("ffmpeg")

    monkeypatch.setattr(analysis, "decode_check", stopped)
    job = {"_outcomes": [], "_failed": 0, "_cancel": True, "current": "", "id": "j"}
    from spotcheck.errors import Cancelled

    with pytest.raises(Cancelled):
        engine.process_track(job, pl, tr, {"validate": True})
    assert tr["check"]["verdict"] == "OK"
    assert job["_failed"] == 0

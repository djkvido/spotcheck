import csv
import io
import json
import sys

import pytest
from conftest import make_song

from spotcheck import __main__, projects, report, terms
from spotcheck.engine import Engine
from spotcheck.library import Library, compute_status


@pytest.fixture()
def library(workspace):
    projects.activate(projects.current())
    lib = Library()
    songs = [make_song("t1", "Fine"), make_song("t2", "=Wrong", "Кино"), make_song("t3", "Gone")]
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "Mix", songs)["playlist"]
    folder = workspace.downloads / "Mix"
    folder.mkdir()
    for tr in pl["tracks"][:2]:
        name = f"{tr['artist']} - {tr['name']}.mp3"
        (folder / name).write_bytes(b"x" * 100)
        tr["file"] = f"Mix/{name}"
    pl["tracks"][0].update(verify={"verdict": "MATCH", "ber": 0.05}, check={"verdict": "OK", "cutoff_measured": True})
    pl["tracks"][1].update(
        verify={"verdict": "MISMATCH", "ber": 0.45},
        check={"verdict": "OK", "cutoff_measured": True},
        notes=[{"code": "mismatch", "params": {}}],
    )
    for tr in pl["tracks"]:
        tr["status"] = compute_status(tr)
    lib.save()
    return lib


def test_the_report_lists_counts_and_what_needs_attention(library):
    data = report.summary(library, projects.current())
    assert data["tracks"] == 3
    assert (data["statuses"]["ok"], data["statuses"]["bad"], data["statuses"]["missing"]) == (1, 1, 1)
    assert [p["status"] for p in data["problems"]] == ["bad", "missing"]
    text = report.render(data)
    assert "3 tracks: 1 verified, 1 wrong, 1 missing" in text
    assert "[wrong] Mix: Кино - =Wrong (mismatch)" in text
    rows = list(csv.reader(io.StringIO(report.render(data, "csv"))))
    assert rows[1][:4] == ["Mix", "Кино", "'=Wrong", "wrong"]
    assert json.loads(report.render(data, "json"))["statuses"]["bad"] == 1
    assert report.exit_code(data) == 2


def run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["spotcheck", *args])
    monkeypatch.setattr(__main__, "running_instance", lambda host, port: False)
    monkeypatch.setattr(__main__, "configure", lambda home=None: None)
    monkeypatch.setattr(__main__.runtime, "prepare", lambda packaged, home=None: None)
    with pytest.raises(SystemExit) as stop:
        __main__.main()
    out = capsys.readouterr()
    return stop.value.code, out.out, out.err


def test_the_report_needs_accepted_terms(library, monkeypatch, capsys):
    code, out, err = run_cli(monkeypatch, capsys, "--report")
    assert code == 1 and "terms of use" in err and out == ""


def test_the_report_and_its_exit_code_on_the_command_line(library, monkeypatch, capsys):
    terms.accept()
    code, out, _ = run_cli(monkeypatch, capsys, "--report", "json")
    assert code == 2
    assert json.loads(out)["tracks"] == 3


def test_check_verifies_changed_files_without_the_app(library, monkeypatch, capsys):
    terms.accept()
    checked = []

    def fake_check(self, tr, path):
        checked.append(tr["id"])
        return {"verdict": "OK", "cutoff_measured": True, "sig": self.lib.signature(path), "notes": []}

    monkeypatch.setattr(Engine, "do_check", fake_check)
    monkeypatch.setattr(Engine, "do_verify", lambda self, tr, path: {"verdict": "MATCH", "ber": 0.04})
    monkeypatch.setattr(Engine, "organize", lambda self, ids=None: {})
    code, out, err = run_cli(monkeypatch, capsys, "--check", "all")
    assert sorted(checked) == ["t1", "t2"]
    assert code == 0
    assert "Checked 2 of 2 files" in err
    assert "2 verified, 1 missing" in out
    assert Library().data["playlists"][0]["tracks"][1]["status"] == "ok"

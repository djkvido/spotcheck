from conftest import make_song

from spotcheck import analysis, sources
from spotcheck.engine import Bus, Engine
from spotcheck.library import Library

WRONG = {"src": "Deezer", "title": "Song (Pianoforte)", "link": "d/3", "file": "a.mp3", "picker": 2}
RIGHT = {"src": "Deezer", "title": "Song", "link": "d/1", "file": "a-alt1.mp3", "picker": 2}


def engine_with_file(workspace):
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1")])["playlist"]
    return Engine(lib, Bus()), pl["tracks"][0]


def fake_alignment(monkeypatch, bers):
    monkeypatch.setattr(analysis, "ffmpeg_info", lambda refresh=False: {"chromaprint": True})
    monkeypatch.setattr(Engine, "preview_fingerprint", lambda self, preview: preview["file"])
    monkeypatch.setattr(
        analysis,
        "best_alignment",
        lambda path, fp: {"ber": bers[fp], "at": 10.0, "window": 30.0, "file_len": 200.0, "curve": [bers[fp]]},
    )


def test_a_wrong_preview_falls_back_to_the_next_best_one(workspace, monkeypatch, tmp_path):
    engine, tr = engine_with_file(workspace)
    remembered = []
    monkeypatch.setattr(sources, "get_preview", lambda song, st, force=False: WRONG)
    monkeypatch.setattr(sources, "alternative_previews", lambda song, st, used, limit=2: iter([RIGHT]))
    monkeypatch.setattr(sources, "remember_preview", lambda track_id, preview: remembered.append(preview))
    fake_alignment(monkeypatch, {"a.mp3": 0.42, "a-alt1.mp3": 0.05})
    verify = engine.do_verify(tr, tmp_path / "file.mp3")
    assert verify["verdict"] == "MATCH" and verify["ref"] is RIGHT and remembered == [RIGHT]


def test_a_really_wrong_file_stays_a_mismatch(workspace, monkeypatch, tmp_path):
    engine, tr = engine_with_file(workspace)
    monkeypatch.setattr(sources, "get_preview", lambda song, st, force=False: WRONG)
    monkeypatch.setattr(sources, "alternative_previews", lambda song, st, used, limit=2: iter([RIGHT]))
    fake_alignment(monkeypatch, {"a.mp3": 0.42, "a-alt1.mp3": 0.44})
    assert engine.do_verify(tr, tmp_path / "file.mp3")["verdict"] == "MISMATCH"


def test_a_match_does_not_look_for_alternatives(workspace, monkeypatch, tmp_path):
    engine, tr = engine_with_file(workspace)
    monkeypatch.setattr(sources, "get_preview", lambda song, st, force=False: RIGHT)
    monkeypatch.setattr(sources, "alternative_previews", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    fake_alignment(monkeypatch, {"a-alt1.mp3": 0.03})
    assert engine.do_verify(tr, tmp_path / "file.mp3")["verdict"] == "MATCH"


def test_mismatches_from_the_old_preview_choice_are_checked_again(workspace):
    lib = Library()
    pl = lib.upsert_playlist("https://open.spotify.com/playlist/A", "A", [make_song("t1"), make_song("t2", "Two")])[
        "playlist"
    ]
    folder = workspace.downloads / "A"
    folder.mkdir()
    for tr, name in zip(pl["tracks"], ("one.mp3", "two.mp3"), strict=True):
        (folder / name).write_bytes(b"x" * 100)
        tr["file"] = f"A/{name}"
        tr["check"] = {"cutoff_measured": True, "sig": lib.signature(folder / name)}
    pl["tracks"][0]["verify"] = {"verdict": "MISMATCH", "ber": 0.42, "ref": {"file": "x.mp3"}}
    pl["tracks"][1]["verify"] = {
        "verdict": "MISMATCH",
        "ber": 0.42,
        "ref": {"file": "y.mp3", "picker": sources.PREVIEW_PICKER},
    }
    assert lib.changed_tracks() == ["A:t1"]

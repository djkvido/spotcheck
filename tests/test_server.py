import os

import pytest
from fastapi.testclient import TestClient

from spotcheck import terms
from spotcheck.server import create_app

HEADERS = {"X-SpotCheck": "1"}


@pytest.fixture()
def fresh(workspace):
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture()
def client(fresh):
    terms.accept()
    return fresh


def test_nothing_changes_until_the_terms_are_accepted(fresh):
    state = fresh.get("/api/state").json()
    assert state["terms"]["accepted"] is False
    blocked = fresh.put("/api/settings", json={"threads": 2}, headers=HEADERS)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "terms_required"
    outdated = fresh.post("/api/terms", json={"version": "0"}, headers=HEADERS)
    assert outdated.json()["detail"] == "terms_outdated"
    accepted = fresh.post("/api/terms", json={"version": terms.VERSION}, headers=HEADERS)
    assert accepted.json()["accepted"] is True
    assert fresh.get("/api/state").json()["terms"]["accepted"] is True
    assert fresh.put("/api/settings", json={"threads": 2}, headers=HEADERS).status_code == 200


def test_state_and_ui_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/i18n.js").status_code == 200
    body = client.get("/api/state").json()
    assert body["library"]["playlists"] == []
    assert "accept_ber" in body["library"]["settings"]


def test_foreign_host_is_rejected(client):
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400


def test_mutations_require_custom_header(client):
    assert client.put("/api/settings", json={"threads": 2}).status_code == 403
    assert client.put("/api/settings", json={"threads": 2}, headers=HEADERS).status_code == 200


def test_invalid_settings_are_explained_per_field(client):
    response = client.put("/api/settings", json={"accept_ber": 5, "audio_providers": []}, headers=HEADERS)
    assert response.status_code == 422
    fields = response.json()["detail"]["fields"]
    assert fields["accept_ber"]["code"] == "out_of_range"
    assert fields["audio_providers"]["code"] == "select_one"
    saved = client.put("/api/settings", json={"accept_ber": 0.25}, headers=HEADERS).json()
    assert saved["accept_ber"] == 0.25


def test_state_describes_the_project_and_settings_limits(client):
    body = client.get("/api/state").json()
    assert body["project"]["name"] == "My library"
    assert body["settings_meta"]["bounds"]["accept_ber"] == [0.02, 0.48]


def test_projects_can_be_created_switched_and_removed(client, tmp_path):
    music = tmp_path / "elsewhere" / "Salsa"
    created = client.post("/api/projects", json={"name": "Salsa", "music": str(music)}, headers=HEADERS)
    assert created.status_code == 200
    salsa = created.json()["project"]
    assert music.is_dir()
    assert client.get("/api/state").json()["project"]["id"] == salsa["id"]

    taken = client.post("/api/projects", json={"name": "salsa", "music": str(tmp_path / "x")}, headers=HEADERS)
    assert taken.json()["detail"]["fields"]["name"]["code"] == "name_taken"
    shared = client.post("/api/projects", json={"name": "Other", "music": str(music / "sub")}, headers=HEADERS)
    assert shared.json()["detail"]["fields"]["music"]["code"] == "folder_in_use"

    assert client.delete(f"/api/projects/{salsa['id']}", headers=HEADERS).json()["detail"] == "project_active"
    assert client.post("/api/projects/default/open", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/projects/{salsa['id']}", headers=HEADERS).status_code == 200
    assert music.is_dir()


def test_audio_is_served_in_ranges_without_keeping_the_file_open(client, workspace):
    from spotcheck.server import read_range

    file = workspace.downloads / "a.mp3"
    file.write_bytes(bytes(range(256)) * 10)
    size, start, end, data = read_range(file, "bytes=10-19")
    assert (size, start, end, data) == (2560, 10, 19, bytes(range(10, 20)))
    assert read_range(file, "bytes=-6")[3] == (bytes(range(256)) * 10)[-6:]
    file.rename(workspace.downloads / "b.mp3")


def test_duplicate_and_invalid_links_are_separated():
    from spotcheck.server import split_urls

    pasted = "https://open.spotify.com/playlist/AAA?si=1\nhttps://open.spotify.com/playlist/AAA?si=2 nope"
    urls, rejected = split_urls(pasted)
    assert urls == ["https://open.spotify.com/playlist/AAA?si=1"]
    assert rejected == ["nope"]


def test_bad_requests(client):
    assert client.post("/api/process", json={"flags": {}}, headers=HEADERS).status_code == 400
    assert (
        client.post("/api/process", json={"flags": {"verify": True}, "only": "x"}, headers=HEADERS).status_code == 422
    )
    rejected = client.post("/api/playlists", json={"urls": "https://example.com"}, headers=HEADERS)
    assert (rejected.status_code, rejected.json()["detail"]) == (400, "invalid_playlist_url")
    assert client.get("/api/audio/nope:nope").status_code == 404
    assert client.post("/api/tracks/nope:nope/restore", json={"index": 0}, headers=HEADERS).status_code == 404


def test_job_label_must_be_a_message_code(client):
    body = {"flags": {"verify": True}, "label": "<b>Hello</b>"}
    assert client.post("/api/process", json=body, headers=HEADERS).status_code == 422
    body["label"] = "check"
    job_id = client.post("/api/process", json=body, headers=HEADERS).json()["job"]
    job = next(j for j in client.get("/api/state").json()["jobs"] if j["id"] == job_id)
    assert job["label"] == {"code": "check", "params": {}}


def test_static_files_are_revalidated(client):
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"


def test_links_from_every_supported_source_are_accepted():
    from spotcheck.server import split_urls

    pasted = """https://www.youtube.com/watch?v=fOoFshzL0k0&list=PL8nmK&index=3
https://www.youtube.com/playlist?list=PL8nmK
https://soundcloud.com/the-band/sets/the-ep
https://artist.bandcamp.com/album/first-light
https://www.youtube.com/watch?v=fOoFshzL0k0"""
    urls, rejected = split_urls(pasted)
    assert urls == [
        "https://www.youtube.com/playlist?list=PL8nmK",
        "https://soundcloud.com/the-band/sets/the-ep",
        "https://artist.bandcamp.com/album/first-light",
        "https://www.youtube.com/watch?v=fOoFshzL0k0",
    ]
    assert rejected == []


def test_file_settings_are_validated(client):
    response = client.put("/api/settings", json={"audio_format": "wav", "mp3_bitrate": "999k"}, headers=HEADERS)
    fields = response.json()["detail"]["fields"]
    assert fields["audio_format"]["code"] == "invalid_choice"
    assert fields["mp3_bitrate"]["code"] == "invalid_choice"
    saved = client.put("/api/settings", json={"audio_format": "opus", "number_files": True}, headers=HEADERS).json()
    assert (saved["audio_format"], saved["number_files"]) == ("opus", True)
    assert client.get("/api/state").json()["settings_meta"]["choices"]["audio_format"] == ["mp3", "opus", "m4a"]


def test_export_target_is_validated(client, workspace, tmp_path):
    from spotcheck.library import Library
    from spotcheck.server import export_target_error

    assert export_target_error("", workspace.downloads) == "required"
    assert export_target_error("relative/path", workspace.downloads) == "folder_not_absolute"
    assert export_target_error(str(workspace.downloads / "sub"), workspace.downloads) == "folder_inside_music"
    assert export_target_error(str(tmp_path / "usb"), workspace.downloads) is None

    assert client.post("/api/export", json={"mode": "copy"}, headers=HEADERS).status_code == 404
    Library().upsert_playlist("https://open.spotify.com/playlist/A", "A", [])
    client.post("/api/projects/default/open", headers=HEADERS)
    body = {"mode": "copy", "target": str(workspace.downloads)}
    response = client.post("/api/export", json=body, headers=HEADERS)
    assert response.json()["detail"]["fields"]["target"]["code"] == "folder_inside_music"


def test_tags_need_tracks_with_missing_tags(client):
    response = client.post("/api/tags", json={}, headers=HEADERS)
    assert (response.status_code, response.json()["detail"]) == (400, "no_tags_missing")


def test_responses_carry_security_headers(client):
    headers = client.get("/").headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def test_quit_stops_the_server_only_when_it_can(client):
    response = client.post("/api/quit", headers=HEADERS)
    assert (response.status_code, response.json()["detail"]) == (501, "quit_unavailable")
    calls = []
    client.app.state.shutdown = lambda: calls.append(True)
    assert client.post("/api/quit", headers=HEADERS).json() == {"ok": True, "pid": os.getpid()}
    assert client.post("/api/quit").status_code == 403

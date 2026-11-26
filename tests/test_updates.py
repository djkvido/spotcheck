import hashlib
import io
import time

import pytest
from fastapi.testclient import TestClient

from spotcheck import terms, updates
from spotcheck.errors import JobError
from spotcheck.server import create_app

HEADERS = {"X-SpotCheck": "1"}
BASE = "https://github.com/djkvido/spotcheck/releases/download/v9.0.0/"
NAME = "SpotCheck-9.0.0-windows-x64-setup.exe"
PAYLOAD = b"installer" * 1000


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def release(tag="v9.0.0", payload=PAYLOAD, **extra):
    data = {
        "tag_name": tag,
        "html_url": "https://github.com/djkvido/spotcheck/releases/tag/" + tag,
        "body": "### Added\n\n- **Updates** from the app\n\n## Download\n\nLong install text",
        "assets": [
            {"name": NAME, "browser_download_url": BASE + NAME, "size": len(payload)},
            {"name": "SHA256SUMS.txt", "browser_download_url": BASE + "SHA256SUMS.txt", "size": 100},
        ],
    }
    data.update(extra)
    return data


@pytest.fixture()
def windows(monkeypatch):
    monkeypatch.setattr(updates, "suffix", lambda: "-windows-x64-setup.exe")
    monkeypatch.setattr(updates.runtime, "packaged", lambda: True)
    monkeypatch.setattr(updates.runtime, "WINDOWS", True)


def serve(monkeypatch, data, payload=PAYLOAD, digest=None):
    digest = digest or hashlib.sha256(payload).hexdigest()
    files = {
        updates.API: __import__("json").dumps(data).encode(),
        BASE + "SHA256SUMS.txt": f"{'0' * 64}  other.dmg\n{digest}  {NAME}\n".encode(),
        BASE + NAME: payload,
    }

    def fake(url, timeout=30):
        if url not in files:
            raise OSError("not found")
        return Response(files[url], url.replace("github.com", "objects.githubusercontent.com"))

    monkeypatch.setattr(updates, "_open", fake)


def wait(updater, phases=("ready", "failed")):
    deadline = time.time() + 10
    while updater.phase not in phases and time.time() < deadline:
        time.sleep(0.02)
    return updater.phase


def test_a_newer_release_is_offered_with_its_notes(windows):
    found = updates.parse_release(release(), current="1.3.0")
    assert found["version"] == "9.0.0" and found["asset"]["url"] == BASE + NAME
    assert found["notes"] == "### Added\n\n- **Updates** from the app"
    assert updates.parse_release(release("v1.3.0"), current="1.3.0") is None
    assert updates.parse_release(release("v1.2.9"), current="1.3.0") is None
    assert updates.parse_release(release(prerelease=True), current="1.3.0") is None
    assert updates.parse_release(release("nightly"), current="1.3.0") is None
    odd = release(html_url="javascript:alert(1)")
    odd["assets"][0]["browser_download_url"] = "https://evil.example/" + NAME
    found = updates.parse_release(odd, current="1.3.0")
    assert found["asset"] is None and found["page"] == updates.RELEASES


def test_checksums_are_read_per_file():
    sums = f"{'a' * 64}  {NAME}\n{'b' * 64} *other.dmg\nbroken line\n"
    assert updates.expected_hash(sums, NAME) == "a" * 64
    assert updates.expected_hash(sums, "other.dmg") == "b" * 64
    assert updates.expected_hash(sums, "missing.exe") is None


def test_an_update_is_downloaded_verified_and_installed(workspace, windows, monkeypatch):
    serve(monkeypatch, release())
    bus = Bus()
    updater = updates.Updater(bus)
    info = updater.check(force=True)
    assert info["version"] == "9.0.0" and info["installable"] and info["size"] == len(PAYLOAD)
    updater.download()
    assert wait(updater) == "ready"
    assert (updates.folder() / NAME).read_bytes() == PAYLOAD
    assert not (updates.folder() / f"{NAME}.part").exists()
    started, stopped = [], []
    monkeypatch.setattr(updates.subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(updates.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(updates.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert updater.install(lambda: stopped.append(True)) == "restart"
    assert started[0][0].endswith(NAME) and "/RELAUNCH=1" in started[0] and "/SILENT" in started[0]
    assert stopped == [True] and updater.public()["phase"] == "installing"
    assert any(e["type"] == "app_update" and e["update"]["phase"] == "downloading" for e in bus.events)


def test_a_damaged_download_is_deleted(workspace, windows, monkeypatch):
    serve(monkeypatch, release(), digest="f" * 64)
    updater = updates.Updater(Bus())
    updater.check(force=True)
    updater.download()
    assert wait(updater) == "failed" and updater.error == "update_checksum"
    assert list(updates.folder().iterdir()) == []
    with pytest.raises(JobError, match="update_not_ready"):
        updater.install(lambda: None)


def test_a_failed_check_keeps_quiet_and_source_runs_cannot_install(workspace, windows, monkeypatch):
    monkeypatch.setattr(updates, "_open", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    updater = updates.Updater(Bus())
    assert updater.check(force=True) is None
    serve(monkeypatch, release())
    monkeypatch.setattr(updates.runtime, "packaged", lambda: False)
    info = updater.check(force=True)
    assert info["installable"] is False and info["packaged"] is False
    with pytest.raises(JobError, match="update_unavailable"):
        updater.download()


def test_old_installers_are_removed(workspace, windows, monkeypatch):
    updates.folder().mkdir(parents=True)
    (updates.folder() / "SpotCheck-1.0.0-windows-x64-setup.exe").write_bytes(b"old")
    serve(monkeypatch, release())
    updates.Updater(Bus()).check(force=True)
    assert list(updates.folder().iterdir()) == []


def test_update_endpoints(workspace, monkeypatch):
    terms.accept()
    monkeypatch.setattr(updates, "_open", lambda url, timeout=30: (_ for _ in ()).throw(OSError("offline")))
    with TestClient(create_app(automation=False), base_url="http://127.0.0.1") as client:
        assert client.get("/api/state").json()["app_update"] is None
        assert client.post("/api/update/check", headers=HEADERS).json() == {"update": None}
        assert client.post("/api/update/download", headers=HEADERS).json()["detail"] == "update_unavailable"
        response = client.post("/api/update/install", headers=HEADERS)
        assert response.status_code in (422, 501)
        assert client.post("/api/update/check").status_code == 403

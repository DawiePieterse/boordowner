"""GET /api/version: the field names Boord's heartbeat.ps1 reads from every
app (tag, state, frontend_version), plus this app's name and the VERSION the
header shows. Public, like every other endpoint here."""
import re

from routers import version


def test_version_reports_frontend_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "Boord Owner"
    assert set(body) >= {"app", "version", "tag", "state", "frontend_version"}
    assert body["state"] in ("release", "ahead", "unknown")
    # The number frontend/shared/api.js carries, read the same way.
    with open(version.API_JS, encoding="utf-8") as fh:
        expected = re.search(r'^  VERSION: "(.*)",$', fh.read(), re.MULTILINE).group(1)
    assert body["frontend_version"] == expected
    assert body["version"] == expected


def test_version_states_from_git(client, monkeypatch):
    def fake_git(args):
        return {"--exact-match": "", "--always": "v1.9-3-gabc1234"}[args[-1]]

    monkeypatch.setattr(version, "_git", fake_git)
    body = client.get("/api/version").json()
    assert body["state"] == "ahead" and body["tag"] == "v1.9-3-gabc1234"

    monkeypatch.setattr(version, "_git", lambda args: "v1.9" if "--exact-match" in args else "")
    body = client.get("/api/version").json()
    assert body["state"] == "release" and body["tag"] == "v1.9"

    monkeypatch.setattr(version, "_git", lambda args: "")
    body = client.get("/api/version").json()
    assert body["state"] == "unknown" and body["tag"] is None

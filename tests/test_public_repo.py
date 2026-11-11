"""Card « Signaler un problème : mail en principal, lien GitHub en second »,
11 Nov 2026, choice 3: the GitHub link is turned on by the server."""
from fastapi.testclient import TestClient

from sarcade import version
from sarcade.api.app import app


def test_public_repo_only_a_github_repository(monkeypatch):
    monkeypatch.delenv("SARCADE_PUBLIC_REPO_URL", raising=False)
    assert version.public_repo() is None
    monkeypatch.setenv("SARCADE_PUBLIC_REPO_URL", "https://github.com/sdubos-consulting/sarcade-app/")
    assert version.public_repo() == "https://github.com/sdubos-consulting/sarcade-app"
    for bad in ("http://github.com/a/b", "https://evil.example/a/b", "https://github.com/a", "javascript:alert(1)",
                "https://github.com/a/b/issues"):
        monkeypatch.setenv("SARCADE_PUBLIC_REPO_URL", bad)
        assert version.public_repo() is None, bad


def test_public_repo_in_version(monkeypatch):
    monkeypatch.setenv("SARCADE_PUBLIC_REPO_URL", "https://github.com/sdubos-consulting/sarcade-app")
    body = TestClient(app).get("/version").json()
    assert body["public_repo"] == "https://github.com/sdubos-consulting/sarcade-app"

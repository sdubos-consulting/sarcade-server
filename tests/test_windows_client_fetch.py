# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Windows installer taken from the windows-dev pre-release of GitHub and
distributed by the server (card « Windows : menu Mise à jour », lot 25)."""
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import admin
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import SecurityEventRow, ServerSettingRow
from sarcade.updates import service

INSTALLER = b"MZ-sarcade-installer" * 100
SHA = hashlib.sha256(INSTALLER).hexdigest()
MANIFEST = {"version": "0.1.0+960", "build": "main@abc1234", "sha256": SHA, "size": len(INSTALLER),
            "installer": "sarcade-windows-setup.exe"}


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


def github(manifest=None, installer=INSTALLER, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path.endswith("/sarcade-windows-version.json"):
            return httpx.Response(200, json=MANIFEST if manifest is None else manifest)
        if request.url.path.endswith("/sarcade-windows-setup.exe"):
            return httpx.Response(200, content=installer)
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_manifest_checked():
    assert service.parse_windows_manifest(MANIFEST)["version"] == "0.1.0+960"
    assert service.parse_windows_manifest({**MANIFEST, "sha256": SHA.upper()})["sha256"] == SHA
    for bad in ([], {**MANIFEST, "version": "latest"}, {**MANIFEST, "sha256": "abc"}, {**MANIFEST, "size": 0},
                {**MANIFEST, "size": True}):
        with pytest.raises(service.PackageFetchError):
            service.parse_windows_manifest(bad)


@pytest.mark.asyncio
async def test_installer_written_when_size_and_sha256_match(tmp_path):
    target = tmp_path / "clients" / "sarcade-windows-setup.exe"
    calls = []
    got = await service.fetch_windows_package(target, max_bytes=10 ** 6, client=github(calls=calls))
    assert got["changed"] and got["version"] == "0.1.0+960" and got["build"] == "main@abc1234"
    assert target.read_bytes() == INSTALLER
    assert calls == ["/sdubos-consulting/sarcade-app/releases/download/windows-dev/sarcade-windows-version.json",
                     "/sdubos-consulting/sarcade-app/releases/download/windows-dev/sarcade-windows-setup.exe"]
    # Same installer already there: only the manifest is read.
    calls.clear()
    again = await service.fetch_windows_package(target, current_sha256=SHA, max_bytes=10 ** 6, client=github(calls=calls))
    assert not again["changed"] and len(calls) == 1


@pytest.mark.asyncio
async def test_installer_refused_when_it_does_not_match(tmp_path):
    target = tmp_path / "sarcade-windows-setup.exe"
    target.write_bytes(b"previous")
    for client, reason in ((github(installer=INSTALLER[:-1] + b"X"), "installer_sha256_mismatch"),
                           (github(installer=INSTALLER + b"X"), "installer_size_mismatch"),
                           (github(installer=INSTALLER[:10]), "installer_size_mismatch"),
                           (github(manifest={**MANIFEST, "size": 10 ** 7}), "package_too_large"),
                           (httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
                            "manifest_http_404")):
        with pytest.raises(service.PackageFetchError) as exc:
            await service.fetch_windows_package(target, max_bytes=10 ** 6, client=client)
        assert str(exc.value) == reason
        # The package already distributed is kept, no temporary file left.
        assert target.read_bytes() == b"previous"
        assert [p.name for p in tmp_path.iterdir()] == ["sarcade-windows-setup.exe"]


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (ServerSettingRow, SecurityEventRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setenv("SARCADE_FILE_ROOT", str(tmp_path))
    monkeypatch.delenv("SARCADE_ADMIN_TOKEN", raising=False)
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_route_distributes_the_github_installer(client, tmp_path, monkeypatch):
    real = service.fetch_windows_package

    async def fake(target, **kw):
        return await real(target, client=github(), **kw)
    monkeypatch.setattr(service, "fetch_windows_package", fake)
    r = client.post("/api/v0.1/admin/clients/windows/fetch")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "0.1.0+960" and body["sha256"] == SHA and body["changed"] is True
    assert body["build"] == "main@abc1234" and body["url"] == "/api/v0.1/clients/windows/package"
    latest = client.get("/api/v0.1/clients/latest").json()
    assert latest["windows"]["version"] == "0.1.0+960" and latest["windows"]["sha256"] == SHA
    assert client.get("/api/v0.1/clients/windows/package").content == INSTALLER
    assert client.post("/api/v0.1/admin/clients/windows/fetch").json()["changed"] is False


def test_route_failure_keeps_the_package(client, db, monkeypatch):
    async def fail(target, **kw):
        raise service.PackageFetchError("manifest_http_404")
    monkeypatch.setattr(service, "fetch_windows_package", fail)
    r = client.post("/api/v0.1/admin/clients/windows/fetch")
    assert r.status_code == 502
    assert r.json()["detail"] == {"error": "windows_fetch_failed", "reason": "manifest_http_404"}
    assert client.get("/api/v0.1/clients/latest").json() == {}
    assert admin.CLIENT_FILES["windows"] == service.WINDOWS_INSTALLER


def test_route_needs_the_admin_token(client, monkeypatch):
    monkeypatch.setenv("SARCADE_ADMIN_TOKEN", "secret-token")
    assert client.post("/api/v0.1/admin/clients/windows/fetch").status_code == 401

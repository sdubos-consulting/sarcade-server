# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Downloads served by the server (card « Windows : publier l'installateur
sans GitHub », 8 Nov 2026): /telechargements, files of the image, Windows
package of /clients/latest taken from the image when newer."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import downloads
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import SecurityEventRow, ServerSettingRow
from sarcade.updates import service

EXE = b"MZ-setup" * 500
APK = b"PK-apk" * 300
SRC = b"\x1f\x8b-sources" * 50


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


def entry(kind, name, data, **extra):
    return {"kind": kind, "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "version": "0.1.0+1286", "build": "main@abc1234", "date": "2026-11-09", **extra}


@pytest.fixture
def root(tmp_path, monkeypatch):
    d = tmp_path / "downloads"
    d.mkdir()
    (d / "sarcade-windows-setup.exe").write_bytes(EXE)
    (d / "sarcade-android.apk").write_bytes(APK)
    (d / "sarcade-app-sources-abc1234.tar.gz").write_bytes(SRC)
    files = [entry("sources-app", "sarcade-app-sources-abc1234.tar.gz", SRC),
             entry("windows", "sarcade-windows-setup.exe", EXE, signed=False),
             entry("android", "sarcade-android.apk", APK)]
    (d / "downloads.json").write_text(json.dumps({"generated_at": "2026-11-09T10:00:00Z", "files": files}))
    monkeypatch.setenv("SARCADE_DOWNLOADS_ROOT", str(d))
    monkeypatch.setenv("SARCADE_FILE_ROOT", str(tmp_path / "files"))
    return d


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (ServerSettingRow, SecurityEventRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_manifest_sorted_and_checked(root):
    m = downloads.manifest()
    assert [f["kind"] for f in m["files"]] == ["windows", "android", "sources-app"]
    # A file whose size differs from the manifest is not offered.
    (root / "sarcade-android.apk").write_bytes(APK + b"x")
    assert [f["kind"] for f in downloads.manifest()["files"]] == ["windows", "sources-app"]


def test_bad_entries_ignored(root):
    files = [entry("windows", "../etc/passwd", EXE), entry("other", "x.bin", EXE),
             {**entry("windows", "sarcade-windows-setup.exe", EXE), "sha256": "abc"},
             entry("windows", "downloads.json", EXE)]
    (root / "downloads.json").write_text(json.dumps({"files": files}))
    assert downloads.manifest()["files"] == []


def test_no_manifest(tmp_path, monkeypatch, client):
    monkeypatch.setenv("SARCADE_DOWNLOADS_ROOT", str(tmp_path / "none"))
    assert downloads.manifest() == {"generated_at": None, "files": []}
    r = client.get("/telechargements")
    assert r.status_code == 200 and "Aucun fichier" in r.text


def test_page_lists_files(root, client):
    r = client.get("/telechargements")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "SARCADE pour Windows" in r.text and "SARCADE pour Android" in r.text
    assert hashlib.sha256(EXE).hexdigest() in r.text and "0.1.0+1286" in r.text
    assert "Exécuter quand même" in r.text  # unsigned installer explained
    assert 'href="/telechargements/sarcade-windows-setup.exe"' in r.text


def test_signed_installer_has_no_warning(root, client):
    m = json.loads((root / "downloads.json").read_text())
    m["files"][1]["signed"] = True
    (root / "downloads.json").write_text(json.dumps(m))
    assert "Exécuter quand même" not in client.get("/telechargements").text


def test_files_served(root, client):
    r = client.get("/telechargements/sarcade-windows-setup.exe")
    assert r.status_code == 200 and r.content == EXE
    r = client.get("/telechargements/sarcade-android.apk")
    assert r.headers["content-type"] == "application/vnd.android.package-archive" and r.content == APK
    assert client.get("/telechargements/downloads.json").status_code == 404
    assert client.get("/telechargements/inconnu.exe").status_code == 404
    api = client.get("/api/v0.1/downloads").json()
    assert api["files"][0]["url"] == "/telechargements/sarcade-windows-setup.exe"


def test_latest_offers_the_image_installer(root, client):
    latest = client.get("/api/v0.1/clients/latest").json()
    assert latest["windows"]["url"] == "/telechargements/sarcade-windows-setup.exe"
    assert latest["windows"]["sha256"] == hashlib.sha256(EXE).hexdigest()
    assert latest["windows"]["version"] == "0.1.0+1286" and latest["windows"]["source"] == "image"
    assert latest["apk"]["url"] == "/telechargements/sarcade-android.apk"


def test_newer_admin_package_wins(root, db, client):
    from datetime import UTC, datetime
    stored = {"windows": {"version": "0.1.0+1300", "sha256": "a" * 64, "size": 10, "uploaded_at": "x"}}
    service.set_value(db, "client_packages", stored, datetime.now(UTC))
    db.commit()
    latest = client.get("/api/v0.1/clients/latest").json()
    assert latest["windows"]["version"] == "0.1.0+1300"
    assert latest["windows"]["url"] == "/api/v0.1/clients/windows/package"
    # Older, or taken from GitHub: the image wins.
    for pkg in ({**stored["windows"], "version": "0.1.0+1200"}, {**stored["windows"], "source": "github"}):
        service.set_value(db, "client_packages", {"windows": pkg}, datetime.now(UTC))
        db.commit()
        assert client.get("/api/v0.1/clients/latest").json()["windows"]["source"] == "image"
    # Taken from the latest major release of GitHub (10 Nov 2026): kept while newer.
    service.set_value(db, "client_packages", {"windows": {**stored["windows"], "source": "release"}}, datetime.now(UTC))
    db.commit()
    assert client.get("/api/v0.1/clients/latest").json()["windows"]["source"] == "release"


def test_newer_counts_the_build_number():
    assert downloads.newer("0.1.0+1286", "0.1.0+1285")
    assert not downloads.newer("0.1.0+99", "0.1.0+1285")
    assert downloads.newer("0.2.0+1", "0.1.0+1285")
    assert downloads.newer("0.1.0+1", None)


def test_privacy_page(client):
    r = client.get("/confidentialite")
    assert r.status_code == 200 and "Politique de confidentialité" in r.text

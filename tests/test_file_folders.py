# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Subfolders of the event and personal files (29 Oct 2026)."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import app as app_module
from sarcade.api import personal_files
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import (EventRow, FileFolderRow, LogbookRow, PersonalFileRow, ServerSettingRow,
                               SharedFileRow)
from sarcade.files import folders


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, LogbookRow, SharedFileRow, PersonalFileRow, FileFolderRow, ServerSettingRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active",
                         created_at=datetime(2026, 10, 29, tzinfo=UTC), version=0))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "FILE_ROOT", tmp_path / "event")
    monkeypatch.setattr(personal_files, "ROOT", tmp_path / "personal")
    monkeypatch.setattr(personal_files, "_owner", lambda request: "a@x.fr")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("raw, path", [("", ""), (None, ""), ("/Procédures/Radio/", "Procédures/Radio"),
                                       (" A / B ", "A/B"), ("A/B/C", "A/B/C")])
def test_valid_paths(raw, path):
    assert folders.clean_path(raw) == path


@pytest.mark.parametrize("raw, detail", [("A/B/C/D", "folder_too_deep"), ("A//B", "invalid_folder"),
                                         ("..", "invalid_folder"), ("A/./B", "invalid_folder"),
                                         ("a\\b", "invalid_folder"), ("x" * 61, "invalid_folder"),
                                         ("tab\there", "invalid_folder"), (12, "invalid_folder")])
def test_invalid_paths(raw, detail):
    with pytest.raises(folders.FolderError) as exc:
        folders.clean_path(raw)
    assert exc.value.status == 400 and exc.value.detail == detail


def test_event_folders_lifecycle(client, db):
    base = "/api/v0.1/events/e1/files"
    r = client.post(f"{base}/folders", json={"path": "Procédures/Radio", "actor_id": "PCO"})
    assert r.status_code == 201 and r.json()["folders"] == ["Procédures", "Procédures/Radio"]
    assert client.post(f"{base}/folders", json={"path": "Procédures"}).status_code == 409
    assert client.post(f"{base}/folders", json={"path": "A/B/C/D"}).status_code == 400

    up = client.post(base, data={"sender_id": "TEL-01", "folder": "Procédures/Radio"},
                     files={"file": ("plan.pdf", b"%PDF", "application/pdf")})
    assert up.status_code == 201 and up.json()["folder"] == "Procédures/Radio"
    fid = up.json()["id"]
    up2 = client.post(f"{base}?folder=Cartes", data={"sender_id": "TEL-01"}, files={"file": ("c.png", b"png")})
    assert up2.json()["folder"] == "Cartes"
    assert client.get(base).json()[0]["folder"] == "Procédures/Radio"          # list kept for older apps
    listing = client.get(f"{base}?with_folders=true").json()
    assert listing["folders"] == ["Cartes", "Procédures", "Procédures/Radio"] and len(listing["files"]) == 2

    # Not empty: refused; rename moves files and subfolders.
    assert client.delete(f"{base}/folders", params={"path": "Procédures"}).status_code == 409
    assert client.patch(f"{base}/folders", json={"from": "Procédures", "to": "Cartes"}).status_code == 409
    assert client.patch(f"{base}/folders", json={"from": "Procédures", "to": "Procédures/X"}).status_code == 409
    assert client.patch(f"{base}/folders", json={"from": "Procédures", "to": "A/B/C"}).status_code == 400
    r = client.patch(f"{base}/folders", json={"from": "Procédures", "to": "Docs/Procédures"})
    assert r.status_code == 200
    assert r.json()["folders"] == ["Cartes", "Docs", "Docs/Procédures", "Docs/Procédures/Radio"]
    assert db.get(SharedFileRow, fid).folder == "Docs/Procédures/Radio"

    # Move and rename a file, then delete the emptied folders.
    r = client.patch(f"{base}/{fid}", json={"folder": "", "name": "Plan radio.pdf", "actor_id": "PCO"})
    assert r.status_code == 200 and r.json()["folder"] == "" and r.json()["name"] == "Plan radio.pdf"
    assert client.patch(f"{base}/{fid}", json={"folder": "a/../b"}).status_code == 400
    assert client.patch(f"{base}/{fid}", json={"name": "x/y"}).status_code == 400
    assert client.patch(f"{base}/nope", json={"name": "x"}).status_code == 404
    assert client.delete(f"{base}/folders", params={"path": "Docs"}).status_code == 409
    assert client.delete(f"{base}/folders", params={"path": "Docs/Procédures/Radio"}).status_code == 204
    assert client.delete(f"{base}/folders", params={"path": "Docs/Procédures/Radio"}).status_code == 404
    assert client.get(f"{base}/folders").json()["folders"] == ["Cartes", "Docs", "Docs/Procédures"]
    assert client.get("/api/v0.1/events/nope/files/folders").status_code == 404

    log = [r.summary for r in db.scalars(select(LogbookRow).where(LogbookRow.kind == "file").order_by(LogbookRow.seq))]
    assert "Dossier créé : Procédures/Radio" in log
    assert "Dossier renommé : Procédures → Docs/Procédures" in log
    assert "Fichier renommé : plan.pdf → Plan radio.pdf" in log
    assert "Fichier déplacé : Plan radio.pdf → racine" in log
    assert "Dossier supprimé : Docs/Procédures/Radio" in log


def test_personal_folders_owner_only(client, db):
    r = client.post("/api/v0.1/me/files/folders", json={"path": "Radio"})
    assert r.status_code == 201 and r.json()["folders"] == ["Radio"]
    up = client.post("/api/v0.1/me/files", data={"folder": "Radio"}, files={"file": ("p.txt", b"x")})
    assert up.status_code == 201 and up.json()["folder"] == "Radio"
    listing = client.get("/api/v0.1/me/files").json()
    assert listing["folders"] == ["Radio"] and listing["files"][0]["folder"] == "Radio"
    fid = up.json()["id"]
    assert client.patch("/api/v0.1/me/files/folders", json={"from": "Radio", "to": "Plans"}).json()["folders"] == ["Plans"]
    assert client.patch(f"/api/v0.1/me/files/{fid}", json={"folder": "", "name": "q.txt"}).json()["folder"] == ""
    assert client.delete("/api/v0.1/me/files/folders", params={"path": "Plans"}).status_code == 204
    # Someone else's folders are not visible.
    db.add(FileFolderRow(scope="personal", owner="b@x.fr", path="Secret", created_at=datetime.now(UTC)))
    db.commit()
    assert client.get("/api/v0.1/me/files/folders").json()["folders"] == []
    assert client.delete("/api/v0.1/me/files/folders", params={"path": "Secret"}).status_code == 404
    # No personal folder written in the logbook.
    assert db.scalars(select(LogbookRow)).all() == []

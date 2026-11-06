# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Profile photo in place of the initials (lot 29, group 7)."""
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.accounts.caller import Caller
from sarcade.api import app as app_module
from sarcade.api import photos as photos_api
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import DevicePhotoRow, EventRow, SecurityEventRow
from sarcade.profile import photo

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 100


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (EventRow, DevicePhotoRow, SecurityEventRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active",
                         created_at=datetime(2026, 10, 29, tzinfo=UTC), version=0))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def sent(db, monkeypatch):
    out = []

    async def _keep(event_id, payload, **k):
        out.append(payload)
    monkeypatch.setattr(app_module.manager, "broadcast", _keep)
    return out


@pytest.fixture
def client(db, sent, monkeypatch):
    monkeypatch.setattr(photos_api.journal, "record", lambda *a, **k: None)
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _as(monkeypatch, caller):
    monkeypatch.setattr(photos_api, "caller_of", lambda request: caller)


URL = "/api/v0.1/events/e1/devices/TEL-01/photo"


def test_type_read_from_bytes():
    assert photo.mime_of(JPEG) == "image/jpeg"
    assert photo.mime_of(PNG) == "image/png"
    assert photo.mime_of(WEBP) == "image/webp"
    assert photo.mime_of(b"GIF89a....") is None


def test_own_photo_set_read_listed_and_announced(client, sent, monkeypatch):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    r = client.put(URL, content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200
    got = client.get(URL)
    assert got.content == JPEG and got.headers["content-type"] == "image/jpeg"
    assert [p["device_id"] for p in client.get("/api/v0.1/events/e1/photos").json()] == ["TEL-01"]
    assert sent[-1]["type"] == "photo.updated" and sent[-1]["data"]["device_id"] == "TEL-01"
    assert "removed_by" not in sent[-1]["data"]


def test_replacing_keeps_one_photo(client, monkeypatch, db):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    client.put(URL, content=JPEG)
    client.put(URL, content=PNG)
    assert client.get(URL).content == PNG
    assert len(client.get("/api/v0.1/events/e1/photos").json()) == 1


def test_too_heavy_or_wrong_type_refused(client, monkeypatch):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    assert client.put(URL, content=b"\xff\xd8\xff" + b"0" * (64 * 1024)).status_code == 413
    assert client.put(URL, content=b"GIF89a" + b"0" * 50).status_code == 415
    assert client.put(URL, content=b"").status_code == 413


def test_someone_else_cannot_set_my_photo(client, monkeypatch):
    _as(monkeypatch, Caller(email="b@x.fr", via="device", device_id="TEL-02"))
    assert client.put(URL, content=JPEG).status_code == 403


def test_unknown_event_or_photo(client, monkeypatch):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    assert client.put("/api/v0.1/events/zz/devices/TEL-01/photo", content=JPEG).status_code == 404
    assert client.get(URL).status_code == 404
    assert client.delete(URL).status_code == 404


def test_operator_removes_own_photo(client, sent, monkeypatch):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    client.put(URL, content=JPEG)
    assert client.delete(URL).status_code == 200
    assert client.get(URL).status_code == 404
    assert sent[-1]["data"] == {"device_id": "TEL-01", "updated_at": None}


def test_pco_removes_someone_else_photo_and_operator_is_told(client, sent, monkeypatch):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    client.put(URL, content=JPEG)
    _as(monkeypatch, Caller(email="pco@x.fr", via="device", device_id="PCO-01"))
    monkeypatch.setattr(photos_api.roles_api, "require_pco", lambda request, db: "pco@x.fr")
    assert client.delete(URL).status_code == 200
    assert sent[-1]["data"]["removed_by"] == "pco"


def test_non_pco_cannot_remove_someone_else_photo(client, monkeypatch):
    _as(monkeypatch, Caller(email="a@x.fr", via="device", device_id="TEL-01"))
    client.put(URL, content=JPEG)
    _as(monkeypatch, Caller(email="b@x.fr", via="device", device_id="TEL-02"))

    def _refuse(request, db):
        raise HTTPException(403, "réservé au PCO")
    monkeypatch.setattr(photos_api.roles_api, "require_pco", _refuse)
    assert client.delete(URL).status_code == 403
    assert client.get(URL).status_code == 200

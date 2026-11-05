# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Manual notes of the Main courante (lot 29.5)."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import app as app_module
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import EventRow, LogbookRow


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[EventRow.__table__, LogbookRow.__table__])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active",
                         created_at=datetime(2026, 11, 5, tzinfo=UTC), version=0))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db, monkeypatch):
    async def _quiet(*a, **k):
        return None
    monkeypatch.setattr(app_module.manager, "broadcast", _quiet)
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_note_is_added_to_the_logbook(client):
    r = client.post("/api/v0.1/events/e1/logbook/notes", json={"actor_id": "PCO", "text": " Équipe 2 au point haut ",
                                                               "lat": 48.8, "lon": 2.13})
    assert r.status_code == 201
    assert r.json()["kind"] == "note"
    assert r.json()["summary"] == "Équipe 2 au point haut (48.80000, 2.13000)"
    rows = client.get("/api/v0.1/events/e1/logbook").json()
    assert [(x["kind"], x["actor_id"]) for x in rows] == [("note", "PCO")]


def test_note_without_position(client):
    r = client.post("/api/v0.1/events/e1/logbook/notes", json={"actor_id": "TEL-01", "text": "RAS"})
    assert r.status_code == 201 and r.json()["summary"] == "RAS"


def test_empty_note_or_unknown_event(client):
    assert client.post("/api/v0.1/events/e1/logbook/notes", json={"actor_id": "PCO", "text": "   "}).status_code == 422
    assert client.post("/api/v0.1/events/e1/logbook/notes", json={"actor_id": "PCO", "text": ""}).status_code == 422
    assert client.post("/api/v0.1/events/zz/logbook/notes", json={"actor_id": "PCO", "text": "x"}).status_code == 404

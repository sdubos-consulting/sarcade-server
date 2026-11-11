# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Export CSV of the Main courante in the common format (lot 29, group 8)."""
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


def test_csv_has_the_common_columns_and_each_fingerprint(client, monkeypatch):
    from sarcade.security import event_format, journal
    monkeypatch.setattr(journal, "server_id", lambda: "srv-test")
    client.post("/api/v0.1/events/e1/logbook/notes", json={"actor_id": "PCO", "text": "SOS ; équipe 2", "lat": 48.8, "lon": 2.13})
    client.post("/api/v0.1/events/e1/logbook/notes", json={"actor_id": "TEL-01", "text": "RAS"})
    r = client.get("/api/v0.1/events/e1/logbook.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "main-courante-e1.csv" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    lines = text.split("\r\n")
    assert lines[0].split(";") == event_format.CSV_COLUMNS
    import csv
    rows = list(csv.reader(lines[1:3], delimiter=";"))
    first = dict(zip(event_format.CSV_COLUMNS, rows[0]))
    assert first["event.dataset"] == "sarcade.logbook" and first["event.action"] == "note"
    assert first["user.id"] == "PCO" and first["geo.location.lat"] == "48.8"
    assert first["message"].startswith("SOS ; équipe 2")
    assert len(first["event.hash"]) == 64
    second = dict(zip(event_format.CSV_COLUMNS, rows[1]))
    assert second["geo.location.lat"] == "" and second["event.hash"] != first["event.hash"]


def test_csv_of_an_unknown_event(client):
    assert client.get("/api/v0.1/events/nope/logbook.csv").status_code == 404

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Mode of the probable zone of each searched signal (card « Plusieurs
zones : le découpage ne se fait pas quand un relevé est gardé à la main »,
points 8 and 11, 1 Nov 2026): kept on the server, set by any operator."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import operations
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import EventRow, LogbookRow


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (EventRow, LogbookRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Test NAS 1", kind="exercise", status="active", created_at=T0, version=0,
                         settings={}))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def broadcast(event_id, msg):
        out.append((event_id, msg))
    monkeypatch.setattr(operations.manager, "broadcast", broadcast)
    return out


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


URL = "/api/v0.1/events/e1/zone-mode"


def test_set_and_reset_the_mode_of_a_signal(client, db, sent):
    r = client.put(URL, json={"actor_id": "op1", "search": "121,375 MHz · Balise d’exercice", "mode": "multi"})
    assert r.status_code == 200
    assert r.json()["settings"]["zone_modes"] == {"121,375 mhz · balise d'exercice": "multi"}
    assert sent[-1][1]["type"] == "event.settings.updated"
    assert sent[-1][1]["data"]["settings"]["zone_modes"] == r.json()["settings"]["zone_modes"]
    summaries = [x.summary for x in db.scalars(select(LogbookRow))]
    assert summaries == ["Zone probable « 121,375 mhz · balise d'exercice » : Plusieurs balises"]
    # Same signal, another apostrophe: same key.
    r = client.put(URL, json={"actor_id": "op2", "search": "121,375 MHz · Balise d'exercice", "mode": "single"})
    assert r.json()["settings"]["zone_modes"] == {"121,375 mhz · balise d'exercice": "single"}
    r = client.put(URL, json={"actor_id": "op2", "search": "121,375 MHz · Balise d'exercice", "mode": "auto"})
    assert r.json()["settings"]["zone_modes"] == {}
    assert client.get("/api/v0.1/events/e1").json()["settings"]["zone_modes"] == {}


def test_nothing_changed_nothing_sent(client, sent):
    client.put(URL, json={"actor_id": "op1", "search": "S", "mode": "auto"})
    assert sent == []


def test_wrong_mode_or_closed_event(client, db, sent):
    assert client.put(URL, json={"actor_id": "op1", "search": "S", "mode": "two"}).status_code == 422
    db.get(EventRow, "e1").ended_at = T0
    db.commit()
    assert client.put(URL, json={"actor_id": "op1", "search": "S", "mode": "multi"}).status_code == 409
    assert client.put("/api/v0.1/events/nope/zone-mode",
                      json={"actor_id": "op1", "search": "S", "mode": "multi"}).status_code == 404


def test_no_pco_needed(client, sent):
    """Any operator of the event: refused PCO rights do not stop it, while
    the settings of the PCO stay refused."""
    from fastapi import HTTPException
    from sarcade.api.roles import require_pco

    def refused():
        raise HTTPException(status_code=403, detail="pco_required")
    app.dependency_overrides[require_pco] = refused
    try:
        assert client.put(URL, json={"actor_id": "op1", "search": "S", "mode": "multi"}).status_code == 200
        assert client.patch("/api/v0.1/events/e1/settings",
                            json={"actor_id": "op1", "settings": {"zone_modes": {}}}).status_code == 403
    finally:
        app.dependency_overrides.pop(require_pco, None)

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Status of the operator in one touch (card « Android Auto : statut en un
appui », lot 24, 3 Nov 2026): en_route, sur_place, disponible, pause or
none, kept per device with its time, sent by REST or by the sync, broadcast
as ``operator.status``."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import operations
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.api.serializers import with_operator_status
from sarcade.db.base import Base
from sarcade.db.models import DeviceRow, EventRow, LogbookRow, SyncOperationRow
from sarcade.events import operator_status as st
from sarcade.sync.service import VALID_TYPES


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


# --- Pure rules -------------------------------------------------------------------


def test_known_statuses_only():
    assert st.STATUSES == ("en_route", "sur_place", "disponible", "pause")
    assert st.clean("pause") == "pause"
    assert st.clean(None) is None and st.clean("") is None
    for bad in ("busy", 3, "En route"):
        with pytest.raises(st.InvalidStatus):
            st.clean(bad)


def test_time_of_the_device_future_brought_back():
    assert st.parse_time(None, T0) == T0
    assert st.parse_time("2026-11-03T07:59:00Z", T0) == T0 - timedelta(minutes=1)
    assert st.parse_time("2026-11-03T09:00:00Z", T0) == T0, "an hour ahead: server time"
    with pytest.raises(st.InvalidStatus):
        st.parse_time("hier", T0)


def test_newer_wins_and_same_status_keeps_its_time():
    d = SimpleNamespace(event_id="e1", device_id="CAR", operator_status=None, operator_status_at=None)
    assert st.apply(d, "en_route", T0)
    assert not st.apply(d, "en_route", T0 + timedelta(minutes=1)), "same status: unchanged"
    assert d.operator_status_at == T0
    assert not st.apply(d, "pause", T0 - timedelta(seconds=1)), "queued offline, older: ignored"
    assert st.apply(d, "sur_place", T0 + timedelta(minutes=5))
    assert st.apply(d, None, T0 + timedelta(minutes=6))
    assert st.state(d) == {"event_id": "e1", "device_id": "CAR", "operator_status": None,
                           "operator_status_at": "2026-11-03T08:06:00Z"}


def test_logbook_lines():
    assert st.logbook_summary("F4ABC", "en_route") == "Statut de F4ABC : En route"
    assert st.logbook_summary("F4ABC", None) == "Statut de F4ABC retiré"


def test_synchronised_type():
    assert "operator_status" in VALID_TYPES


def test_latest_positions_carry_the_status(db):
    db.add(DeviceRow(event_id="e1", device_id="CAR", operator_status="sur_place", operator_status_at=T0))
    db.commit()
    out = with_operator_status(db, "e1", [{"device_id": "CAR"}, {"device_id": "OTHER"}])
    assert out[0]["operator_status"] == "sur_place" and out[0]["operator_status_at"] == "2026-11-03T08:00:00Z"
    assert out[1]["operator_status"] is None and out[1]["operator_status_at"] is None


# --- API ------------------------------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (EventRow, LogbookRow, DeviceRow,
                                                                    SyncOperationRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active", created_at=T0, version=0,
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


URL = "/api/v0.1/events/e1/devices/CAR-1/status"


def _lines(db):
    return [x.summary for x in db.scalars(select(LogbookRow).where(LogbookRow.kind == "operator_status")
                                          .order_by(LogbookRow.seq))]


def test_put_status_broadcast_and_logbook(client, db, sent):
    r = client.put(URL, json={"status": "en_route"})
    assert r.status_code == 200
    body = r.json()
    assert body["operator_status"] == "en_route" and body["changed"] is True and body["device_id"] == "CAR-1"
    assert sent[-1] == ("e1", {"type": "operator.status", "data": {k: v for k, v in body.items() if k != "changed"}})
    # Same status again: nothing sent, nothing logged.
    n = len(sent)
    assert client.put(URL, json={"status": "en_route"}).json()["changed"] is False
    assert len(sent) == n
    # Listed, and in the devices of the PCO.
    assert client.get("/api/v0.1/events/e1/operator-statuses").json()[0]["operator_status"] == "en_route"
    row = db.get(DeviceRow, ("e1", "CAR-1"))
    dev = operations.device_dict(row, {}, row.last_contact_at)
    assert dev["operator_status"] == "en_route" and dev["operator_status_at"]
    assert dev["status"] == "ok", "contact status unchanged"
    # Removed.
    assert client.put(URL, json={"status": None}).json()["operator_status"] is None
    assert client.get("/api/v0.1/events/e1/operator-statuses").json() == []
    assert _lines(db) == ["Statut de CAR-1 : En route", "Statut de CAR-1 retiré"]


def test_older_status_sent_late_is_ignored(client, sent):
    now = datetime.now(UTC)
    client.put(URL, json={"status": "sur_place", "at": now.isoformat()})
    r = client.put(URL, json={"status": "en_route", "at": (now - timedelta(minutes=3)).isoformat()})
    assert r.json()["changed"] is False and r.json()["operator_status"] == "sur_place"


def test_label_of_the_device_in_the_logbook(client, db, sent):
    db.add(DeviceRow(event_id="e1", device_id="CAR-1", label="Voiture F4ABC"))
    db.commit()
    client.put(URL, json={"status": "pause"})
    assert _lines(db) == ["Statut de Voiture F4ABC : Pause"]


def test_invalid_status_or_event(client, sent):
    assert client.put(URL, json={"status": "busy"}).status_code == 422
    assert client.put(URL, json={"status": "pause", "at": "hier"}).status_code == 422
    assert client.put("/api/v0.1/events/nope/devices/CAR-1/status", json={"status": "pause"}).status_code == 404
    assert client.get("/api/v0.1/events/nope/operator-statuses").status_code == 404
    assert sent == []


def _op(op_id, status, at, device="CAR-1", **kw):
    p = {"operation_id": op_id, "event_id": "e1", "object_id": device, "object_type": "operator_status",
         "action": "update", "client_time": at, "payload": {"device_id": device, "status": status, "at": at}}
    p.update(kw)
    return p


def test_status_through_the_sync(client, db, sent):
    now = datetime.now(UTC)
    t1, t0 = now.isoformat(), (now - timedelta(minutes=2)).isoformat()
    r = client.post("/api/v0.1/sync", json=[_op("op-1", "en_route", t1)])
    assert r.json()[0]["status"] == "accepted"
    assert sent[-1][1]["type"] == "operator.status" and sent[-1][1]["data"]["operator_status"] == "en_route"
    # Same operation again: duplicate; an older one: conflict.
    assert client.post("/api/v0.1/sync", json=[_op("op-1", "en_route", t1)]).json()[0]["status"] == "duplicate"
    assert client.post("/api/v0.1/sync", json=[_op("op-0", "pause", t0)]).json()[0]["status"] == "conflict"
    # The change feed carries the resulting state.
    feed = client.get("/api/v0.1/events/e1/sync/changes").json()["changes"]
    assert [(c["object_type"], c["payload"]["operator_status"]) for c in feed] == [("operator_status", "en_route")]
    # Malformed: rejected.
    bad = [_op("op-2", "busy", t1), _op("op-3", "pause", t1, object_id="OTHER"),
           _op("op-4", "pause", t1, action="create")]
    assert [x["status"] for x in client.post("/api/v0.1/sync", json=bad).json()] == ["rejected"] * 3

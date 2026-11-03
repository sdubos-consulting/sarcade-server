# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Estimated arrival time of a move order (card « Android Auto : heure
d'arrivée estimée envoyée au PCO », lot 24, 3 Nov 2026): set by the
targeted operator while the order is accepted, broadcast with the order,
cleared at the arrival or the cancellation."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, event, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import app as app_module
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import (
    AckRow,
    CommGroupRow,
    DeviceRow,
    EventRow,
    LogbookRow,
    MessageRow,
    RouteObjectRow,
    SecurityEventRow,
    ServerSettingRow,
    SyncOperationRow,
)
from sarcade.features.service import InvalidFeature
from sarcade.routes import service as r

E, OID = "evt-1", "order-1"
NOW = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


# --- Pure rules -------------------------------------------------------------------


def test_eta_bounds():
    assert r.clean_eta(None, NOW) is None
    assert r.clean_eta("2026-11-03T08:15:00Z", NOW) == NOW + timedelta(minutes=15)
    for bad in ("2026-11-03T06:30:00Z", "2026-11-06T08:00:00Z", "bientôt", 12):
        with pytest.raises(InvalidFeature):
            r.clean_eta(bad, NOW)
    assert r.clean_remaining(None) is None and r.clean_remaining(1234.56) == 1234.6
    for bad in (-1, True, float("nan"), 3_000_000):
        with pytest.raises(InvalidFeature):
            r.clean_remaining(bad)


def test_only_the_target_of_an_accepted_order():
    order = {"target_id": "CAR", "status": "accepted"}
    assert r.eta_allowed(order, "CAR", pco=False)
    assert r.eta_allowed(order, "PHONE", pco=False, same_person_as_target=True)
    assert r.eta_allowed(order, "PCO", pco=True)
    assert not r.eta_allowed(order, "OTHER", pco=False)
    assert not r.eta_allowed(dict(order, status="sent"), "CAR", pco=False)
    assert not r.eta_allowed(dict(order, status="arrived"), "CAR", pco=False)


def test_kept_while_accepted_cleared_otherwise():
    data = r.with_eta({"status": "accepted", "note": ""}, NOW + timedelta(minutes=15), "CAR", NOW, 4200.0)
    assert data["eta"] == "2026-11-03T08:15:00Z" and data["eta_updated_at"] == "2026-11-03T08:00:00Z"
    assert data["eta_by"] == "CAR" and data["eta_remaining_m"] == 4200.0
    # A change of the note by the PCO keeps it; the synchronised order never sets it.
    kept = r.keep_eta(data, {"status": "accepted", "note": "Prendre la Yagi", "eta": "2030-01-01T00:00:00Z"})
    assert kept["eta"] == data["eta"] and kept["note"] == "Prendre la Yagi"
    for status in ("arrived", "cancelled", "refused"):
        assert "eta" not in r.keep_eta(data, {"status": status}), status
    assert "eta" not in r.keep_eta(None, {"status": "accepted", "eta": "2026-11-03T08:15:00Z"})
    assert "eta" not in r.with_eta(data, None, "CAR", NOW)


# --- API ------------------------------------------------------------------------


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


def _aware(target, *a):
    """SQLite gives back naive times: aware again, as with PostgreSQL."""
    if target.updated_at is not None and target.updated_at.tzinfo is None:
        target.updated_at = target.updated_at.replace(tzinfo=UTC)


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, LogbookRow, MessageRow, AckRow, SyncOperationRow, RouteObjectRow, DeviceRow, CommGroupRow,
        SecurityEventRow, ServerSettingRow)])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    db.add(EventRow(id=E, name="Exercice", kind="exercise", status="active", created_at=NOW, version=0))
    db.add(DeviceRow(event_id=E, device_id="CAR", callsign="F4ABC-9"))
    db.add(DeviceRow(event_id=E, device_id="PHONE", callsign="F4ABC"))
    db.add(DeviceRow(event_id=E, device_id="OTHER", callsign="F4XYZ"))
    db.commit()
    event.listen(RouteObjectRow, "load", _aware)
    event.listen(RouteObjectRow, "refresh", _aware)
    sent = []

    async def _broadcast(event_id, msg):
        sent.append(msg)

    async def _quiet(*a, **k):
        return None

    mp = pytest.MonkeyPatch()
    mp.setattr(app_module.manager, "broadcast", _broadcast)
    mp.setattr(app_module.wakeup_service, "wake_for_message", _quiet)
    mp.setattr(app_module.security_journal, "record", lambda *a, **k: None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app), db, sent
    finally:
        app.dependency_overrides.pop(get_db, None)
        mp.undo()
        event.remove(RouteObjectRow, "load", _aware)
        event.remove(RouteObjectRow, "refresh", _aware)
        db.close()


def _iso(t):
    return t.isoformat().replace("+00:00", "Z")


def _sync(client, op_id, actor, status, at, action="update", **kw):
    p = {"id": OID, "event_id": E, "target_id": "CAR", "status": status,
         "destination": {"lat": 48.78, "lon": 2.02, "label": "Point haut"}, "note": "",
         "created_by": "PCO", "updated_by": actor, "updated_at": _iso(at)}
    p.update(kw)
    op = {"operation_id": op_id, "event_id": E, "object_id": OID, "object_type": "move_order",
          "action": action, "client_time": _iso(at), "payload": p}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]["status"]


URL = f"/api/v0.1/events/{E}/move-orders/{OID}/eta"


def test_eta_of_an_accepted_order(env):
    client, db, sent = env
    t = datetime.now(UTC)
    eta = _iso(t + timedelta(minutes=15))
    assert _sync(client, "op-1", "PCO", "sent", t, action="create") == "accepted"
    assert client.put(URL, json={"device_id": "CAR", "eta": eta}).status_code == 409, "not accepted yet"
    assert _sync(client, "op-2", "CAR", "accepted", t + timedelta(seconds=1)) == "accepted"
    updated_at = db.get(RouteObjectRow, OID).updated_at

    res = client.put(URL, json={"device_id": "CAR", "eta": eta, "remaining_m": 8000})
    assert res.status_code == 200
    body = res.json()
    assert body["eta"] and body["eta_by"] == "CAR" and body["eta_remaining_m"] == 8000.0 and body["status"] == "accepted"
    assert sent[-1] == {"type": "move_order.upserted", "data": body}
    assert db.get(RouteObjectRow, OID).updated_at == updated_at, "the answers keep their order"
    # In the list of orders and the change feed.
    assert client.get(f"/api/v0.1/events/{E}/move-orders").json()[0]["eta"] == body["eta"]
    feed = client.get(f"/api/v0.1/events/{E}/sync/changes").json()["changes"]
    assert feed[-1]["payload"]["eta"] == body["eta"]
    # Another terminal of the same person; not someone else.
    assert client.put(URL, json={"device_id": "PHONE", "eta": eta}).status_code == 200
    assert client.put(URL, json={"device_id": "OTHER", "eta": eta}).status_code == 403
    assert client.put(URL, json={"device_id": "CAR", "eta": "demain"}).status_code == 422
    assert client.put(f"/api/v0.1/events/{E}/move-orders/nope/eta",
                      json={"device_id": "CAR", "eta": eta}).status_code == 404
    # Sent again unchanged: fresher eta_updated_at, broadcast again. No
    # logbook line for an estimate.
    n = len(sent)
    client.put(URL, json={"device_id": "PHONE", "eta": eta})
    assert len(sent) == n + 1 and sent[-1]["data"]["eta_by"] == "PHONE"
    assert not [x for x in db.scalars(select(LogbookRow)) if "estim" in x.summary]

    # Arrived: the estimate is gone.
    assert _sync(client, "op-3", "CAR", "arrived", t + timedelta(seconds=2)) == "accepted"
    assert "eta" not in db.get(RouteObjectRow, OID).data
    assert client.put(URL, json={"device_id": "CAR", "eta": eta}).status_code == 409
    assert client.put(URL, json={"device_id": "CAR", "eta": None}).status_code == 200, "clearing: nothing to do"


def test_cleared_by_the_cancellation_and_by_the_operator(env):
    client, db, sent = env
    t = datetime.now(UTC)
    eta = _iso(t + timedelta(minutes=20))
    _sync(client, "op-1", "PCO", "sent", t, action="create")
    _sync(client, "op-2", "CAR", "accepted", t + timedelta(seconds=1))
    client.put(URL, json={"device_id": "CAR", "eta": eta})
    # Cleared by the operator (navigation stopped).
    assert "eta" not in client.put(URL, json={"device_id": "CAR", "eta": None}).json()
    client.put(URL, json={"device_id": "CAR", "eta": eta})
    # The PCO changes the note: kept. Then cancels: cleared.
    assert _sync(client, "op-3", "PCO", "accepted", t + timedelta(seconds=2), note="Yagi") == "accepted"
    assert db.get(RouteObjectRow, OID).data["eta"]
    assert _sync(client, "op-4", "PCO", "cancelled", t + timedelta(seconds=3)) == "accepted"
    assert "eta" not in db.get(RouteObjectRow, OID).data

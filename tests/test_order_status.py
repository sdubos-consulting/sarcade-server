# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Lot 27: the status of the operator follows his move orders (card
« Ordres de déplacement : statut mis à jour automatiquement ») and is
« Disponible » by default (card « Statut de l'opérateur : « Disponible »
par défaut »)."""
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
    ClientDeviceRow,
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
from sarcade.events import order_status as o
from sarcade.routes import service as r

E, OID = "evt-1", "order-1"
NOW = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


# --- Pure rules -------------------------------------------------------------------


def test_transitions():
    sent, acc, arr = {"status": "sent"}, {"status": "accepted"}, {"status": "arrived"}
    assert o.transition(None, sent) is None, "creation: no status"
    assert o.transition(sent, acc) == "accepted"
    assert o.transition(acc, arr) == "arrived"
    assert o.transition(sent, arr) == "arrived"
    assert o.transition(acc, {"status": "refused"}) == "refused"
    assert o.transition(acc, {"status": "cancelled"}) == "cancelled"
    assert o.transition(acc, dict(acc, note="Yagi")) is None, "a note of the PCO: no status"
    assert o.transition(arr, dict(arr, finished_at="2026-11-03T09:00:00Z")) == "finished"
    done = dict(arr, finished_at="2026-11-03T09:00:00Z")
    assert o.transition(done, dict(done)) is None, "finished once"


def test_target_status():
    assert o.target_status("accepted", {}, "CAR") == (True, "en_route")
    assert o.target_status("arrived", {}, "CAR") == (True, "sur_place")
    assert o.target_status("finished", {}, "CAR") == (True, "disponible")
    assert o.target_status("cancelled", {"CAR": "pause"}, "CAR") == (True, "pause")
    assert o.target_status("refused", {"CAR": None}, "CAR") == (True, None)
    assert o.target_status("refused", {}, "CAR") == (False, None), "never accepted: nothing to restore"


def test_kept_fields():
    before = {"status": "arrived", "operator_status_before": {"CAR": "pause"}, "finished_at": "2026-11-03T09:00:00Z"}
    # The client sends the order back without the fields of the server.
    kept = o.keep_fields(before, {"status": "arrived", "operator_status_before": {"CAR": "en_route"}})
    assert kept == before
    assert "finished_at" not in o.keep_fields(before, {"status": "cancelled"})


def test_finished_only_on_an_arrived_order():
    p = {"id": OID, "event_id": E, "target_id": "CAR", "destination": {"lat": 48.78, "lon": 2.02},
         "created_by": "PCO", "updated_by": "CAR", "updated_at": "2026-11-03T08:00:00Z",
         "finished_at": "2026-11-03T09:00:00+00:00"}
    assert r.validate_move_order(dict(p, status="arrived"), event_id=E, object_id=OID)["finished_at"] == \
        "2026-11-03T09:00:00Z"
    assert "finished_at" not in r.validate_move_order(dict(p, status="accepted"), event_id=E, object_id=OID)


def test_logbook_reason():
    from sarcade.events.operator_status import logbook_summary
    assert logbook_summary("F4ABC", "en_route", "ordre accepté") == "Statut de F4ABC : En route, ordre accepté"
    assert logbook_summary("F4ABC", None, "ordre annulé") == "Statut de F4ABC retiré, ordre annulé"


# --- API ------------------------------------------------------------------------


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


def _aware(target, *a):
    if target.updated_at is not None and target.updated_at.tzinfo is None:
        target.updated_at = target.updated_at.replace(tzinfo=UTC)


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, LogbookRow, MessageRow, AckRow, SyncOperationRow, RouteObjectRow, DeviceRow, CommGroupRow,
        SecurityEventRow, ServerSettingRow, ClientDeviceRow)])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    db.add(EventRow(id=E, name="Exercice", kind="exercise", status="active", created_at=NOW, version=0, settings={}))
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


def _sync(client, op_id, actor, status, at, action="update", oid=OID, **kw):
    p = {"id": oid, "event_id": E, "target_id": "CAR", "status": status,
         "destination": {"lat": 48.78, "lon": 2.02, "label": "Point haut"}, "note": "",
         "created_by": "PCO", "updated_by": actor, "updated_at": _iso(at)}
    p.update(kw)
    op = {"operation_id": op_id, "event_id": E, "object_id": oid, "object_type": "move_order",
          "action": action, "client_time": _iso(at), "payload": p}
    return client.post("/api/v0.1/sync", json=[op]).json()[0]["status"]


def _put_status(client, device, status, at):
    return client.put(f"/api/v0.1/events/{E}/devices/{device}/status", json={"status": status, "at": _iso(at)})


def _status(db, device):
    db.expire_all()
    return db.get(DeviceRow, (E, device)).operator_status


def _lines(db):
    return [x.summary for x in db.scalars(select(LogbookRow).where(LogbookRow.kind == "operator_status")
                                          .order_by(LogbookRow.seq))]


def test_accept_arrive_finish(env):
    client, db, sent = env
    t = datetime.now(UTC) - timedelta(minutes=5)
    _put_status(client, "CAR", "disponible", t)
    assert _sync(client, "op-1", "PCO", "sent", t + timedelta(seconds=1), action="create") == "accepted"
    assert _status(db, "CAR") == "disponible", "an order sent changes nothing"
    # 1. Accepted → En route, with the reason in the logbook and realtime.
    assert _sync(client, "op-2", "CAR", "accepted", t + timedelta(seconds=2)) == "accepted"
    assert _status(db, "CAR") == "en_route"
    assert _lines(db)[-1] == "Statut de F4ABC-9 : En route, ordre accepté"
    st = [m for m in sent if m["type"] == "operator.status"][-1]["data"]
    assert st["device_id"] == "CAR" and st["operator_status"] == "en_route"
    assert db.get(RouteObjectRow, OID).data["operator_status_before"] == {"CAR": "disponible"}
    # In the change feed, for the clients that were offline.
    feed = client.get(f"/api/v0.1/events/{E}/sync/changes").json()["changes"]
    assert any(c["object_type"] == "operator_status" and c["payload"]["operator_status"] == "en_route" for c in feed)
    # 2. Arrived → Sur place.
    assert _sync(client, "op-3", "CAR", "arrived", t + timedelta(seconds=3)) == "accepted"
    assert _status(db, "CAR") == "sur_place"
    assert _lines(db)[-1] == "Statut de F4ABC-9 : Sur place, arrivée sur zone"
    # 4. « Terminé, disponible » → Disponible; the order stays arrived.
    order = client.get(f"/api/v0.1/events/{E}/move-orders").json()[0]
    assert _sync(client, "op-4", "CAR", "arrived", t + timedelta(seconds=4),
                 finished_at=_iso(t + timedelta(seconds=4))) == "accepted"
    assert _status(db, "CAR") == "disponible"
    assert _lines(db)[-1] == "Statut de F4ABC-9 : Disponible, mission terminée"
    data = db.get(RouteObjectRow, OID).data
    assert data["status"] == "arrived" and data["finished_at"] and "operator_status_before" not in data
    # Sent back by an older client without the field: still finished, no new line.
    n = len(_lines(db))
    assert _sync(client, "op-5", "PCO", "arrived", t + timedelta(seconds=5), note="Merci") == "accepted"
    assert db.get(RouteObjectRow, OID).data["finished_at"] and len(_lines(db)) == n
    assert order["status"] == "arrived"


def test_cancelled_or_refused_goes_back_to_the_status_before(env):
    client, db, sent = env
    t = datetime.now(UTC) - timedelta(minutes=5)
    _put_status(client, "CAR", "pause", t)
    _sync(client, "op-1", "PCO", "sent", t + timedelta(seconds=1), action="create")
    _sync(client, "op-2", "CAR", "accepted", t + timedelta(seconds=2))
    assert _status(db, "CAR") == "en_route"
    # 3. Cancelled by the PCO → the status before the order.
    assert _sync(client, "op-3", "PCO", "cancelled", t + timedelta(seconds=3)) == "accepted"
    assert _status(db, "CAR") == "pause"
    assert _lines(db)[-1] == "Statut de F4ABC-9 : Pause, ordre annulé"
    # A second order refused after being accepted: same rule; refused at
    # once: nothing changes.
    assert _sync(client, "op-4", "PCO", "sent", t + timedelta(seconds=4), oid="order-2", action="create") == "accepted"
    n = len(_lines(db))
    assert _sync(client, "op-5", "CAR", "refused", t + timedelta(seconds=5), oid="order-2") == "accepted"
    assert _status(db, "CAR") == "pause" and len(_lines(db)) == n


def test_never_had_a_status_goes_back_to_none(env):
    client, db, sent = env
    t = datetime.now(UTC) - timedelta(minutes=5)
    _sync(client, "op-1", "PCO", "sent", t, action="create")
    _sync(client, "op-2", "CAR", "accepted", t + timedelta(seconds=1))
    assert _status(db, "CAR") == "en_route"
    _sync(client, "op-3", "CAR", "refused", t + timedelta(seconds=2))
    assert _status(db, "CAR") is None
    assert _lines(db)[-1] == "Statut de F4ABC-9 retiré, ordre refusé"


def test_manual_change_kept_until_the_next_event_of_the_order(env):
    client, db, sent = env
    t = datetime.now(UTC) - timedelta(minutes=5)
    _sync(client, "op-1", "PCO", "sent", t, action="create")
    # The operator answers offline at t+1, then chooses « Pause » at t+2
    # online; the answer arrives later: « Pause » is kept.
    _put_status(client, "CAR", "pause", t + timedelta(seconds=2))
    assert _sync(client, "op-2", "CAR", "accepted", t + timedelta(seconds=1)) == "accepted"
    assert _status(db, "CAR") == "pause"
    # The PCO changes the note: not an event of the order for the status.
    assert _sync(client, "op-3", "PCO", "accepted", t + timedelta(seconds=3), note="Yagi") == "accepted"
    assert _status(db, "CAR") == "pause"
    # The next event (arrived) moves it again.
    assert _sync(client, "op-4", "CAR", "arrived", t + timedelta(seconds=4), note="Yagi") == "accepted"
    assert _status(db, "CAR") == "sur_place"


def test_answer_from_another_terminal_of_the_same_person(env):
    client, db, sent = env
    t = datetime.now(UTC) - timedelta(minutes=5)
    _sync(client, "op-1", "PCO", "sent", t, action="create")
    # The order went to CAR; the phone of the same person accepts it.
    assert _sync(client, "op-2", "PHONE", "accepted", t + timedelta(seconds=1)) == "accepted"
    assert _status(db, "CAR") == "en_route" and _status(db, "PHONE") == "en_route"
    assert _status(db, "OTHER") is None
    assert set(db.get(RouteObjectRow, OID).data["operator_status_before"]) == {"CAR", "PHONE"}
    _sync(client, "op-3", "PCO", "cancelled", t + timedelta(seconds=2))
    assert _status(db, "CAR") is None and _status(db, "PHONE") is None


# --- « Disponible » by default ------------------------------------------------------


def _hb(client, device, **kw):
    return client.post(f"/api/v0.1/events/{E}/devices/{device}/heartbeat", json=kw)


def test_new_operator_is_available_once(env):
    client, db, sent = env
    assert _hb(client, "TEL-1", label="Équipe 1", pending_count=0).status_code == 200
    assert _status(db, "TEL-1") == "disponible"
    assert _lines(db) == ["Statut de Équipe 1 : Disponible, par défaut"]
    assert any(m["type"] == "operator.status" and m["data"]["operator_status"] == "disponible" for m in sent)
    # « Aucun statut » chosen afterwards: never put back.
    _put_status(client, "TEL-1", None, datetime.now(UTC))
    _hb(client, "TEL-1", pending_count=0)
    assert _status(db, "TEL-1") is None and len(_lines(db)) == 2


def test_operator_in_pause_restarts_the_app(env):
    client, db, sent = env
    _put_status(client, "CAR", "pause", datetime.now(UTC) - timedelta(minutes=1))
    _hb(client, "CAR", pending_count=0)
    assert _status(db, "CAR") == "pause"


def test_waits_for_the_outbox(env):
    client, db, sent = env
    _hb(client, "TEL-1", pending_count=2)
    assert _status(db, "TEL-1") is None, "a status chosen offline may be on its way"
    # The queued status arrives (older than now), then the outbox is empty.
    _put_status(client, "TEL-1", "pause", datetime.now(UTC) - timedelta(minutes=2))
    _hb(client, "TEL-1", pending_count=0)
    assert _status(db, "TEL-1") == "pause"


def test_new_device_of_the_same_person_takes_his_status(env):
    client, db, sent = env
    _put_status(client, "CAR", "sur_place", datetime.now(UTC) - timedelta(minutes=1))
    _hb(client, "SURFACE", callsign="F4ABC-2", pending_count=0)
    assert _status(db, "SURFACE") == "sur_place"
    assert _lines(db)[-1] == "Statut de F4ABC-2 : Sur place, repris de F4ABC-9"
    # Someone else: « Disponible ».
    _hb(client, "TEL-9", callsign="F1AAA", pending_count=0)
    assert _status(db, "TEL-9") == "disponible"


def test_person_without_status_stays_without(env):
    client, db, sent = env
    # « Disponible » given to the car, then « Aucun statut » chosen there.
    _hb(client, "CAR", pending_count=0)
    _put_status(client, "CAR", None, datetime.now(UTC))
    assert _status(db, "CAR") is None
    _hb(client, "SURFACE", callsign="F4ABC", pending_count=0)
    assert _status(db, "SURFACE") is None


def test_not_in_a_closed_event(env):
    client, db, sent = env
    db.get(EventRow, E).ended_at = datetime.now(UTC)
    db.commit()
    _hb(client, "TEL-1", pending_count=0)
    assert _status(db, "TEL-1") is None

"""Move order of the PCO to an operator (decision of 29 Oct 2026)."""
import pytest

from sarcade.features.service import InvalidFeature
from sarcade.routes import service as r

E, OID = "evt-1", "order-1"


def order(**kw):
    p = {"id": OID, "event_id": E, "target_id": "TEL-02", "status": "sent",
         "destination": {"lat": 48.78, "lon": 2.02, "label": "Point haut"}, "note": "Prendre la Yagi",
         "created_by": "PCO", "updated_by": "PCO", "updated_at": "2026-10-29T10:00:00Z"}
    p.update(kw)
    return p


def test_valid_order():
    d = r.validate_move_order(order(), event_id=E, object_id=OID)
    assert d["target_id"] == "TEL-02" and d["status"] == "sent" and d["destination"]["label"] == "Point haut"
    assert "Point haut" in r.move_order_text(d) and "Prendre la Yagi" in r.move_order_text(d)


@pytest.mark.parametrize("kw", [{"target_id": ""}, {"status": "lost"}, {"destination": {"lat": 99, "lon": 2}},
                                {"note": "x" * 201}])
def test_invalid_orders(kw):
    with pytest.raises(InvalidFeature):
        r.validate_move_order(order(**kw), event_id=E, object_id=OID)


def test_only_the_pco_gives_an_order():
    new = r.validate_move_order(order(), event_id=E, object_id=OID)
    assert r.move_order_allowed(None, None, new, "PCO", "create", pco=True)
    assert not r.move_order_allowed(None, None, new, "TEL-03", "create", pco=False)


def test_the_operator_only_answers():
    before = r.validate_move_order(order(), event_id=E, object_id=OID)
    answer = dict(before, status="accepted", updated_by="TEL-02")
    assert r.move_order_allowed(before, "PCO", answer, "TEL-02", "update", pco=False)
    assert not r.move_order_allowed(before, "PCO", dict(answer, status="cancelled"), "TEL-02", "update", pco=False)
    moved = dict(answer, destination={"lat": 48.0, "lon": 2.0, "label": "ailleurs"})
    assert not r.move_order_allowed(before, "PCO", moved, "TEL-02", "update", pco=False)
    assert not r.move_order_allowed(before, "PCO", answer, "TEL-03", "update", pco=False)
    assert not r.move_order_allowed(before, "PCO", answer, "TEL-02", "delete", pco=False)
    assert r.move_order_allowed(before, "PCO", dict(before, status="cancelled"), "PCO", "update", pco=True)


def test_move_orders_are_journaled_by_the_sync():
    from sarcade.sync.service import VALID_TYPES
    assert "move_order" in VALID_TYPES


def test_move_order_default_label_is_point_de_la_carte():
    text = r.move_order_text({"destination": {"lat": 48.0, "lon": 2.0}})
    assert "« Point de la carte »" in text
    assert r.DEFAULT_ORDER_LABEL == "Point de la carte"


# --- Lot 20: answer from another terminal of the same person ---------------------


def test_same_person_by_callsign_without_ssid():
    assert r.same_person("F4ABC-7", "f4abc")
    assert r.same_person("F4ABC-9", "F4ABC-7")
    assert not r.same_person("F4ABC", "F4XYZ")
    assert not r.same_person(None, None), "two terminals without callsign are not one person"
    assert not r.same_person("", "")


def test_another_terminal_of_the_person_answers():
    before = r.validate_move_order(order(), event_id=E, object_id=OID)
    answer = dict(before, status="accepted", updated_by="SURFACE")
    assert not r.move_order_allowed(before, "PCO", answer, "SURFACE", "update", pco=False)
    assert r.move_order_allowed(before, "PCO", answer, "SURFACE", "update", pco=False, same_person_as_target=True)
    # Still an answer only: never a change of the order, never a deletion.
    moved = dict(answer, destination={"lat": 48.0, "lon": 2.0, "label": "ailleurs"})
    assert not r.move_order_allowed(before, "PCO", moved, "SURFACE", "update", pco=False, same_person_as_target=True)
    assert not r.move_order_allowed(before, "PCO", answer, "SURFACE", "delete", pco=False, same_person_as_target=True)
    assert not r.move_order_allowed(before, "PCO", dict(answer, status="cancelled"), "SURFACE", "update",
                                    pco=False, same_person_as_target=True)


def test_sync_answer_from_the_phone_when_the_order_went_to_the_pc():
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient
    from sqlalchemy import BigInteger, create_engine, event
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

    @compiles(BigInteger, "sqlite")
    def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
        return "INTEGER"

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, LogbookRow, MessageRow, AckRow, SyncOperationRow, RouteObjectRow, DeviceRow, CommGroupRow,
        SecurityEventRow, ServerSettingRow)])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    db.add(EventRow(id=E, name="Exercice", kind="exercise", status="active",
                    created_at=datetime(2026, 11, 2, tzinfo=UTC), version=0))
    db.add(DeviceRow(event_id=E, device_id="SURFACE", callsign="F4ABC"))
    db.add(DeviceRow(event_id=E, device_id="TEL-PIXEL", callsign="F4ABC-7"))
    db.add(DeviceRow(event_id=E, device_id="TEL-AUTRE", callsign="F4XYZ"))
    db.commit()

    # SQLite gives back naive times: aware again, as with PostgreSQL.
    def _aware(target, *a):
        if target.updated_at is not None and target.updated_at.tzinfo is None:
            target.updated_at = target.updated_at.replace(tzinfo=UTC)
    event.listen(RouteObjectRow, "load", _aware)
    event.listen(RouteObjectRow, "refresh", _aware)

    async def _quiet(*a, **k):
        return None

    mp = pytest.MonkeyPatch()
    mp.setattr(app_module.manager, "broadcast", _quiet)
    mp.setattr(app_module.wakeup_service, "wake_for_message", _quiet)
    mp.setattr(app_module.security_journal, "record", lambda *a, **k: None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def sync(op_id, actor, status, action="create"):
            p = order(target_id="SURFACE", status=status, created_by="PCO", updated_by=actor, updated_at=now)
            op = {"operation_id": op_id, "event_id": E, "object_id": OID, "object_type": "move_order",
                  "action": action, "client_time": now, "payload": p}
            return client.post("/api/v0.1/sync", json=[op]).json()[0]["status"]

        assert sync("op-1", "PCO", "sent") == "accepted"
        assert sync("op-2", "TEL-PIXEL", "accepted", "update") == "accepted", "same callsign F4ABC"
        assert db.get(RouteObjectRow, OID).data["status"] == "accepted"
        assert sync("op-3", "TEL-AUTRE", "arrived", "update") == "rejected", "another person"
    finally:
        app.dependency_overrides.pop(get_db, None)
        mp.undo()
        event.remove(RouteObjectRow, "load", _aware)
        event.remove(RouteObjectRow, "refresh", _aware)
        db.close()

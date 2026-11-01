# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Version and contact of the devices in the logbook, signal defaults."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade import tv
from sarcade.api import signals
from sarcade.api.app import app
from sarcade.api.deps import get_db
from sarcade.db.base import Base
from sarcade.db.models import (ClientDeviceRow, DeviceRow, EventRow, LogbookRow, SecurityEventRow,
                               ServerSettingRow)
from sarcade.events import contacts
from sarcade.features import service as features


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


T0 = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, DeviceRow, LogbookRow, ServerSettingRow, ClientDeviceRow, SecurityEventRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active", created_at=T0, version=0,
                         settings={"sync_alert_minutes": 5}))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def lines(db, kind=None):
    stmt = select(LogbookRow).order_by(LogbookRow.seq)
    if kind:
        stmt = stmt.where(LogbookRow.kind == kind)
    return [r.summary for r in db.scalars(stmt)]


def test_arrival_loss_and_return_one_line_per_change(db):
    from sarcade.api.operations import touch_device
    touch_device(db, "e1", "TEL-01", T0)
    db.commit()
    assert lines(db, "contact") == ["Arrivée dans l'événement"]
    touch_device(db, "e1", "TEL-01", T0 + timedelta(seconds=30))
    db.commit()
    assert contacts.sweep_contacts(db, T0 + timedelta(minutes=4)) == 0
    assert contacts.sweep_contacts(db, T0 + timedelta(minutes=6)) == 1
    db.commit()
    assert contacts.sweep_contacts(db, T0 + timedelta(minutes=7)) == 0     # once only
    touch_device(db, "e1", "TEL-01", T0 + timedelta(minutes=12, seconds=30))
    db.commit()
    assert lines(db, "contact") == ["Arrivée dans l'événement", "Perte de contact", "Contact rétabli (après 12 min)"]
    touch_device(db, "e1", "TEL-01", T0 + timedelta(minutes=14))
    db.commit()
    assert len(lines(db, "contact")) == 3


def test_sweep_uses_the_event_threshold_and_skips_closed_events(db):
    from sarcade.api.operations import touch_device
    db.add(EventRow(id="e2", name="Fini", kind="exercise", status="closed", created_at=T0, version=0,
                    ended_at=T0))
    touch_device(db, "e1", "A", T0)
    touch_device(db, "e2", "B", T0)
    db.get(EventRow, "e1").settings = {"sync_alert_minutes": 20}
    db.commit()
    assert contacts.sweep_contacts(db, T0 + timedelta(minutes=10)) == 0
    assert contacts.sweep_contacts(db, T0 + timedelta(minutes=21)) == 1
    db.get(EventRow, "e1").settings = None                                  # default: 5 min
    touch_device(db, "e1", "C", T0)
    db.commit()
    assert contacts.sweep_contacts(db, T0 + timedelta(minutes=6)) == 1


def test_heartbeat_logs_version_changes_not_the_first_report(client, db):
    url = "/api/v0.1/events/e1/devices/TEL-01/heartbeat"
    assert client.post(url, json={"label": "Équipe 1", "platform": "android", "app_version": "0.1.0+414"}).status_code == 200
    assert lines(db, "device_version") == []
    client.post(url, json={"app_version": "0.1.0+414"})
    client.post(url, json={"app_version": "0.1.0+415"})
    assert lines(db, "device_version") == ["Équipe 1 : SARCADE 0.1.0+414 → 0.1.0+415 (android)"]
    assert lines(db, "contact") == ["Arrivée dans l'événement"]
    # Operators list (GET /events/{id}/devices, same dict as device.updated).
    from sarcade.api.operations import device_dict
    row = db.get(DeviceRow, ("e1", "TEL-01"))
    row.last_contact_at = contacts.aware(row.last_contact_at)
    d = device_dict(row, {}, row.last_contact_at)
    assert d["platform"] == "android" and d["app_version"] == "0.1.0+415"
    assert d["last_seen"] == d["last_contact_at"] and d["last_seen"] is not None


def test_tv_hides_version_and_contact_lines():
    # The TV no longer shows the logbook at all (29 Oct 2026).
    assert "logbook" not in tv.snapshot({"id": "e"}, [], [])


def test_bearing_exercise_and_frequency():
    base = {"id": "b1", "event_id": "e", "kind": "bearing", "points": [[48.82, 1.91]], "color": 0xFF3DD6F5,
            "stroke_width": 2, "label": "", "updated_by": "ADR78-01", "updated_at": "2026-10-27T10:00:00Z"}
    b = features.validate_upsert({**base, "bearing": {"azimuth_deg": 17, "exercise": True, "frequency_mhz": 121.375}},
                                 event_id="e", object_id="b1")["bearing"]
    assert b["exercise"] is True and b["frequency_mhz"] == 121.375
    b = features.validate_upsert({**base, "bearing": {"azimuth_deg": 17, "exercise": False}},
                                 event_id="e", object_id="b1")["bearing"]
    assert "exercise" not in b and "frequency_mhz" not in b
    for bad in ({"exercise": "oui"}, {"frequency_mhz": 0.5}, {"frequency_mhz": 20_000}):
        with pytest.raises(features.InvalidFeature):
            features.validate_upsert({**base, "bearing": {"azimuth_deg": 17, **bad}}, event_id="e", object_id="b1")
    assert features.logbook_summary("bearing", "", "create", exercise=True).startswith("EXERCICE · ")
    assert not features.logbook_summary("bearing", "", "create").startswith("EXERCICE")


def test_signal_defaults_initial_list_and_admin_edit(client, db, monkeypatch):
    r = client.get("/api/v0.1/signals/defaults")
    assert r.status_code == 200 and r.json() == {"signals": signals.INITIAL}
    new = [{"label": "Balise d'exercice", "frequency_mhz": 121.375, "exercise": True, "hidden": False},
           {"label": "Relais local", "frequency_mhz": 145.6, "exercise": False, "hidden": True}]
    assert client.put("/api/v0.1/signals/defaults", json={"signals": new}).status_code == 403
    from sarcade.api import roles as roles_api
    app.dependency_overrides[roles_api.require_admin_email] = lambda: "admin@x.fr"
    try:
        r = client.put("/api/v0.1/signals/defaults", json={"signals": new})
        assert r.status_code == 200 and r.json()["signals"] == new
        assert client.get("/api/v0.1/signals/defaults").json()["signals"] == new
        for bad in ([{"label": "", "frequency_mhz": 121.5}], [{"label": "x" * 61, "frequency_mhz": 121.5}],
                    [{"label": "a", "frequency_mhz": 0}], [{"label": "a", "frequency_mhz": 121.5}] * 21):
            assert client.put("/api/v0.1/signals/defaults", json={"signals": bad}).status_code == 422
    finally:
        app.dependency_overrides.pop(roles_api.require_admin_email, None)


def test_admin_required_without_checked_email():
    from fastapi import HTTPException
    from sarcade.api.roles import require_admin_email
    with pytest.raises(HTTPException) as exc:
        require_admin_email(SimpleNamespace(state=SimpleNamespace()), None)
    assert exc.value.status_code == 403


def test_heartbeat_carries_the_operator_icon(client, db):
    """1 Nov 2026: icon chosen in Paramètres › Profil, shown to every device."""
    url = "/api/v0.1/events/e1/devices/TEL-02/heartbeat"
    assert client.post(url, json={"icon": "car"}).status_code == 200
    assert db.get(DeviceRow, ("e1", "TEL-02")).icon == "car"
    assert client.post(url, json={"icon": "rocket"}).status_code == 422
    client.post(url, json={"battery_pct": 50})
    assert db.get(DeviceRow, ("e1", "TEL-02")).icon == "car"   # unchanged when not sent
    client.post(url, json={"icon": ""})
    assert db.get(DeviceRow, ("e1", "TEL-02")).icon is None

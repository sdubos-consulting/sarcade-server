# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Colour of an operator shared with the others (card « Carte : la trace
d'un opérateur dans la couleur choisie dans son profil », validated on
10 Nov 2026): sent with the heartbeat, kept on the device, returned with the
list of the devices (Équipes), the latest positions, the realtime positions
and the TV; red reserved for the SOS; none from a former client."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sarcade.api import tv as tv_api
from sarcade.api.app import app
from sarcade.api.operations import device_dict
from sarcade.api.deps import get_db
from sarcade.api.serializers import device_colours, with_operator_status
from sarcade.db.base import Base
from sarcade.db.models import (ClientDeviceRow, DeviceRow, EventRow, LogbookRow, SecurityEventRow,
                               ServerSettingRow, SyncOperationRow)
from sarcade.profile.preferences import (RESERVED_RED_FALLBACK, InvalidPreferences, is_reserved_red,
                                         operator_colour)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # autoincrement keys on SQLite
    return "INTEGER"


T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
GREEN, RED, PINK = 0xFF43A047, 0xFFE53935, 0xFFD81B60


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        EventRow, DeviceRow, LogbookRow, ServerSettingRow, ClientDeviceRow, SecurityEventRow, SyncOperationRow)])
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    session.add(EventRow(id="e1", name="Exercice", kind="exercise", status="active", created_at=T0, version=0,
                         settings={}))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def listed(db, device_id: str) -> dict:
    row = db.get(DeviceRow, ("e1", device_id))
    return device_dict(DeviceRow(event_id=row.event_id, device_id=row.device_id, color=row.color), {}, T0)


# --- Rule of the colour (choice 4) -------------------------------------------


def test_red_is_reserved_as_in_the_application():
    assert is_reserved_red(RED) and is_reserved_red(0xFFFF0000) and is_reserved_red(0xFFB71C1C)
    assert not is_reserved_red(GREEN) and not is_reserved_red(RESERVED_RED_FALLBACK)
    # Pink (hue 339°) and orange (hue 33°) are not the red of the SOS.
    assert not is_reserved_red(0xFFFB8C00)
    assert not is_reserved_red(0xFF000000) and not is_reserved_red(0xFFFFFFFF)


def test_operator_colour_cleaned():
    assert operator_colour(GREEN) == GREEN
    assert operator_colour(RED) == RESERVED_RED_FALLBACK
    assert operator_colour(0x0043A047) == GREEN          # always opaque
    assert operator_colour(0) is None                    # removed
    for bad in (-1, 0x1FFFFFFFF, True, "green", 1.5):
        with pytest.raises(InvalidPreferences):
            operator_colour(bad)


# --- Heartbeat and Équipes (choices 1, 2, 3) -------------------------------------


def test_heartbeat_carries_the_colour_and_the_list_returns_it(client, db):
    url = "/api/v0.1/events/e1/devices/F4JPO/heartbeat"
    assert client.post(url, json={"color": GREEN}).status_code == 200
    assert db.get(DeviceRow, ("e1", "F4JPO")).color == GREEN
    # List of the devices (Équipes); SQLite keeps naive times: dict built
    # without the contact time.
    assert listed(db, "F4JPO")["color"] == GREEN
    # Changed in the profile (choice 7): the new colour replaces the old.
    client.post(url, json={"color": PINK})
    assert db.get(DeviceRow, ("e1", "F4JPO")).color == PINK
    # Not sent (former client): unchanged.
    client.post(url, json={"battery_pct": 40})
    assert db.get(DeviceRow, ("e1", "F4JPO")).color == PINK


def test_heartbeat_red_refused_invalid_rejected_zero_removes(client, db):
    url = "/api/v0.1/events/e1/devices/TEL-2/heartbeat"
    client.post(url, json={"color": RED})
    assert db.get(DeviceRow, ("e1", "TEL-2")).color == RESERVED_RED_FALLBACK
    assert client.post(url, json={"color": -5}).status_code == 422
    assert client.post(url, json={"color": 0x100000000}).status_code == 422
    client.post(url, json={"color": 0})
    assert db.get(DeviceRow, ("e1", "TEL-2")).color is None


def test_former_client_without_colour(client, db):
    """Choice 6: a device that never sent a colour has ``color`` null, the
    clients keep their palette."""
    client.post("/api/v0.1/events/e1/devices/OLD/heartbeat", json={"app_version": "0.1.0+400"})
    row = db.get(DeviceRow, ("e1", "OLD"))
    assert row.color is None
    assert listed(db, "OLD")["color"] is None


# --- Positions and TV (choices 2, 3, 7) ------------------------------------------


def test_latest_positions_carry_the_colour(db):
    db.add_all([DeviceRow(event_id="e1", device_id="F4JPO", color=GREEN),
                DeviceRow(event_id="e1", device_id="CAR", operator_status="pause", operator_status_at=T0)])
    db.commit()
    out = with_operator_status(db, "e1", [{"device_id": "F4JPO"}, {"device_id": "CAR"}, {"device_id": "OLD"}])
    assert [p["color"] for p in out] == [GREEN, None, None]
    assert out[0]["operator_status"] is None and out[1]["operator_status"] == "pause"
    assert device_colours(db, "e1", ["F4JPO", "CAR"]) == {"F4JPO": GREEN}
    assert device_colours(db, "e1", []) == {}


def test_tv_gets_the_colour_of_dots_and_traces(db):
    assert "color" in tv_api.STATE_POSITION_KEYS
    db.add(DeviceRow(event_id="e1", device_id="F4JPO", color=GREEN))
    db.commit()
    traces = [{"device_id": "F4JPO", "aprs": False, "points": [[48.8, 2.1], [48.9, 2.2]]},
              {"device_id": "OLD", "aprs": False, "points": [[48.8, 2.1], [48.9, 2.2]]},
              {"device_id": "aprs:F4ABC", "aprs": True, "points": [[48.8, 2.1], [48.9, 2.2]]}]
    assert [t["color"] for t in tv_api._coloured(db, "e1", traces)] == [GREEN, None, None]
    # Read again at each call: a colour changed recolours a cached trace.
    db.get(DeviceRow, ("e1", "F4JPO")).color = PINK
    db.commit()
    assert tv_api._coloured(db, "e1", traces)[0]["color"] == PINK


def test_tv_page_paints_dots_and_traces_in_the_operator_colour():
    page = tv_api._TV
    assert "function argb(c,dflt)" in page
    assert "argb(t.color,'#3dd6f5')" in page and "argb(p.color,'#3dd6f5')" in page
    assert "setStyle({fillColor:fill})" in page

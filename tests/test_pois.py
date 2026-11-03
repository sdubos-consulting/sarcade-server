# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Points of interest: sheet, rename and delete (decision of 29 Oct 2026)."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from sarcade.api import app as api
from sarcade.api.schemas import SyncOperationIn
from sarcade.db.models import LogbookRow, POIRow
from sarcade.pois import service as pois
from sarcade.sync.service import VALID_TYPES

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)


def row(**kw):
    r = POIRow(id="p1", event_id="e1", kind="designated", label="Point haut",
               point=from_shape(Point(1.91, 48.82), srid=4326), created_at=NOW, version=0,
               created_by="TEL-02", color=0xFF3DD6F5)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


class FakeDb:
    def __init__(self, r):
        self.r, self.added, self.journal = r, [], SimpleNamespace(status="accepted", payload=None)

    def get(self, _model, _id):
        return self.r

    def add(self, x):
        self.added.append(x)

    def flush(self):
        pass

    def scalar(self, _stmt):
        return self.journal


def op(action, **payload):
    return SyncOperationIn(operation_id=f"o-{action}", event_id="e1", object_id="p1", object_type="poi",
                           action=action, client_time=NOW, payload=payload)


@pytest.fixture(autouse=True)
def journal(monkeypatch):
    monkeypatch.setattr(api, "record_operation", lambda db, **kw: ("accepted", 7))


def test_rights_author_or_pco():
    assert pois.can_edit("TEL-02", "TEL-02")
    assert not pois.can_edit("TEL-02", "TEL-03")
    assert pois.can_edit("TEL-02", "PCO") and pois.can_edit("TEL-02", "TEL-03", pco=True)
    assert not pois.can_edit(None, "TEL-03") and pois.can_edit(None, "PCO-1")
    assert not pois.can_edit("TEL-02", None)


def test_clean_values():
    assert pois.clean_label("  Gué  ") == "Gué" and pois.clean_label("") is None
    with pytest.raises(pois.InvalidPoi):
        pois.clean_label("x" * 161)
    with pytest.raises(pois.InvalidPoi):
        pois.clean_color(-1)
    assert pois.clean_color(0xFF00FF00) == 0xFF00FF00


def test_rename_by_author():
    db = FakeDb(row())
    status, cursor, msg = api._apply_poi_change(db, op("update", label="Gué", updated_by="TEL-02"), NOW, False)
    assert status == "accepted" and cursor == 7 and msg["type"] == "poi.updated"
    assert msg["data"]["label"] == "Gué" and msg["data"]["version"] == 1 and db.journal.payload == msg["data"]
    assert msg["data"]["color"] == 0xFF3DD6F5 and msg["data"]["created_by"] == "TEL-02"
    assert not [x for x in db.added if isinstance(x, LogbookRow)]


def test_delete_by_pco_goes_to_logbook():
    r = row()
    db = FakeDb(r)
    status, _, msg = api._apply_poi_change(db, op("delete", updated_by="PCO"), NOW, False)
    assert status == "accepted" and msg["type"] == "poi.deleted" and msg["data"]["deleted"] is True
    assert r.deleted_at == NOW
    logs = [x for x in db.added if isinstance(x, LogbookRow)]
    assert len(logs) == 1 and "Point haut" in logs[0].summary and "supprimé" in logs[0].summary


def test_other_operator_is_refused():
    with pytest.raises(api.SyncRejected) as exc:
        api._apply_poi_change(FakeDb(row()), op("delete", updated_by="TEL-03"), NOW, False)
    assert exc.value.journal["category"] == "rights"


def test_pco_by_access_email_may_delete():
    status, _, _ = api._apply_poi_change(FakeDb(row()), op("delete", updated_by="TEL-03"), NOW, True)
    assert status == "accepted"


def test_unknown_or_already_deleted():
    with pytest.raises(api.SyncRejected):
        api._apply_poi_change(FakeDb(None), op("delete", updated_by="PCO"), NOW, False)
    db = FakeDb(row(deleted_at=NOW))
    status, _, msg = api._apply_poi_change(db, op("delete", updated_by="PCO"), NOW, False)
    assert status == "conflict" and msg is None and db.journal.status == "conflict"


def test_invalid_label_is_rejected():
    with pytest.raises(api.SyncRejected):
        api._apply_poi_change(FakeDb(row()), op("update", label=5, updated_by="TEL-02"), NOW, False)


def test_poi_is_still_a_sync_type():
    assert "poi" in VALID_TYPES


def test_icon_values():
    assert pois.clean_icon(None) is None and pois.clean_icon("") is None
    assert pois.clean_icon("helipad") == "helipad" and len(pois.ICONS) >= 12
    with pytest.raises(pois.InvalidPoi):
        pois.clean_icon("skull")
    with pytest.raises(pois.InvalidPoi):
        pois.clean_icon(3)


def test_icon_change_keeps_the_name():
    db = FakeDb(row())
    status, _, msg = api._apply_poi_change(db, op("update", icon="parking", updated_by="TEL-02"), NOW, False)
    assert status == "accepted" and msg["data"]["icon"] == "parking" and msg["data"]["label"] == "Point haut"


def test_rename_keeps_the_icon():
    db = FakeDb(row(icon="water"))
    _, _, msg = api._apply_poi_change(db, op("update", label="Gué", updated_by="TEL-02"), NOW, False)
    assert msg["data"]["icon"] == "water" and msg["data"]["label"] == "Gué"


def test_invalid_icon_is_rejected():
    with pytest.raises(api.SyncRejected):
        api._apply_poi_change(FakeDb(row()), op("update", icon="skull", updated_by="TEL-02"), NOW, False)


# Lot 25: colour in the window of the point, and move.

def test_color_change_by_author():
    db = FakeDb(row())
    status, _, msg = api._apply_poi_change(db, op("update", color=0xFFE53935, updated_by="TEL-02"), NOW, False)
    assert status == "accepted" and msg["data"]["color"] == 0xFFE53935 and msg["data"]["label"] == "Point haut"


def test_invalid_color_is_rejected():
    with pytest.raises(api.SyncRejected):
        api._apply_poi_change(FakeDb(row()), op("update", color="red", updated_by="TEL-02"), NOW, False)


def test_one_update_for_name_icon_and_color():
    db = FakeDb(row())
    _, _, msg = api._apply_poi_change(db, op("update", label="Gué", icon="water", color=0xFF1E88E5,
                                             updated_by="TEL-02"), NOW, False)
    assert (msg["data"]["label"], msg["data"]["icon"], msg["data"]["color"]) == ("Gué", "water", 0xFF1E88E5)


def test_move_changes_the_place_and_goes_to_logbook():
    db = FakeDb(row())
    # About 120 m north of 48.82, 1.91.
    status, _, msg = api._apply_poi_change(db, op("update", lat=48.82108, lon=1.91, updated_by="TEL-02"), NOW, False)
    assert status == "accepted" and msg["data"]["lat"] == pytest.approx(48.82108) and msg["data"]["lon"] == pytest.approx(1.91)
    logs = [x for x in db.added if isinstance(x, LogbookRow)]
    assert len(logs) == 1 and logs[0].summary == "Point d'intérêt « Point haut » déplacé de 120 m"
    assert logs[0].kind == "poi" and logs[0].actor_id == "TEL-02"


def test_move_by_another_operator_is_refused():
    with pytest.raises(api.SyncRejected):
        api._apply_poi_change(FakeDb(row()), op("update", lat=48.83, lon=1.92, updated_by="TEL-03"), NOW, False)


def test_move_by_pco_is_accepted():
    status, _, _ = api._apply_poi_change(FakeDb(row()), op("update", lat=48.83, lon=1.92, updated_by="PCO"), NOW, False)
    assert status == "accepted"


@pytest.mark.parametrize("lat,lon", [(91, 1.0), (48.0, None), ("48", 1.0), (True, 1.0), (float("nan"), 1.0)])
def test_invalid_position_is_rejected(lat, lon):
    with pytest.raises(api.SyncRejected):
        api._apply_poi_change(FakeDb(row()), op("update", lat=lat, lon=lon, updated_by="TEL-02"), NOW, False)


def test_rename_alone_does_not_move_nor_log():
    db = FakeDb(row())
    _, _, msg = api._apply_poi_change(db, op("update", label="Gué", updated_by="TEL-02"), NOW, False)
    assert msg["data"]["lat"] == pytest.approx(48.82) and not [x for x in db.added if isinstance(x, LogbookRow)]


def test_distance_formats():
    assert pois.format_distance(119.6) == "120 m" and pois.format_distance(1430) == "1,4 km"
    assert pois.move_summary(None, 50) == "Point d'intérêt déplacé de 50 m"

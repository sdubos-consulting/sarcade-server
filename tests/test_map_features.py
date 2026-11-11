from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from sarcade.features import service as f

EVENT, FID = "evt-1", "0192f6a0-0000-7000-8000-000000000001"


def payload(**overrides):
    p = {
        "id": FID, "event_id": EVENT, "kind": "zone",
        "points": [[48.70, 2.00], [48.71, 2.00], [48.71, 2.01]],
        "radius_m": None, "color": 0xFFE53935, "stroke_width": 4, "label": " Secteur A ",
        "created_by": "TERRAIN-01", "updated_by": "TERRAIN-01",
        "updated_at": "2026-10-07T20:00:00.000Z",
    }
    p.update(overrides)
    return p


def stored(at, by="TERRAIN-01"):
    return SimpleNamespace(updated_at=at, updated_by=by)


T0 = datetime(2026, 10, 7, 20, 0, tzinfo=UTC)


def test_valid_zone_is_canonicalised():
    c = f.validate_upsert(payload(), event_id=EVENT, object_id=FID)
    assert c["label"] == "Secteur A"
    assert c["updated_at"] == T0
    assert c["stroke_width"] == 4.0
    assert c["radius_m"] is None


@pytest.mark.parametrize("overrides,reason", [
    ({"kind": "hexagon"}, "invalid_kind"),
    ({"points": [[48.7, 2.0], [48.71, 2.0]]}, "not_enough_points"),
    ({"points": [[95, 2.0], [48.71, 2.0], [48.71, 2.01]]}, "point_out_of_range"),
    ({"id": "other"}, "id_mismatch"),
    ({"kind": "circle", "points": [[48.7, 2.0]], "radius_m": -3}, "invalid_radius"),
    ({"label": "x" * 200}, "invalid_label"),
    ({"updated_at": "hier"}, "invalid_updated_at"),
])
def test_invalid_payloads_are_rejected(overrides, reason):
    with pytest.raises(f.InvalidFeature, match=reason):
        f.validate_upsert(payload(**overrides), event_id=EVENT, object_id=FID)


def test_geometry_is_lon_lat_and_closed():
    c = f.validate_upsert(payload(), event_id=EVENT, object_id=FID)
    assert f.geometry_wkt(c) == "POLYGON((2.0 48.7,2.0 48.71,2.01 48.71,2.0 48.7))"
    rect = f.validate_upsert(payload(kind="rectangle", points=[[48.7, 2.0], [48.71, 2.01]]), event_id=EVENT, object_id=FID)
    assert f.geometry_wkt(rect).startswith("POLYGON((2.0 48.7,2.01 48.7,2.01 48.71,2.0 48.71,2.0 48.7")
    circle = f.validate_upsert(payload(kind="circle", points=[[48.7, 2.0]], radius_m=250), event_id=EVENT, object_id=FID)
    assert f.geometry_wkt(circle) == "POINT(2.0 48.7)"
    line = f.validate_upsert(payload(kind="line", points=[[48.7, 2.0], [48.8, 2.1]]), event_id=EVENT, object_id=FID)
    assert f.geometry_wkt(line) == "LINESTRING(2.0 48.7,2.1 48.8)"


def test_last_writer_wins():
    change = {"updated_at": T0, "updated_by": "TERRAIN-02"}
    assert f.decide("update", change, None).status == "accepted"
    assert f.decide("update", change, stored(T0 - timedelta(seconds=1))).status == "accepted"
    assert f.decide("update", change, stored(T0 + timedelta(seconds=1))).status == "conflict"
    # Same instant: the author breaks the tie, the same way on every server.
    assert f.decide("update", change, stored(T0, "TERRAIN-01")).status == "accepted"
    assert f.decide("update", change, stored(T0, "TERRAIN-03")).status == "conflict"
    # Replaying the exact same change is not newer.
    assert f.decide("update", change, stored(T0, "TERRAIN-02")).status == "conflict"


def test_delete_is_a_tombstone_and_a_newer_change_restores():
    delete = {"updated_at": T0, "updated_by": "PCO"}
    d = f.decide("delete", delete, stored(T0 - timedelta(minutes=1)))
    assert d.status == "accepted" and d.tombstone
    assert f.decide("delete", delete, None).tombstone
    older_edit = {"updated_at": T0 - timedelta(seconds=10), "updated_by": "TERRAIN-01"}
    assert f.decide("update", older_edit, stored(T0, "PCO")).status == "conflict"
    undo = {"updated_at": T0 + timedelta(seconds=5), "updated_by": "PCO"}
    r = f.decide("update", undo, stored(T0, "PCO"))
    assert r.status == "accepted" and not r.tombstone


def test_future_clock_is_clamped():
    now = T0
    assert f.clamp_time(now + timedelta(minutes=4), now) == now + timedelta(minutes=4)
    assert f.clamp_time(now + timedelta(hours=3), now) == now


def test_logbook_summary_is_french():
    assert f.logbook_summary("zone", "Secteur A", "create") == "Objet cartographique créé : Zone « Secteur A »"
    assert f.logbook_summary("circle", "", "delete") == "Objet cartographique supprimé : Cercle"


def test_opacity_is_kept_below_100_percent_only():
    assert "opacity" not in f.validate_upsert(payload(), event_id=EVENT, object_id=FID)
    assert "opacity" not in f.validate_upsert(payload(opacity=1), event_id=EVENT, object_id=FID)
    assert f.validate_upsert(payload(opacity=0.456), event_id=EVENT, object_id=FID)["opacity"] == 0.46
    for bad in (0, 1.5, "x", True):
        with pytest.raises(f.InvalidFeature):
            f.validate_upsert(payload(opacity=bad), event_id=EVENT, object_id=FID)


def test_text_font_size_is_kept_unless_default():
    text = dict(kind="text", points=[[48.7, 2.0]], label="PC")
    assert f.validate_upsert(payload(**text, font_size=24), event_id=EVENT, object_id=FID)["font_size"] == 24.0
    assert "font_size" not in f.validate_upsert(payload(**text, font_size=15), event_id=EVENT, object_id=FID)
    assert "font_size" not in f.validate_upsert(payload(font_size=24), event_id=EVENT, object_id=FID)
    with pytest.raises(f.InvalidFeature, match="invalid_font_size"):
        f.validate_upsert(payload(**text, font_size=200), event_id=EVENT, object_id=FID)


def test_group_of_several_objects_is_one_logbook_line():
    g = f.group_of({"group": {"id": "g1", "n": 5, "action": "delete"}})
    assert g == {"id": "g1", "n": 5, "action": "delete"}
    assert f.group_summary(g) == "5 objets supprimés"
    assert f.group_of({"group": {"id": "g1", "n": 1, "action": "delete"}}) is None
    assert f.group_of({"group": {"id": "g1", "n": 3, "action": "explode"}}) is None
    assert f.group_of({}) is None

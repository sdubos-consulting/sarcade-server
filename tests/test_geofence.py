"""Géorepérage (ATAK 5)."""
from sarcade import geofence as g
from sarcade.features import service as f

SQUARE = {"id": "z1", "kind": "zone", "label": "Zone dangereuse",
          "points": [[48.80, 1.90], [48.80, 1.92], [48.82, 1.92], [48.82, 1.90]], "geofence": True}


def test_inside_polygon_rectangle_circle():
    assert g.contains(SQUARE, 48.81, 1.91)
    assert not g.contains(SQUARE, 48.83, 1.91)
    assert g.contains({"kind": "rectangle", "points": [[48.8, 1.9], [48.82, 1.92]]}, 48.81, 1.91)
    assert g.contains({"kind": "circle", "points": [[48.8, 1.9]], "radius_m": 200}, 48.801, 1.9)
    assert not g.contains({"kind": "circle", "points": [[48.8, 1.9]], "radius_m": 50}, 48.801, 1.9)


def test_alert_only_on_a_change_of_side():
    assert g.transitions("e", "TEL-1", 48.83, 1.91, [SQUARE]) == []          # first position: state only
    assert g.transitions("e", "TEL-1", 48.81, 1.91, [SQUARE]) == [(SQUARE, True)]
    assert g.transitions("e", "TEL-1", 48.811, 1.911, [SQUARE]) == []
    assert g.transitions("e", "TEL-1", 48.79, 1.91, [SQUARE]) == [(SQUARE, False)]
    assert g.message("TEL-1", SQUARE, True) == "Géorepérage : TEL-1 est entré dans « Zone dangereuse »"


def test_flag_kept_by_the_drawing_validation():
    p = {"id": "0192f6a0-0000-7000-8000-000000000009", "event_id": "e", "kind": "zone",
         "points": SQUARE["points"], "color": 0xFFE53935, "stroke_width": 3, "label": "Z",
         "created_by": "PCO", "updated_by": "PCO", "updated_at": "2026-10-29T10:00:00Z", "geofence": True}
    assert f.validate_upsert(p, event_id="e", object_id=p["id"])["geofence"] is True
    p2 = {**p, "kind": "line", "points": SQUARE["points"][:2]}
    assert "geofence" not in f.validate_upsert(p2, event_id="e", object_id=p["id"])

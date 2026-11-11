import xml.etree.ElementTree as ET

import pytest

from sarcade.features.service import InvalidFeature
from sarcade.routes import service as r

E, RID, WID = "evt-1", "route-1", "wp-1"


def route(**kw):
    p = {"id": RID, "event_id": E, "name": "Recherche secteur nord", "point_order": ["a", "b"],
         "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": "2026-10-10T06:00:00Z"}
    p.update(kw)
    return p


def waypoint(**kw):
    p = {"id": WID, "event_id": E, "route_id": RID, "name": "CP3", "type": "checkpoint",
         "lat": 48.8, "lon": 2.1, "created_by": "TEL-01", "updated_by": "TEL-01",
         "updated_at": "2026-10-10T06:00:00Z"}
    p.update(kw)
    return p


def test_route_defaults():
    c = r.validate_route(route(), event_id=E, object_id=RID)
    assert c["default_leg_mode"] == "straight" and c["profile"] == "foot" and c["status"] == "draft"
    assert c["assigned_team_id"] is None and not r.is_assigned(c)


def test_waypoint_defaults_to_50_m_radius():
    c = r.validate_waypoint(waypoint(), event_id=E, object_id=WID)
    assert c["radius_m"] == 50.0 and c["leg_mode"] == "straight" and c["leg_needs_routing"] is False


@pytest.mark.parametrize("kw,reason", [
    ({"point_order": ["a", "a"]}, "invalid_point_order"),
    ({"default_leg_mode": "fly"}, "invalid_leg_mode"),
    ({"profile": "bike"}, "invalid_profile"),
    ({"name": ""}, "invalid_name"),
])
def test_invalid_routes(kw, reason):
    with pytest.raises(InvalidFeature, match=reason):
        r.validate_route(route(**kw), event_id=E, object_id=RID)


@pytest.mark.parametrize("kw,reason", [
    ({"type": "summit"}, "invalid_type"),
    ({"radius_m": 2}, "invalid_radius"),
    ({"lat": 91}, "invalid_lat"),
    ({"route_id": ""}, "invalid_route_id"),
    ({"leg_geometry": "abc"}, "invalid_leg_geometry"),
])
def test_invalid_waypoints(kw, reason):
    with pytest.raises(InvalidFeature, match=reason):
        r.validate_waypoint(waypoint(**kw), event_id=E, object_id=WID)


def test_paths_leg_keeps_routing_flag():
    c = r.validate_waypoint(waypoint(leg_mode="paths", leg_needs_routing=True), event_id=E, object_id=WID)
    assert c["leg_needs_routing"] is True
    c = r.validate_waypoint(waypoint(leg_mode="straight", leg_needs_routing=True), event_id=E, object_id=WID)
    assert c["leg_needs_routing"] is False


def test_assigned_route_is_edited_by_author_and_pco_only():
    free = {"assigned_team_id": None}
    assigned = {"assigned_team_id": "team-1"}
    assert r.can_edit(free, "TEL-01", "TEL-09")
    assert r.can_edit(assigned, "TEL-01", "TEL-01") and r.can_edit(assigned, "TEL-01", "PCO")
    assert not r.can_edit(assigned, "TEL-01", "TEL-09")


def test_concurrent_order_change_detection():
    assert r.concurrent_order_change(["a", "c", "b"], ["a", "b", "c"], ["b", "a", "c"])
    assert not r.concurrent_order_change(["a", "b", "c"], ["a", "b", "c"], ["b", "a", "c"]), "no concurrent edit"
    assert not r.concurrent_order_change(["a", "b"], None, ["b", "a"])


def test_leg_length_straight_and_along_paths():
    a = {"lat": 48.8, "lon": 2.1}
    b = {"lat": 48.81, "lon": 2.1, "leg_mode": "straight"}
    straight = r.leg_length_m(a, b)
    assert 1100 < straight < 1125
    b_paths = dict(b, leg_mode="paths", leg_geometry=[[48.8, 2.1], [48.805, 2.11], [48.81, 2.1]])
    assert r.leg_length_m(a, b_paths) > straight


def test_change_summary():
    text = r.change_summary("Nord", ["point « CP3 » déplacé", "ordre des points modifié"])
    assert text == "Route « Nord » modifiée : point « CP3 » déplacé; ordre des points modifié. Merci d'accuser réception."


def test_gpx_has_named_route_points_and_track_for_paths():
    wps = [{"name": "Départ", "type": "start", "lat": 48.8, "lon": 2.1, "comment": "Parking"},
           {"name": "CP1 & pont", "type": "checkpoint", "lat": 48.81, "lon": 2.12, "leg_mode": "paths",
            "leg_geometry": [[48.8, 2.1], [48.805, 2.11], [48.81, 2.12]]}]
    root = ET.fromstring(r.gpx({"name": "Nord"}, wps))
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    names = [e.text for e in root.findall("g:rte/g:rtept/g:name", ns)]
    assert names == ["Départ", "CP1 & pont"]
    assert len(root.findall("g:trk/g:trkseg/g:trkpt", ns)) == 4

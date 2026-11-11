import pytest

from sarcade.features import service as features

BASE = {"id": "b1", "event_id": "e", "kind": "bearing", "points": [[48.82, 1.91]], "color": 0xFF3DD6F5,
        "stroke_width": 2, "label": "", "updated_by": "ADR78-01", "updated_at": "2026-10-27T10:00:00Z"}


def test_bearing_defaults_and_canonical_form():
    out = features.validate_upsert({**BASE, "bearing": {"azimuth_deg": 17, "taken_at": "2026-10-27T10:00:00Z",
                                                        "method": "compass", "magnetic_deg": 15.2}},
                                   event_id="e", object_id="b1")
    b = out["bearing"]
    assert b["length_m"] == 10_000 and b["cone_deg"] == 5 and b["strength"] == 0
    assert b["method"] == "compass" and b["magnetic_deg"] == 15.2
    assert features.geometry_wkt(out) == "POINT(1.91 48.82)"


@pytest.mark.parametrize("bearing, error", [
    (None, "invalid_bearing"),
    ({}, "invalid_azimuth"),
    ({"azimuth_deg": 10, "cone_deg": 60}, "invalid_cone"),
    ({"azimuth_deg": 10, "strength": 12}, "invalid_strength"),
    ({"azimuth_deg": 10, "length_m": 10}, "invalid_length"),
])
def test_invalid_bearings(bearing, error):
    with pytest.raises(features.InvalidFeature, match=error):
        features.validate_upsert({**BASE, "bearing": bearing}, event_id="e", object_id="b1")


def test_logbook_name():
    assert "Relevé" in features.logbook_summary("bearing", "", "create")


def test_zone_decided_by_hand():
    out = features.validate_upsert({**BASE, "bearing": {"azimuth_deg": 17, "zone": "out"}}, event_id="e", object_id="b1")
    assert out["bearing"]["zone"] == "out"
    auto = features.validate_upsert({**BASE, "bearing": {"azimuth_deg": 17, "zone": "auto"}}, event_id="e", object_id="b1")
    assert "zone" not in auto["bearing"]
    with pytest.raises(features.InvalidFeature, match="invalid_zone"):
        features.validate_upsert({**BASE, "bearing": {"azimuth_deg": 17, "zone": "maybe"}}, event_id="e", object_id="b1")

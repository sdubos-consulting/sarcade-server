import asyncio
import json

import httpx
import pytest

from sarcade.features.service import InvalidFeature
from sarcade.routes import service as routes
from sarcade.routing import valhalla as v


def test_polyline6_round_trip():
    pts = [[48.80123, 2.13456], [48.8103, 2.1401], [48.79, 2.09]]
    assert v.decode_polyline6(v.encode_polyline6(pts)) == pts


def test_modes_map_to_valhalla_costings():
    car = v.build_request([[48.8, 2.1], [48.9, 2.2]], "car")
    assert car["costing"] == "auto" and car["directions_options"]["language"] == "fr-FR"
    assert v.build_request([[48.8, 2.1], [48.9, 2.2]], "foot")["costing"] == "pedestrian"
    off = v.build_request([[48.8, 2.1], [48.9, 2.2]], "offroad")
    assert off["costing"] == "auto" and off["costing_options"]["auto"]["use_tracks"] == 1.0
    with pytest.raises(ValueError):
        v.build_request([[48.8, 2.1]], "car")


def test_closed_roads_become_exclusion_polygons_around_the_line():
    body = v.build_request([[48.8, 2.1], [48.9, 2.2]], "car", [[[48.85, 2.15], [48.851, 2.152]]])
    ring = body["exclude_polygons"][0]
    assert ring[0] == ring[-1], "closed ring"
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    assert 2.149 < min(lons) < 2.15 and 2.152 < max(lons) < 2.153, "a few metres around, lon first"
    assert 48.849 < min(lats) and max(lats) < 48.852


def test_parse_joins_legs_and_offsets_maneuvers():
    leg1 = {"shape": v.encode_polyline6([[48.8, 2.1], [48.81, 2.1]]), "summary": {"length": 1.1, "time": 80},
            "maneuvers": [{"instruction": "Partez", "length": 1.1, "time": 80, "begin_shape_index": 0}]}
    leg2 = {"shape": v.encode_polyline6([[48.81, 2.1], [48.81, 2.12]]), "summary": {"length": 1.5, "time": 100},
            "maneuvers": [{"instruction": "Tournez", "length": 1.5, "time": 100, "begin_shape_index": 0}]}
    r = v.parse_response({"trip": {"legs": [leg1, leg2], "summary": {"length": 2.6, "time": 180}}})
    assert r["length_m"] == 2600.0 and r["duration_s"] == 180
    assert r["geometry"] == [[48.8, 2.1], [48.81, 2.1], [48.81, 2.12]], "shared point kept once"
    assert [m["begin_index"] for m in r["maneuvers"]] == [0, 1]
    with pytest.raises(v.NoRoute):
        v.parse_response({"trip": {"legs": []}})


def test_route_errors():
    def run(handler):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await v.route([[48.8, 2.1], [48.9, 2.2]], "car", client=client)
        return asyncio.run(go())

    with pytest.raises(v.NoRoute):
        run(lambda r: httpx.Response(400, json={"error": "No path"}))
    with pytest.raises(v.RoutingUnavailable):
        run(lambda r: httpx.Response(503))
    sent = {}

    def ok(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"trip": {"legs": [{"shape": v.encode_polyline6([[48.8, 2.1], [48.9, 2.2]]),
                                                            "summary": {"length": 13, "time": 900}}],
                                                  "summary": {"length": 13, "time": 900}}})

    assert run(ok)["length_m"] == 13000 and sent["costing"] == "auto"


E = "evt"


def test_road_closure_validation():
    base = {"id": "c1", "event_id": E, "label": "Pont de la D30", "points": [[48.8, 2.1], [48.801, 2.101]],
            "updated_by": "PCO", "updated_at": "2026-10-10T06:00:00Z"}
    assert routes.validate_closure(base, event_id=E, object_id="c1")["active"] is True
    with pytest.raises(InvalidFeature, match="invalid_points"):
        routes.validate_closure(dict(base, points=[[48.8, 2.1]]), event_id=E, object_id="c1")


def test_itinerary_validation():
    base = {"id": "i1", "event_id": E, "device_id": "TEL-01", "mode": "foot", "destination": {"lat": 48.8, "lon": 2.1,
            "label": "Ferme"}, "geometry": [[48.79, 2.09], [48.8, 2.1]], "eta": "2026-10-10T07:00:00Z",
            "updated_by": "TEL-01", "updated_at": "2026-10-10T06:00:00Z"}
    c = routes.validate_itinerary(base, event_id=E, object_id="i1")
    assert c["status"] == "active" and c["destination"]["label"] == "Ferme" and c["eta"].startswith("2026-10-10T07")
    with pytest.raises(InvalidFeature, match="invalid_mode"):
        routes.validate_itinerary(dict(base, mode="plane"), event_id=E, object_id="i1")

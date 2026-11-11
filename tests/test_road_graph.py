import heapq
from pathlib import Path

from sarcade.routing import graph as g
from sarcade.updates import policy

import pytest

SAMPLE = str(Path(__file__).resolve().parents[1] / "demo" / "osm" / "sample.osm")


def test_modes_from_osm_tags():
    assert g.way_modes({"highway": "residential"}) == (7, 7)
    assert g.way_modes({"highway": "primary", "oneway": "yes"}) == (7, 2), "foot ignores one-way"
    assert g.way_modes({"highway": "track"}) == (6, 6), "track: foot and off-road only"
    assert g.way_modes({"highway": "track", "motor_vehicle": "yes"}) == (7, 7)
    assert g.way_modes({"highway": "motorway"}) == (5, 0), "motorway: one-way, no pedestrians"
    assert g.way_modes({"highway": "service", "access": "private"}) == (0, 0)
    assert g.way_modes({"highway": "footway"}) == (2, 2)
    assert g.way_modes({"highway": "residential", "foot": "no"}) == (5, 5)
    assert g.way_modes({"highway": "secondary", "oneway": "-1"}) == (2, 7)
    assert g.way_modes({"highway": "building"}) == (0, 0)
    assert g.way_modes({"highway": "residential", "junction": "roundabout"}) == (7, 2)


def graph():
    return g.build_graph(g.read_osm(SAMPLE))


def test_vertices_only_at_junctions_and_ends():
    gr = graph()
    assert len(gr.vertices) == 5, "private road dropped, intermediate points kept as geometry"
    assert len(gr.edges) == 6
    assert sum(len(e[7]) for e in gr.edges) == 2


def test_package_round_trip_and_header():
    gr = graph()
    blob = g.serialize(gr, source="sample.osm")
    header, back = g.parse(blob)
    assert header["format"] == "SRG1" and header["vertices"] == 5 and header["edges"] == 6
    assert header["classes"][header["speeds_kmh"]["car"].index(110)] == "motorway"
    assert back.names == gr.names
    for e1, e2 in zip(gr.edges, back.edges):
        assert e1[:2] == e2[:2] and abs(e1[2] - e2[2]) < 0.1 and e1[3:7] == e2[3:7]
    assert g.serialize(gr, built_at=g.datetime(2026, 10, 10, tzinfo=g.UTC)) == \
        g.serialize(gr, built_at=g.datetime(2026, 10, 10, tzinfo=g.UTC)), "reproducible"


def shortest(gr, a, b, mode):
    """Reference Dijkstra on duration, same rules as the app."""
    bit = g.MODE_BITS[mode]
    speeds = {c: s for c, s in g.SPEEDS[mode].items()}
    adj = {}
    for i, (u, v, length, fwd, bwd, cls, _, _) in enumerate(gr.edges):
        kmh = speeds.get(g.CLASSES[cls], 0)
        if not kmh:
            continue
        t = length / (kmh / 3.6)
        if fwd & bit:
            adj.setdefault(u, []).append((v, t))
        if bwd & bit:
            adj.setdefault(v, []).append((u, t))
    dist, queue, prev = {a: 0.0}, [(0.0, a)], {}
    while queue:
        d, u = heapq.heappop(queue)
        if u == b:
            path = [b]
            while path[-1] != a:
                path.append(prev[path[-1]])
            return d, path[::-1]
        for v, t in adj.get(u, []):
            if d + t < dist.get(v, float("inf")):
                dist[v], prev[v] = d + t, u
                heapq.heappush(queue, (d + t, v))
    return None, []


def test_reference_itineraries():
    gr = graph()
    v = {(round(lat, 4), round(lon, 4)): i for i, (lat, lon) in enumerate(gr.vertices)}
    n1, n2, n3, n4, n5 = v[(48.8, 2.1)], v[(48.8, 2.11)], v[(48.8, 2.12)], v[(48.81, 2.12)], v[(48.81, 2.1)]
    assert shortest(gr, n4, n3, "car")[1] == [n4, n2, n3], "D30 is one-way"
    assert shortest(gr, n3, n4, "car")[1] == [n3, n4]
    assert shortest(gr, n1, n5, "car")[0] is None, "the forest track is closed to cars"
    assert shortest(gr, n1, n5, "offroad")[1] == [n1, n5]
    assert shortest(gr, n5, n4, "foot")[1] == [n5, n4], "the footpath"


def test_navigation_setting():
    s = policy.validate({"navigation": {"app": "organic_maps", "hide_tracking_apps": True}})
    assert s["navigation"] == {"app": "organic_maps", "hide_tracking_apps": True}
    assert policy.validate({})["navigation"]["app"] == "operator"
    with pytest.raises(policy.InvalidSettings, match="invalid_navigation_app"):
        policy.validate({"navigation": {"app": "mapquest"}})
    with pytest.raises(policy.InvalidSettings, match="navigation_app_hidden"):
        policy.validate({"navigation": {"app": "waze", "hide_tracking_apps": True}})


# --- Variants of the server itinerary (decision of 10 Oct 2026) ------------------

def test_variants_requested_only_between_two_points():
    from sarcade.routing import valhalla
    body = valhalla.build_request([[48.8, 2.1], [48.9, 2.2]], "car", alternatives=5)
    assert body["alternates"] == 2
    assert "alternates" not in valhalla.build_request([[48.8, 2.1], [48.85, 2.1], [48.9, 2.2]], "car", alternatives=2)
    assert "alternates" not in valhalla.build_request([[48.8, 2.1], [48.9, 2.2]], "car")


def test_near_identical_variants_dropped():
    from sarcade.routing import valhalla
    main = {"geometry": [[48.80, 2.10], [48.90, 2.10], [48.90, 2.20]]}
    same = {"geometry": [[48.80, 2.1001], [48.90, 2.1001], [48.90, 2.20]]}
    other = {"geometry": [[48.80, 2.10], [48.80, 2.20], [48.90, 2.20]]}
    third = {"geometry": [[48.80, 2.10], [48.85, 2.15], [48.90, 2.20]]}
    fourth = {"geometry": [[48.80, 2.10], [48.70, 2.15], [48.90, 2.20]]}
    assert valhalla.shared_share(same["geometry"], main["geometry"]) > 0.95
    assert valhalla.shared_share(other["geometry"], main["geometry"]) < 0.1
    kept = valhalla.distinct_alternatives(main, [same, other, dict(other), third, fourth])
    assert kept == [other, third]

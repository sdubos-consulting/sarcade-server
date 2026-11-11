"""Integration check of navigation level 2: itinerary computation, roads
closed by the PCO, legs of routes along paths, shared itineraries.

Needs the Valhalla stand-in of the demo stack (demo/services_stub.py).
Usage: SARCADE_URL=http://localhost:8000 python demo/verify_navigation.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=30)
T0 = datetime.now(timezone.utc).replace(microsecond=0)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


event = c.post(f"{API}/events", json={"name": "CI navigation", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"


def sync(object_type, payload, action="create"):
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": payload["id"],
                                      "object_type": object_type, "action": action, "client_time": iso(T0),
                                      "payload": payload}])
    r.raise_for_status()
    return r.json()[0]["status"]


check(c.get(f"{API}/routing/status").json()["available"], "routing engine reachable")
pts = [[48.80, 2.10], [48.82, 2.13]]
r = c.post(f"{API}/routing", json={"event_id": event, "points": pts, "mode": "car"})
check(r.status_code == 200 and r.json()["geometry"][0] == pts[0] and r.json()["maneuvers"], "itinerary computed")
first_length = r.json()["length_m"]
check(c.post(f"{API}/routing", json={"points": [[30.0, 2.0], [30.1, 2.1]], "mode": "foot"}).status_code == 422,
      "no route: explicit answer")
r = c.post(f"{API}/routing", json={"event_id": event, "points": pts, "mode": "car", "alternatives": 2}).json()
alts = r.get("alternatives", [])
check(len(alts) == 1 and alts[0]["geometry"][1] == [pts[0][0], pts[1][1]] and alts[0]["mode"] == "car",
      "one distinct variant proposed, the near-identical one dropped")
check("alternatives" not in c.post(f"{API}/routing", json={"points": pts, "mode": "car"}).json(),
      "no variants unless asked")

closure = {"id": str(uuid.uuid4()), "event_id": event, "label": "Pont inondé", "points": [[48.81, 2.11], [48.811, 2.112]],
           "created_by": "PCO", "updated_by": "PCO", "updated_at": iso(T0)}
check(sync("road_closure", dict(closure, id=str(uuid.uuid4()), created_by="TEL-01", updated_by="TEL-01"))
      == "rejected", "only the PCO closes a road")
check(sync("road_closure", closure) == "accepted", "the PCO closes a road")
r = c.post(f"{API}/routing", json={"event_id": event, "points": pts, "mode": "car"}).json()
check(r["avoided_closures"] == 1 and r["length_m"] > first_length, "itineraries avoid the closed road")

rid, w1, w2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
sync("route", {"id": rid, "event_id": event, "name": "Approche", "point_order": [w1, w2], "profile": "foot",
               "default_leg_mode": "paths", "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": iso(T0)})
sync("route_waypoint", {"id": w1, "event_id": event, "route_id": rid, "name": "Départ", "type": "start", "lat": 48.80,
                        "lon": 2.10, "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": iso(T0)})
sync("route_waypoint", {"id": w2, "event_id": event, "route_id": rid, "name": "Ferme", "type": "finish", "lat": 48.82,
                        "lon": 2.13, "leg_mode": "paths", "leg_needs_routing": True, "created_by": "TEL-01",
                        "updated_by": "TEL-01", "updated_at": iso(T0)})
check(c.post(f"{E}/routes/{rid}/legs").json() == {"computed": 1, "no_route": 0}, "leg along paths computed")
route = c.get(f"{E}/routes").json()[0]
leg = route["waypoints"][1]
check(leg["leg_geometry"] and leg["leg_needs_routing"] is False, "computed path stored in the waypoint")
check(route["total_length_m"] > 3000, "route length follows the path")
changes = c.get(f"{E}/sync/changes").json()["changes"]
check(any(x["object_id"] == w2 and x["payload"].get("leg_geometry") for x in changes),
      "computed leg published in the change feed for offline clients")

iid = str(uuid.uuid4())
itinerary = {"id": iid, "event_id": event, "device_id": "TEL-01", "mode": "car",
             "destination": {"lat": 48.82, "lon": 2.13, "label": "Ferme"}, "geometry": pts,
             "eta": iso(T0 + timedelta(minutes=12)), "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": iso(T0)}
check(sync("itinerary", itinerary) == "accepted", "itinerary shared with the PCO")
check(c.get(f"{E}/itineraries").json()[0]["eta"], "the PCO sees the estimated arrival")
check(sync("itinerary", dict(itinerary, status="arrived", updated_at=iso(T0 + timedelta(seconds=30))), "update")
      == "accepted", "arrival recorded")
log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
for needle in ("Route coupée : « Pont inondé »", "Départ de TEL-01 vers « Ferme »", "Arrivée de TEL-01 à « Ferme »"):
    check(needle in log, f"logbook: {needle}")
print("navigation: all checks passed")

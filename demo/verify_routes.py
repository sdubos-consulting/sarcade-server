"""Integration check of routes with waypoints: creation, waypoints synced
one by one, assignment to a team, rights, automatic urgent message to the
team, concurrent order change, passages, report, GPX export.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_routes.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=10)
T0 = datetime.now(timezone.utc).replace(microsecond=0)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


event = c.post(f"{API}/events", json={"name": "CI routes", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"
team = c.post(f"{E}/teams", json={"name": "Équipe 1"}).json()["id"]


def sync(*ops):
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": o["id"],
                                      "object_type": t, "action": a, "client_time": iso(T0), "payload": o}
                                     for t, a, o in ops])
    r.raise_for_status()
    return [x["status"] for x in r.json()]


rid = str(uuid.uuid4())
wids = [str(uuid.uuid4()) for _ in range(3)]


def route(at, by="TEL-01", **kw):
    p = {"id": rid, "event_id": event, "name": "Secteur nord", "point_order": wids, "default_leg_mode": "straight",
         "created_by": "TEL-01", "updated_by": by, "updated_at": iso(at)}
    p.update(kw)
    return p


def wp(i, at, by="TEL-01", **kw):
    p = {"id": wids[i], "event_id": event, "route_id": rid, "name": ["Départ", "CP1", "Arrivée"][i],
         "type": ["start", "checkpoint", "finish"][i], "lat": 48.80 + i * 0.01, "lon": 2.10,
         "created_by": "TEL-01", "updated_by": by, "updated_at": iso(at)}
    p.update(kw)
    return p


statuses = sync(("route", "create", route(T0)), *[("route_waypoint", "create", wp(i, T0)) for i in range(3)])
check(statuses == ["accepted"] * 4, "route and its three waypoints synchronised")
view = c.get(f"{E}/routes").json()[0]
check([w["name"] for w in view["waypoints"]] == ["Départ", "CP1", "Arrivée"], "waypoints in route order")
check(2200 < view["total_length_m"] < 2250, "total length computed (about 2.2 km)")
check(view["waypoints"][1]["radius_m"] == 50.0, "approach radius 50 m by default")

# Two people edit two different points: both kept (one object per point).
s = sync(("route_waypoint", "update", wp(0, T0 + timedelta(seconds=5), by="TEL-01", comment="Parking")),
         ("route_waypoint", "update", wp(2, T0 + timedelta(seconds=5), by="TEL-02", comment="Pont")))
check(s == ["accepted", "accepted"], "edits of two different points do not overwrite each other")

def messages():
    return [m for m in c.get(f"{E}/messages").json() if m["sender_id"] == "SARCADE"]

check(sync(("route", "update", route(T0 + timedelta(seconds=10), assigned_team_id=team, status="active")))
      == ["accepted"], "route assigned to the team")
msgs = messages()
check(len(msgs) == 1 and msgs[0]["priority"] == "urgent" and "affectée" in msgs[0]["body"],
      "urgent message to the team on assignment")
team_group = next(g for g in c.get(f"{E}/groups").json() if g.get("team_id") == team)["id"]
check(msgs[0]["recipient_ids"] == [f"group:{team_group}"], "sent to the team group")

check(sync(("route_waypoint", "update", wp(1, T0 + timedelta(seconds=20), by="TEL-09", lat=48.9)))
      == ["rejected"], "another operator cannot change an assigned route")
check(sync(("route_waypoint", "update", wp(1, T0 + timedelta(seconds=20), by="PCO", lat=48.815)))
      == ["accepted"], "the PCO moves a point of the assigned route")
check("point « CP1 » déplacé" in messages()[-1]["body"], "the team is warned the point moved")

# Concurrent order change: client edited from an older order.
reordered = [wids[0], wids[2], wids[1]]
sync(("route", "update", route(T0 + timedelta(seconds=30), by="PCO", assigned_team_id=team, status="active",
                               point_order=reordered)))
s = sync(("route", "update", route(T0 + timedelta(seconds=35), by="TEL-01", assigned_team_id=team, status="active",
                                   point_order=[wids[1], wids[0], wids[2]], base_point_order=wids)))
check(s == ["accepted"], "last order change wins")
log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
check(any("modifié en même temps" in x for x in log), "the PCO is warned of the concurrent order change")

pid = str(uuid.uuid4())
passage = {"id": pid, "event_id": event, "route_id": rid, "waypoint_id": wids[1], "team_id": team,
           "device_id": "TEL-01", "mode": "auto", "time": iso(T0 + timedelta(seconds=40)),
           "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": iso(T0 + timedelta(seconds=40))}
check(sync(("route_passage", "create", passage)) == ["accepted"], "automatic passage recorded")
view = c.get(f"{E}/routes").json()[0]
check(len(view["passages"]) == 1, "passage listed with the route")
cancel = dict(passage, cancelled=True, mode="manual", updated_at=iso(T0 + timedelta(seconds=50)))
check(sync(("route_passage", "update", cancel)) == ["accepted"], "passage cancelled manually")

r = c.post(f"{E}/routes/{rid}/reports", json={"actor_id": "TEL-01", "waypoint_id": wids[2], "reason": "Pont fermé"})
check(r.status_code == 201, "the team reports an impracticable point")
log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
for needle in ("Route créée : « Secteur nord »", "Passage au point « CP1 »", "Passage annulé", "signalé impraticable"):
    check(any(needle in x for x in log), f"logbook: {needle}")

g = c.get(f"{E}/routes/{rid}.gpx")
check(g.status_code == 200 and g.text.count("<rtept") == 3 and "Secteur nord" in g.text, "GPX export")
print("routes: all checks passed")

"""Integration check of the points of interest (29 Oct 2026): created with
author and colour, renamed by the author, another operator refused, deleted
by the PCO, the deletion in the logbook and in the change feed, gone from
the list.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_pois.py
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=10)
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check(cond, msg):
    if not cond:
        print(f"::error::FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


event = c.post(f"{API}/events", json={"name": "CI POI", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"
pid = str(uuid.uuid4())


def sync(action, payload):
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": pid,
                                      "object_type": "poi", "action": action, "client_time": NOW, "payload": payload}])
    r.raise_for_status()
    return r.json()[0]["status"]


poi = {"id": pid, "event_id": event, "kind": "designated", "label": "Point haut", "lat": 48.82, "lon": 1.91,
       "created_at": NOW, "created_by": "TEL-02", "color": 0xFF3DD6F5, "icon": "gathering"}
check(sync("create", poi) == "accepted", "TEL-02 creates a point")
listed = {p["id"]: p for p in c.get(f"{E}/pois").json()}
check(listed[pid]["created_by"] == "TEL-02" and listed[pid]["color"] == 0xFF3DD6F5, "author and colour kept")
check(listed[pid]["icon"] == "gathering", "icon kept")
check(sync("update", {"id": pid, "icon": "skull", "updated_by": "TEL-02"}) == "rejected", "unknown icon refused")
check(sync("update", {"id": pid, "icon": "helipad", "updated_by": "TEL-02"}) == "accepted", "TEL-02 changes the icon")
check({p["id"]: p for p in c.get(f"{E}/pois").json()}[pid]["icon"] == "helipad", "new icon listed")
check(sync("update", {"id": pid, "label": "Gué", "updated_by": "TEL-03"}) == "rejected", "TEL-03 cannot rename it")
check(sync("update", {"id": pid, "label": "Gué", "updated_by": "TEL-02"}) == "accepted", "TEL-02 renames it")
check({p["id"]: p for p in c.get(f"{E}/pois").json()}[pid]["label"] == "Gué", "new name listed")
check(sync("delete", {"id": pid, "updated_by": "TEL-03"}) == "rejected", "TEL-03 cannot delete it")
check(sync("delete", {"id": pid, "updated_by": "PCO"}) == "accepted", "the PCO deletes it")
check(pid not in {p["id"] for p in c.get(f"{E}/pois").json()}, "gone from the list")
feed = c.get(f"{E}/sync/changes").json()["changes"]
check(any(x["object_id"] == pid and x["action"] == "delete" and x["payload"].get("deleted") for x in feed),
      "deletion in the change feed")
log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
check(any("Gué" in s and "supprimé" in s for s in log), "deletion in the logbook")
print("pois: all checks passed")

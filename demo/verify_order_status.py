"""Integration check of lot 27: « Disponible » by default at the first
contact of a device in an event, and the status of the operator that
follows his move orders (accepted → En route, arrived → Sur place,
« Terminé, disponible » → Disponible, cancelled → the status before),
with the reason in the logbook.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_order_status.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=10)
# Answers a little ahead of the server clock (within its 5 min margin), so
# that they are always newer than the statuses set « now » by the server.
T0 = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=20)


def check(cond, msg):
    if not cond:
        print(f"::error::FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


event = c.post(f"{API}/events", json={"name": "CI statut et ordres", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"


def status(device):
    return {d["device_id"]: d for d in c.get(f"{E}/devices").json()}[device]["operator_status"]


r = c.post(f"{E}/devices/TEL-02/heartbeat", json={"label": "TEL-02", "pending_count": 0})
check(r.status_code == 200 and status("TEL-02") == "disponible", "a new operator is « Disponible »")
c.put(f"{E}/devices/TEL-02/status", json={"status": "pause"})
c.post(f"{E}/devices/TEL-02/heartbeat", json={"pending_count": 0})
check(status("TEL-02") == "pause", "an operator in « Pause » restarting the application stays in « Pause »")


def sync(oid, at, by, status, action="update", **kw):
    p = {"id": oid, "event_id": event, "target_id": "TEL-02", "status": status,
         "destination": {"lat": 48.78, "lon": 2.02, "label": "Point haut"}, "note": "",
         "created_by": "PCO", "updated_by": by, "updated_at": iso(at)}
    p.update(kw)
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": oid,
                                      "object_type": "move_order", "action": action, "client_time": iso(at), "payload": p}])
    r.raise_for_status()
    return r.json()[0]["status"]


o1 = str(uuid.uuid4())
check(sync(o1, T0 + timedelta(seconds=2), "PCO", "sent", "create") == "accepted", "the PCO sends an order")
check(sync(o1, T0 + timedelta(seconds=3), "TEL-02", "accepted") == "accepted" and status("TEL-02") == "en_route",
      "accepted → « En route »")
check(sync(o1, T0 + timedelta(seconds=4), "TEL-02", "arrived") == "accepted" and status("TEL-02") == "sur_place",
      "arrived → « Sur place »")
check(sync(o1, T0 + timedelta(seconds=5), "TEL-02", "arrived", finished_at=iso(T0 + timedelta(seconds=5))) == "accepted"
      and status("TEL-02") == "disponible", "« Terminé, disponible » → « Disponible »")
listed = {o["id"]: o for o in c.get(f"{E}/move-orders").json()}
check(listed[o1]["status"] == "arrived" and listed[o1]["finished_at"], "the order stays arrived, marked finished")

c.put(f"{E}/devices/TEL-02/status", json={"status": "pause", "at": iso(T0 + timedelta(seconds=6))})
o2 = str(uuid.uuid4())
sync(o2, T0 + timedelta(seconds=7), "PCO", "sent", "create")
sync(o2, T0 + timedelta(seconds=8), "TEL-02", "accepted")
check(sync(o2, T0 + timedelta(seconds=9), "PCO", "cancelled") == "accepted" and status("TEL-02") == "pause",
      "cancelled by the PCO → the status before the order")

log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
for line in ("Statut de TEL-02 : Disponible, par défaut", "Statut de TEL-02 : En route, ordre accepté",
             "Statut de TEL-02 : Sur place, arrivée sur zone", "Statut de TEL-02 : Disponible, mission terminée",
             "Statut de TEL-02 : Pause, ordre annulé"):
    check(line in log, f"logbook: {line}")
print("order status: all checks passed")

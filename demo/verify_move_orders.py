"""Integration check of the move orders (29 Oct 2026): the PCO sends an
operator to a point, the operator gets an immediate message, answers
(accepted, arrived), another operator cannot answer for him, refusal and
cancellation warn the other side, the logbook keeps everything.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_move_orders.py
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
        print(f"::error::FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


event = c.post(f"{API}/events", json={"name": "CI ordres", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"


def sync(o, action="create"):
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": o["id"],
                                      "object_type": "move_order", "action": action, "client_time": iso(T0), "payload": o}])
    r.raise_for_status()
    return r.json()[0]["status"]


def order(oid, at, by="PCO", **kw):
    p = {"id": oid, "event_id": event, "target_id": "TEL-02", "status": "sent",
         "destination": {"lat": 48.78, "lon": 2.02, "label": "Point haut"}, "note": "Prendre la Yagi",
         "created_by": "PCO", "updated_by": by, "updated_at": iso(at)}
    p.update(kw)
    return p


def messages(to):
    return [m for m in c.get(f"{E}/messages").json() if to in (m.get("recipient_ids") or [])]


o1 = str(uuid.uuid4())
check(sync(order(o1, T0, by="TEL-03", created_by="TEL-03")) == "rejected", "an operator cannot give an order")
check(sync(order(o1, T0)) == "accepted", "the PCO sends TEL-02 to a point")
m = messages("TEL-02")
check(m and m[-1]["priority"] == "immediate" and "Point haut" in m[-1]["body"], "TEL-02 gets an immediate message")
check(sync(order(o1, T0 + timedelta(seconds=1), by="TEL-03", status="accepted"), "update") == "rejected",
      "another operator cannot answer for TEL-02")
check(sync(order(o1, T0 + timedelta(seconds=2), by="TEL-02", status="accepted"), "update") == "accepted", "TEL-02 accepts")
# Estimated arrival time (lot 24): set by TEL-02 while the order is accepted.
ETA = f"{E}/move-orders/{o1}/eta"
eta = iso(T0 + timedelta(minutes=15))
r = c.put(ETA, json={"device_id": "TEL-02", "eta": eta, "remaining_m": 6500})
check(r.status_code == 200 and r.json()["eta"] and r.json()["eta_remaining_m"] == 6500, "TEL-02 sends its arrival time")
check(c.put(ETA, json={"device_id": "TEL-03", "eta": eta}).status_code == 403, "another operator cannot send it")
listed = {o["id"]: o for o in c.get(f"{E}/move-orders").json()}
check(listed[o1]["eta"] == r.json()["eta"] and listed[o1]["eta_by"] == "TEL-02", "the order is listed with its estimate")
check(sync(order(o1, T0 + timedelta(seconds=3), by="TEL-02", status="arrived"), "update") == "accepted", "TEL-02 arrives")
listed = {o["id"]: o for o in c.get(f"{E}/move-orders").json()}
check("eta" not in listed[o1], "the estimate is cleared at the arrival")
check(c.put(ETA, json={"device_id": "TEL-02", "eta": eta}).status_code == 409, "no estimate once arrived")
check(any("est arrivé" in x["body"] for x in messages("PCO")), "the PCO is told of the arrival")
listed = {o["id"]: o for o in c.get(f"{E}/move-orders").json()}
check(listed[o1]["status"] == "arrived", "the order is listed with its answer")

o2 = str(uuid.uuid4())
sync(order(o2, T0 + timedelta(seconds=4)))
check(sync(order(o2, T0 + timedelta(seconds=5), by="TEL-02", status="refused"), "update") == "accepted", "TEL-02 refuses")
check(any("refusé" in x["body"] and x["priority"] == "urgent" for x in messages("PCO")), "the PCO gets an urgent refusal")
o3 = str(uuid.uuid4())
sync(order(o3, T0 + timedelta(seconds=6)))
check(sync(order(o3, T0 + timedelta(seconds=7), status="cancelled"), "update") == "accepted", "the PCO cancels")
check(any("annulé" in x["body"] for x in messages("TEL-02")), "TEL-02 is told of the cancellation")
log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
check(any("Ordre de déplacement : TEL-02" in s for s in log) and any("a accepté" in s for s in log), "logbook kept")
print("move orders: all checks passed")

"""Integration check of the status in one touch (card « Android Auto :
statut en un appui », lot 24, 3 Nov 2026): « Mon statut » sent by REST or
by the sync, kept per device with its time, an older status sent late
ignored, carried by the latest positions and the devices of the PCO,
written in the logbook, migration 0028 applied on PostgreSQL.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_operator_status.py
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


event = c.post(f"{API}/events", json={"name": "CI statut", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"

r = c.post(f"{API}/positions", json={"id": str(uuid.uuid4()), "event_id": event, "device_id": "CAR-1",
                                     "lat": 48.80, "lon": 2.03, "time": iso(T0)})
check(r.status_code == 202, "a position of CAR-1")
r = c.put(f"{E}/devices/CAR-1/status", json={"status": "en_route", "at": iso(T0)})
check(r.status_code == 200 and r.json()["operator_status"] == "en_route" and r.json()["changed"],
      "CAR-1 is « En route »")
check(c.put(f"{E}/devices/CAR-1/status", json={"status": "busy"}).status_code == 422, "unknown status refused")

latest = {p["device_id"]: p for p in c.get(f"{E}/positions/latest").json()}
check(latest["CAR-1"]["operator_status"] == "en_route" and latest["CAR-1"]["operator_status_at"],
      "the latest position carries the status")
devices = {d["device_id"]: d for d in c.get(f"{E}/devices").json()}
check(devices["CAR-1"]["operator_status"] == "en_route", "the devices of the PCO carry the status")


def sync(op_id, status, at):
    r = c.post(f"{API}/sync", json=[{"operation_id": op_id, "event_id": event, "object_id": "CAR-1",
                                      "object_type": "operator_status", "action": "update", "client_time": iso(at),
                                      "payload": {"device_id": "CAR-1", "status": status, "at": iso(at)}}])
    r.raise_for_status()
    return r.json()[0]["status"]


check(sync("st-1", "sur_place", T0 + timedelta(seconds=30)) == "accepted", "« Sur place » through the sync")
check(sync("st-0", "pause", T0 - timedelta(minutes=1)) == "conflict", "an older status sent late is ignored")
check(c.get(f"{E}/operator-statuses").json()[0]["operator_status"] == "sur_place", "status list")
feed = [x for x in c.get(f"{E}/sync/changes").json()["changes"] if x["object_type"] == "operator_status"]
check(len(feed) == 1 and feed[0]["payload"]["operator_status"] == "sur_place", "change feed: resulting state")

r = c.put(f"{E}/devices/CAR-1/status", json={"status": None, "at": iso(T0 + timedelta(minutes=1))})
check(r.json()["operator_status"] is None, "status removed")
check(c.get(f"{E}/operator-statuses").json() == [], "no status left")

log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
for line in ("Statut de CAR-1 : En route", "Statut de CAR-1 : Sur place", "Statut de CAR-1 retiré"):
    check(line in log, f"logbook: {line}")
print("operator status: all checks passed")

"""Integration check of « Robustesse de la synchronisation » and of the
event list (« Choix de l'événement dans une liste »).

A batch mixing an unknown event, an invalid item and valid items: each item
gets its own status, nothing fails as a whole (incident of 23 October 2026).

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_sync_robustness.py
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
c = httpx.Client(timeout=10)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def op(event, object_type, payload, object_id=None, action="create"):
    oid = object_id or payload.get("id") or str(uuid.uuid4())
    return {"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": oid,
            "object_type": object_type, "action": action, "client_time": iso(datetime.now(timezone.utc)),
            "payload": payload}


now = iso(datetime.now(timezone.utc))
open_name = f"CI liste ouvert {uuid.uuid4().hex[:6]}"
closed_name = f"CI liste clos {uuid.uuid4().hex[:6]}"
event = c.post(f"{BASE}/api/v0.1/events", json={"name": open_name, "kind": "exercise"}).json()["id"]
closed = c.post(f"{BASE}/api/v0.1/events", json={"name": closed_name, "kind": "exercise"}).json()["id"]
c.post(f"{BASE}/api/v0.1/events/{closed}/close", json={"actor_id": "PCO"})

# Event list
listed = c.get(f"{BASE}/api/v0.1/events").json()
names = {e["name"] for e in listed}
check(open_name in names and closed_name not in names, "event list hides closed events")
row = next(e for e in listed if e["id"] == event)
check(set(row) >= {"id", "name", "status", "created_at"} and row["status"] == "draft",
      "event list gives name, status and date")
every = {e["name"] for e in c.get(f"{BASE}/api/v0.1/events", params={"include_closed": "true"}).json()}
check(closed_name in every, "closed events listed on request")

# Batch: unknown event, invalid position, malformed item, valid message and position
pid, mid = str(uuid.uuid4()), str(uuid.uuid4())
batch = [
    op("test1-inconnu", "position", {"id": str(uuid.uuid4()), "event_id": "test1-inconnu", "device_id": "PC-01",
                                     "lat": 48.8, "lon": 2.1, "time": now}),
    op(event, "position", {"id": str(uuid.uuid4()), "event_id": event, "device_id": "PC-01", "time": now}),
    {"operation_id": "mal-forme", "event_id": event},
    op(event, "unknown_type", {"id": "x"}),
    op(event, "message", {"id": mid, "event_id": event, "sender_id": "PC-01", "recipient_ids": [],
                          "priority": "immediate", "body": "Message urgent après des éléments fautifs",
                          "created_at": now}),
    op(event, "position", {"id": pid, "event_id": event, "device_id": "PC-01", "lat": 48.8, "lon": 2.1,
                           "time": now}),
    op(event, "ack", {"id": str(uuid.uuid4()), "event_id": event, "message_id": "inconnu",
                      "actor_id": "PC-01", "status": "read", "time": now}),
]
r = c.post(f"{BASE}/api/v0.1/sync", json=batch)
check(r.status_code == 200, "a batch with faulty items never fails as a whole")
res = r.json()
check(len(res) == len(batch), "one result per item")
statuses = [x["status"] for x in res]
check(statuses[0] == "unknown_event" and "test1-inconnu" in res[0]["reason"],
      "unknown event reported with a readable reason")
check(statuses[1] == "rejected" and res[1]["reason"], "invalid position rejected alone")
check(statuses[2] == "rejected" and res[2]["operation_id"] == "mal-forme", "malformed item rejected alone")
check(statuses[3] == "rejected", "unknown type rejected")
check(statuses[4] == "accepted" and statuses[5] == "accepted", "valid items after faulty ones accepted")
check(statuses[6] == "rejected", "ack of an unknown message rejected")

msgs = {m["id"] for m in c.get(f"{BASE}/api/v0.1/events/{event}/messages").json()}
check(mid in msgs, "urgent message stored")
latest = {p["id"] for p in c.get(f"{BASE}/api/v0.1/events/{event}/positions/latest").json()}
check(pid in latest, "position stored")
changes = c.get(f"{BASE}/api/v0.1/events/{event}/sync/changes").json()["changes"]
check(all(ch["object_type"] != "ack" for ch in changes), "rejected ack absent from the change feed")

# Duplicate: the same batch again
again = c.post(f"{BASE}/api/v0.1/sync", json=[batch[4], batch[5]]).json()
check([x["status"] for x in again] == ["duplicate", "duplicate"], "resending is idempotent")

# Photo upload to an unknown event
up = c.post(f"{BASE}/api/v0.1/events/test1-inconnu/files", data={"sender_id": "PC-01"},
            files={"file": ("p.jpg", b"jpeg", "image/jpeg")})
check(up.status_code == 404 and up.json()["detail"] == "unknown_event", "photo to an unknown event: unknown_event")

# Unsent items reported to the PCO
c.post(f"{BASE}/api/v0.1/events/{event}/devices/PC-01/heartbeat", json={"pending_count": 5})
dev = {d["device_id"]: d for d in c.get(f"{BASE}/api/v0.1/events/{event}/devices").json()}
check(dev["PC-01"]["pending_count"] == 5, "unsent items shown in the PCO view")
print("sync robustness: all checks passed")

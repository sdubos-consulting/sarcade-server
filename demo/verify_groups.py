"""Integration check of communication groups: default groups, team groups,
operators' groups (ADR-001), managers' rights, listen-only enforcement.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_groups.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
c = httpx.Client(timeout=10)
T0 = datetime.now(timezone.utc).replace(microsecond=0)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


event = c.post(f"{BASE}/api/v0.1/events", json={"name": "CI groupes", "kind": "exercise"}).json()["id"]
E = f"{BASE}/api/v0.1/events/{event}"


def groups():
    return {g["name"]: g for g in c.get(f"{E}/groups").json()}


def sync(object_type, object_id, payload, action="create"):
    r = c.post(f"{BASE}/api/v0.1/sync", json=[{
        "operation_id": str(uuid.uuid4()), "event_id": event, "object_id": object_id,
        "object_type": object_type, "action": action, "client_time": iso(T0), "payload": payload}])
    r.raise_for_status()
    return r.json()[0]["status"]


g = groups()
check(list(g) == ["Tous", "Diffusion PCO"], "base channels created with the event")
check(c.post(f"{E}/groups/defaults?template=adrasec").json()["created"] == 4, "ADRASEC model added")
g = groups()
check(list(g) == ["Tous", "Diffusion PCO", "PCO", "Équipes terrain", "Transmissions", "Logistique"],
      "ADRASEC model channels present")
check(g["Diffusion PCO"]["mode"] == "listen_only", "Diffusion PCO is listen-only")
check(c.post(f"{E}/groups/defaults").json()["created"] == 0, "default groups are not duplicated")

team = c.post(f"{E}/teams", json={"name": "Équipe 1"}).json()
check(groups()["Équipe 1"]["team_id"] == team["id"], "a group is created for each team")

gid = str(uuid.uuid4())
group = {"id": gid, "event_id": event, "name": "Recherche nord", "mode": "discussion", "kind": "custom",
         "members": ["TEL-01", "TEL-02"], "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": iso(T0)}
check(sync("comm_group", gid, group) == "accepted", "an operator creates a group")
check(groups()["Recherche nord"]["managers"] == ["TEL-01"], "the creator manages it")
other = dict(group, name="Piraté", updated_by="TEL-02", updated_at=iso(T0 + timedelta(seconds=5)))
check(sync("comm_group", gid, other, "update") == "rejected", "a non-manager cannot change it")
stale = dict(group, name="Ancien", updated_at=iso(T0 - timedelta(minutes=1)))
check(sync("comm_group", gid, stale, "update") == "conflict", "an older change loses (ADR-001)")
archived = dict(group, archived=True, updated_by="PCO", updated_at=iso(T0 + timedelta(seconds=10)))
check(sync("comm_group", gid, archived, "update") == "accepted", "the PCO archives an operator's group")
check(groups()["Recherche nord"]["archived"] is True, "the PCO still sees the archived group")

diffusion = g["Diffusion PCO"]["id"]


def message(sender, recipient):
    mid = str(uuid.uuid4())
    return sync("message", mid, {"id": mid, "event_id": event, "sender_id": sender,
                                 "recipient_ids": [f"group:{recipient}"], "priority": "urgent",
                                 "body": "Point de situation", "created_at": iso(T0)})


check(message("PCO", diffusion) == "accepted", "the PCO writes in Diffusion PCO")
check(message("TEL-01", diffusion) == "rejected", "an operator cannot write in a listen-only group")
check(message("TEL-01", g["Tous"]["id"]) == "accepted", "an operator writes in Tous")
check(message("TEL-01", gid) == "rejected", "nobody writes in an archived group")

log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
for line in ("Canaux de base créés (« Tous », « Diffusion PCO »)", "Groupe de communication créé : « Recherche nord »",
             "Groupe de communication archivé : « Recherche nord »"):
    check(line in log, f"logbook: {line}")
print("groups: all checks passed")

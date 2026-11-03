"""Integration check of events and the directory (lot 26): starting
organisations of the migration, event attached to an organising group and
visible to the invited groups only, PCO of the organising group,
communication group made from an organisation group, transition period
(devices without account, PCO by its name) and its end.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_event_access.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
ADMIN = {"Authorization": f"Bearer {os.getenv('SARCADE_ADMIN_TOKEN', 'demo-admin-token')}"}
c = httpx.Client(timeout=30)
TAG = uuid.uuid4().hex[:6]


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def login(email, device_id):
    check(c.post(f"{API}/auth/code/request", json={"email": email}).status_code == 202, f"code for {email}")
    rows = c.get(f"{API}/admin/security-journal", headers=ADMIN, params={"category": "auth"}).json()
    code = next(r["details"]["code"] for r in rows
                if r["action"] == "login_code_simulated" and r["details"].get("email") == email)
    r = c.post(f"{API}/auth/code/verify", json={"email": email, "code": code, "device_id": device_id})
    check(r.status_code == 200, f"{email} connected")
    return {"Authorization": f"Bearer {r.json()['token']}"}


def event_names(headers=None):
    return {e["name"] for e in c.get(f"{API}/events", headers=headers or {}).json()}


groups = {g["short_name"]: g for g in c.get(f"{API}/admin/groups", headers=ADMIN).json()}
check({"FNRASEC", "ADRASEC 78", "PCO ADRASEC 78", "VCMB", "LGA"} <= set(groups), "starting organisations created")
check(groups["ADRASEC 78"]["parent_id"] == groups["FNRASEC"]["id"], "FNRASEC › ADRASEC 78")
habs = c.get(f"{API}/admin/groups/{groups['ADRASEC 78']['id']}/habilitations", headers=ADMIN).json()
check([h["right"] for h in habs] == ["module.adrasec"], "module ADRASEC given to ADRASEC 78")
habs = c.get(f"{API}/admin/groups/{groups['PCO ADRASEC 78']['id']}/habilitations", headers=ADMIN).json()
check(habs[0]["right"] == "event_organizer" and habs[0]["scope_name"] == "ADRASEC 78",
      "PCO ADRASEC 78 organise the events of ADRASEC 78")

state = c.get(f"{API}/transition").json()
check(state["active"] is True and state["pco_by_name"] is True, "transition running (no end date)")
anon = c.post(f"{API}/events", json={"name": f"Sans compte {TAG}", "kind": "exercise"})
check(anon.status_code == 201, "device without account creates an event during the transition")
check(c.get(f"{API}/events/{anon.json()['id']}").json()["organizer_group_id"] == groups["ADRASEC 78"]["id"],
      "event attached to ADRASEC 78 by default")

c.post(f"{API}/admin/groups/{groups['PCO ADRASEC 78']['id']}/members", json={"email": f"chef-{TAG}@a78.fr"},
       headers=ADMIN)
c.post(f"{API}/admin/groups/{groups['VCMB']['id']}/members", json={"email": f"sig-{TAG}@vcmb.fr"}, headers=ADMIN)
HP = login(f"chef-{TAG}@a78.fr", f"PCO-{TAG}")
HV = login(f"sig-{TAG}@vcmb.fr", f"TEL-{TAG}")
r = c.post(f"{API}/events", json={"name": f"Exercice {TAG}", "kind": "exercise"}, headers=HP)
check(r.status_code == 201, "PCO of ADRASEC 78 creates an event")
EID = r.json()["id"]
check(f"Exercice {TAG}" in event_names(HP) and f"Exercice {TAG}" not in event_names(HV),
      "event visible to ADRASEC 78 only")
check(c.get(f"{API}/events/{EID}", headers=HV).status_code == 404, "another organisation gets « not found »")
r = c.post(f"{API}/events", json={"name": "x", "kind": "race", "organizer_group_id": groups["VCMB"]["id"]},
           headers=HP)
check(r.status_code == 403, "no event for an organisation one does not organise")

r = c.put(f"{API}/events/{EID}/access", json={"invited_group_ids": [groups["VCMB"]["id"]]}, headers=HP)
check(r.status_code == 200 and r.json()["invited_groups"][0]["short_name"] == "VCMB", "VCMB invited by the PCO")
check(f"Exercice {TAG}" in event_names(HV), "invited group sees the event")
check(c.put(f"{API}/events/{EID}/access", json={"invited_group_ids": []}, headers=HV).status_code == 403,
      "an invited member is not the PCO")
check(c.post(f"{API}/events/{EID}/close", json={"actor_id": f"TEL-{TAG}"}, headers=HV).status_code == 403,
      "an invited member does not close the event")

r = c.post(f"{API}/events/{EID}/groups/from-directory", json={"group_id": groups["VCMB"]["id"],
                                                               "name": f"Signaleurs {TAG}"}, headers=HP)
check(r.status_code == 201 and f"TEL-{TAG}" in r.json()["members"], "communication group made from VCMB")
check(any(g["name"] == f"Signaleurs {TAG}" for g in c.get(f"{API}/events/{EID}/groups", headers=HP).json()),
      "communication group listed in the event")

# End of the transition, then back to no end date (the next checks of the CI
# use devices without account).
past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
r = c.put(f"{API}/admin/transition", json={"until": past}, headers=ADMIN)
check(r.status_code == 200 and r.json()["active"] is False, "transition ended by the administrator")
check(f"Sans compte {TAG}" not in event_names(), "device without account: Core only, events not visible")
check(c.post(f"{API}/events", json={"name": "x", "kind": "exercise"}).status_code == 403,
      "device without account no longer PCO")
check(c.get(f"{API}/me", headers=HP).json()["transition"]["pco_by_name"] is False, "/me gives the transition state")
r = c.put(f"{API}/admin/transition", json={"until": None}, headers=ADMIN)
check(r.json()["active"] is True, "transition back (no end date)")
check(c.get(f"{API}/admin/security-journal/verify", headers=ADMIN).json()["ok"], "security journal chain intact")
print("event access: all checks passed")

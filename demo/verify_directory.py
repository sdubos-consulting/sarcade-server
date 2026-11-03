"""Integration check of the directory (lot 26): organisation groups
(attachment and nested membership), cycle refused with its path, depth,
invitation before the account, CSV import, habilitations with « Pourquoi ce
droit ? », module crossed with the licence of the organisation, rights in
/me, last administrator kept, every change in the security journal.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_directory.py
"""
import json
import os
from pathlib import Path
import sys
import uuid

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


def post(path, body, headers=ADMIN):
    return c.post(f"{API}{path}", json=body, headers=headers)


def login(email):
    check(post("/auth/code/request", {"email": email}, {}).status_code == 202, f"code requested for {email}")
    rows = c.get(f"{API}/admin/security-journal", headers=ADMIN, params={"category": "auth"}).json()
    code = next(r["details"]["code"] for r in rows
                if r["action"] == "login_code_simulated" and r["details"].get("email") == email)
    r = post("/auth/code/verify", {"email": email, "code": code, "device_id": f"TEL-{TAG}"}, {})
    check(r.status_code == 200, f"{email} connected")
    return {"Authorization": f"Bearer {r.json()['token']}"}


groups = c.get(f"{API}/admin/groups", headers=ADMIN).json()
check({"public", "server-admins"} <= {g["id"] for g in groups}, "system groups Public and Administrateurs present")

org = post("/admin/groups", {"short_name": f"ORG-{TAG}", "name": "Organisation de démonstration"}).json()
check(org["organization"] is True, "organisation created (group without parent)")
sub = post("/admin/groups", {"short_name": f"SUB-{TAG}", "parent_id": org["id"]}).json()
check(sub["parent_id"] == org["id"] and sub["member_of"] == [org["id"]], "subgroup attached and member by default")
club = post("/admin/groups", {"short_name": f"CLUB-{TAG}"}).json()
sig = post("/admin/groups", {"short_name": f"SIG-{TAG}", "parent_id": club["id"]}).json()
check(post(f"/admin/groups/{sig['id']}/members", {"group_id": sub["id"]}).status_code == 201,
      "a group invited in a group of another organisation")
r = post(f"/admin/groups/{sub['id']}/members", {"group_id": club["id"]})
check(r.status_code == 409 and r.json()["detail"]["error"] == "cycle"
      and r.json()["detail"]["path"] == [f"CLUB-{TAG}", f"SIG-{TAG}", f"SUB-{TAG}"], "cycle refused with the path")
check("Impossible" in r.json()["detail"]["message"], "readable message of the cycle")

r = post(f"/admin/groups/{org['id']}/habilitations", {"right": "module.adrasec"})
check(r.status_code == 201, "module ADRASEC given to the organisation")
r = post(f"/admin/groups/{sub['id']}/habilitations", {"right": "event_organizer"})
check(r.status_code == 201 and r.json()["scope_group_id"] == sub["id"], "event organiser of the subgroup")
check(post("/admin/groups/public/habilitations", {"right": "module.race"}).status_code == 422,
      "no right on Public")

email = f"benevole-{TAG}@exemple.fr"
r = post(f"/admin/groups/{sub['id']}/members", {"email": email.upper()})
check(r.json()["kind"] == "invitation", "address invited before the account exists")
r = c.post(f"{API}/admin/groups/{sub['id']}/members/import", content=f"email\nun-{TAG}@x.fr\npas une adresse\n",
           headers={**ADMIN, "Content-Type": "text/csv"})
check(r.json()["invited"] == 1 and r.json()["invalid"] == ["pas une adresse"], "CSV import of addresses")

H = login(email)
me = c.get(f"{API}/me", headers=H).json()
names = {g["short_name"] for g in me["groups"]}
check({"Public", f"SUB-{TAG}", f"ORG-{TAG}", f"SIG-{TAG}"} <= names, "invitation became membership, nested groups")
check(me["module_rights"] == [{"module": "adrasec", "licensed": False}] and me["modules"] == [],
      "module without licence of the organisation: not usable")
check(me["can_administer"] is True and me["admin_rights"][0]["right"] == "event_organizer", "rights in /me")
check("pco" in me["roles"], "event organiser = PCO for the existing checks")

why = c.get(f"{API}/admin/rights", params={"email": email}, headers=ADMIN).json()
texts = [x["path_text"] for x in why["rights"]]
check(f"Module ADRASEC ← ORG-{TAG} ← SUB-{TAG} ← {email}" in texts, "« Pourquoi ce droit ? » gives the path")

licence = Path("/demo/licence-test/licence-org.json")
if licence.exists():
    r = c.put(f"{API}/admin/groups/{org['id']}/licence", content=licence.read_bytes(), headers=ADMIN)
    check(r.status_code == 200 and "adrasec" in r.json()["modules"], "licence of the organisation installed")
    check(c.get(f"{API}/me", headers=H).json()["modules"] == ["adrasec"], "module usable with the licence")
else:
    print("skip licence of the organisation (no test licence)")

check(c.get(f"{API}/admin/groups/{club['id']}", headers=H).status_code == 403,
      "an organiser does not see another organisation")
admin_email = f"admin-{TAG}@exemple.fr"
post("/admin/groups/server-admins/members", {"email": admin_email})
HA = login(admin_email)
check(c.get(f"{API}/me", headers=HA).json()["is_server_admin"] is True, "member of Administrateurs = server admin")
admins = c.get(f"{API}/admin/groups/server-admins/members", headers=ADMIN).json()["users"]
if len(admins) == 1:
    r = c.delete(f"{API}/admin/groups/server-admins/members/users/{admins[0]['user_id']}", headers=ADMIN)
    check(r.status_code == 409 and r.json()["detail"]["error"] == "last_admin", "last administrator kept")
rows = c.get(f"{API}/admin/security-journal", headers=ADMIN, params={"category": "rights"}).json()
actions = {r["action"] for r in rows}
check({"member_added", "habilitation_granted", "group_member_added", "invitation_accepted",
       "directory_refused"} <= actions, "changes and refusals in the security journal")
check(c.get(f"{API}/admin/security-journal/verify", headers=ADMIN).json()["ok"], "security journal chain intact")
print("directory: all checks passed")

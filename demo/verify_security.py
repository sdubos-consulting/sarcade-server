"""Integration check of updates and security: admin token, chained security
journal, administration actions journaled, maintenance windows, update plan
never during an active event, minimal client version, client packages
distributed by the local server, forwarding to a SIEM (Pro module).

Needs the demo stack (services stub for the releases API and the SIEM).
Usage: SARCADE_URL=http://localhost:8000 python demo/verify_security.py
"""
import os
import sys
import time
import uuid

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
STUB = os.getenv("SARCADE_STUB_URL", "http://services-stub:8080")
TOKEN = os.getenv("SARCADE_ADMIN_TOKEN", "demo-admin-token")
c = httpx.Client(timeout=30)
ADMIN = {"Authorization": f"Bearer {TOKEN}"}


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


check(c.get(f"{API}/admin/settings").status_code == 401, "administration refused without token")
check(c.get(f"{API}/admin/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401,
      "administration refused with a wrong token")
journal = c.get(f"{API}/admin/security-journal", headers=ADMIN, params={"category": "auth"}).json()
check(journal and journal[0]["action"] == "admin_token" and journal[0]["outcome"] == "failure",
      "refused access journaled")

settings = c.get(f"{API}/admin/settings", headers=ADMIN).json()
check(settings["journal_retention_days"] == 365, "journal kept one year by default")
check(settings["maintenance"]["windows"] == [{"days": [1], "start": "03:00", "end": "05:00"}],
      "maintenance window by default: Tuesday 03:00-05:00")
r = c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"maintenance": {"windows": [
    {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}]}}})
check(r.status_code == 200 and len(r.json()["maintenance"]["windows"][0]["days"]) == 7, "maintenance windows set")
check(c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"journal_retention_days": 5}}).status_code
      == 422, "retention below 30 days refused")

event = c.post(f"{API}/events", json={"name": "CI sécurité", "kind": "exercise"}, headers={"X-Sarcade-Actor": "PCO"}).json()["id"]
c.patch(f"{API}/events/{event}/settings", json={"settings": {"low_bandwidth": True}, "actor_id": "PCO"})
admin = c.get(f"{API}/admin/security-journal", headers=ADMIN, params={"category": "admin"}).json()
actions = [e["action"] for e in admin]
check("event_created" in actions and "event_settings_changed" in actions and "maintenance_changed" in actions,
      "administration actions journaled")

c.post(f"{API}/admin/updates/check", headers=ADMIN)
state = c.get(f"{API}/admin/updates", headers=ADMIN).json()
check(state["available"] == "9.9.0" and state["installed"], "new server version detected")
check(event in state["active_events"] and state["install"] is False and state["reason"] == "active_event",
      "no installation during an active event, even in a maintenance window")
check(c.post(f"{API}/admin/updates/install-now", headers=ADMIN).status_code == 409,
      "« Installer maintenant » refused during an active event")
r = c.post(f"{API}/admin/updates/report", headers=ADMIN, json={"version": "9.9.0", "status": "signature_invalid",
                                                               "detail": "cosign verify failed"})
check(r.status_code == 201, "updater report recorded")
check(c.get(f"{API}/admin/updates", headers=ADMIN).json()["history"][0]["status"] == "signature_invalid",
      "update history shown")

device = f"TEL-{uuid.uuid4().hex[:6]}"
check(c.post(f"{API}/clients/check", json={"device_id": device, "platform": "windows", "app_version": "0.1.0"})
      .json()["status"] == "ok", "client accepted without minimal version")
c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"clients": {"min_version": "0.2.0"}}})
r = c.post(f"{API}/clients/check", json={"device_id": device, "platform": "windows", "app_version": "0.1.0"}).json()
check(r["status"] == "invited" and r["deadline"], "outdated client invited, 2 hours to update")
hb = c.post(f"{API}/events/{event}/devices/{device}/heartbeat", json={"app_version": "0.1.0", "platform": "windows"})
check(hb.json()["client_update"]["status"] == "invited", "invitation also carried by the heartbeat")
pkg = c.put(f"{API}/admin/clients/windows/package", headers=ADMIN, data={"version": "0.2.0"},
            files={"file": ("sarcade-windows-setup.exe", b"MSIX-demo-package", "application/octet-stream")})
check(pkg.status_code == 200 and len(pkg.json()["sha256"]) == 64, "client package uploaded to the local server")
check(c.get(f"{API}/clients/latest").json()["windows"]["version"] == "0.2.0", "latest client versions listed")
check(c.get(f"{API}/clients/windows/package").content == b"MSIX-demo-package", "client package served on the network")
r = c.post(f"{API}/clients/check", json={"device_id": device, "platform": "windows", "app_version": "0.2.0"}).json()
check(r["status"] == "ok", "updated client accepted")
# Windows installer taken from the latest major release (stand-in), lot 25.
check(c.post(f"{API}/admin/clients/windows/fetch").status_code == 401, "GitHub fetch needs the admin token")
fetched = c.post(f"{API}/admin/clients/windows/fetch", headers=ADMIN)
check(fetched.status_code == 200 and fetched.json()["version"] == "0.1.0+960" and fetched.json()["changed"],
      "Windows installer taken from the latest major release, size and SHA-256 checked")
check(c.get(f"{API}/clients/windows/package").content == b"MZ-SARCADE-demo-installer",
      "installer from GitHub served on the network")
check(c.post(f"{API}/admin/clients/windows/fetch", headers=ADMIN).json()["changed"] is False,
      "same installer not downloaded twice")
c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"clients": {"min_version": None}}})
c.patch(f"{API}/admin/settings", headers=ADMIN,
        json={"settings": {"clients": {"releases": {"android": {"version": "0.9.0", "build": 900}}}}})
rel = c.post(f"{API}/clients/check", json={"device_id": "TEL-STORE", "platform": "android", "app_version": "0.1.0"}).json()
check(rel["latest"] == {"version": "0.9.0", "build": 900, "url": None}, "store release given to Android phones")
c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"clients": {"releases": {}}}})
updates = [e["action"] for e in c.get(f"{API}/admin/security-journal", headers=ADMIN,
                                      params={"category": "update"}).json()]
for needle in ("client_invited", "client_updated", "server_update_signature_invalid", "install_now",
               "server_version_available"):
    check(needle in updates, f"journal: {needle}")

v = c.get(f"{API}/admin/security-journal/verify", headers=ADMIN).json()
check(v["ok"] and v["count"] > 10, "chained journal intact")
# SARCADE Pro licence: signed, verified offline, uploaded in the admin tool.
LICENCE = os.getenv("SARCADE_TEST_LICENCE", "/demo/licence-test/licence.json")
check(c.get(f"{API}/admin/licence", headers=ADMIN).json()["status"] == "none", "no licence: Core only")
check(c.put(f"{API}/admin/licence", headers=ADMIN, content=b"not a licence").status_code == 422,
      "unreadable licence refused")
with open(LICENCE, "rb") as fh:
    good = fh.read()
forged = good.replace(b'"siem"', b'"siem", "locate"', 1)
r = c.put(f"{API}/admin/licence", headers=ADMIN, content=forged)
check(r.status_code == 422 and r.json()["detail"]["reason"] == "bad_signature", "modified licence refused")
r = c.put(f"{API}/admin/licence", headers=ADMIN, content=good)
check(r.status_code == 200 and r.json()["status"] == "valid" and r.json()["test"] and r.json()["modules"] == ["siem"],
      "signed test licence installed")
check(c.get(f"{API}/clients/config").json()["pro"] == {"modules": ["siem"], "status": "valid"},
      "Pro modules announced to the applications")
status = c.get(f"{API}/admin/status", headers=ADMIN).json()
check("siem" in status["pro_modules"], "SIEM module enabled (Pro)")
lic_journal = [e["action"] for e in c.get(f"{API}/admin/security-journal", headers=ADMIN,
                                          params={"category": "admin"}).json()]
check("licence_uploaded" in lic_journal and "licence_checked" in lic_journal, "licence journaled")
received = []
for _ in range(45):
    received = c.get(f"{STUB}/siem/received").json()
    if any("update.client_invited" in line for line in received):
        break
    time.sleep(1)
check(any(line.startswith("<") and " sarcade - update.client_invited [sarcade@" in line for line in received),
      "journal forwarded to the SIEM in syslog RFC 5424")
check(not any("CI sécurité" in line for line in received), "no operational content sent to the SIEM")
c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"maintenance": {"windows": [
    {"days": [1], "start": "03:00", "end": "05:00"}]}}})
print("security: all checks passed")

"""Integration check of the server block of « Paramètres › Diagnostic »
(lot 5): state, components, resources read from /proc in the container;
levels, Brevo and /version (lot 28).

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_diagnostics.py
"""
import os
import sys

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=30)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


r = c.get(f"{API}/diagnostics/server")
check(r.status_code == 200, "diagnostics answer (local network, no Access)")
d = r.json()
check(d["status"] in ("ok", "degraded", "down"), f"general state: {d['status']}")
check(d["components"]["database"] == "green", "database green")
check(set(d["components"]) >= {"realtime", "tunnel", "wakeup", "aprs", "weather"}, "every component listed")
check(d["uptime_s"] >= 0 and d["version"], "version and uptime")
res = d["resources"]
check(res["memory"] and 0 < res["memory"]["pct"] <= 100, "memory read from /proc/meminfo")
check(res["disk"] and res["disk"]["total_gb"] > 0, "disk seen from the container")
check(res["cpu_pct"] is None or 0 <= res["cpu_pct"] <= 100, "processor from /proc/stat")
cl = c.get(f"{API}/diagnostics/client").json()
check(cl["ip"] and cl["via_cloudflare"] is False, f"client address seen by the server: {cl['ip']}")

# Lot 28: levels computed by the server, Brevo, /version.
check(d["level"] == "transition", "device without account during the transition: view of before")
adm = c.get(f"{API}/diagnostics/server", headers={"Authorization": "Bearer demo-admin-token"}).json()
check(adm["level"] == "admin" and "resources" in adm, "administrator: everything")
check(adm["mail"]["delivery"] == "simulated" and adm["mail"]["probe"] is None, "Brevo: simulated sending, no test")
check(adm["build"]["reference"] and isinstance(adm["addresses"], list), "versions and addresses for the administrator")
r = c.post(f"{API}/admin/mail/test", headers={"Authorization": "Bearer demo-admin-token"})
check(r.status_code == 422, "test email needs the address of an administrator")
v = c.get(f"{BASE}/version")
check(v.status_code == 200 and v.json()["service"] == "sarcade-server", f"/version: {v.json().get('reference')}")
check(v.headers.get("cache-control") == "no-store", "/version never kept in a cache")
dl = c.get(f"{API}/admin/deadlines", headers={"Authorization": "Bearer demo-admin-token"}).json()
check(any(i["id"] == "brevo-smtp" for i in dl["items"]) and "summary" in dl, "deadlines: pre-filled list and summary")
check("deadlines" in adm and adm["deadlines"]["summary"]["worst"], "deadlines in the Diagnostic of the administrator")
check(c.get(f"{API}/admin/deadlines").status_code in (401, 403), "deadlines refused without rights")
print("diagnostics: all checks passed")

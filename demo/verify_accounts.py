"""Integration check of the accounts (lot 26): code by email (simulated
sending: the code is read in the security journal by the administrator),
account created at the first connection, enrolled device, token checked by
the server, revocation, anti-abuse.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_accounts.py
"""
import os
import sys
import uuid

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
ADMIN = {"Authorization": f"Bearer {os.getenv('SARCADE_ADMIN_TOKEN', 'demo-admin-token')}"}
c = httpx.Client(timeout=30)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def simulated_code(email):
    rows = c.get(f"{API}/admin/security-journal", headers=ADMIN, params={"category": "auth"}).json()
    for row in rows:
        if row["action"] == "login_code_simulated" and row["details"].get("email") == email:
            return row["details"]["code"]
    return None


EMAIL = f"op-{uuid.uuid4().hex[:8]}@exemple.fr"
state = c.get(f"{API}/auth/state").json()
check(state["delivery"] == "simulated" and "email_code" in state["methods"], "sending simulated without SMTP")

r = c.post(f"{API}/auth/code/request", json={"email": EMAIL.upper()})
check(r.status_code == 202 and r.json()["delivery"] == "simulated", "code requested")
code = simulated_code(EMAIL)
check(code is not None and len(code) == 6, "code readable by the administrator in the security journal")
again = c.post(f"{API}/auth/code/request", json={"email": EMAIL})
check(again.status_code == 429 and again.json()["detail"]["error"] == "resend_too_soon", "a new code waits 60 s")

wrong = "000000" if code != "000000" else "111111"
r = c.post(f"{API}/auth/code/verify", json={"email": EMAIL, "code": wrong})
check(r.status_code == 401 and r.json()["detail"]["attempts_left"] == 4, "wrong code refused, attempts counted")
r = c.post(f"{API}/auth/code/verify", json={"email": EMAIL, "code": code, "device_id": "TEL-CI",
                                            "label": "Téléphone CI", "platform": "android"})
check(r.status_code == 200 and r.json()["created"] is True, "account created at the first connection")
token, device = r.json()["token"], r.json()["device"]
H = {"Authorization": f"Bearer {token}"}
check(c.post(f"{API}/auth/code/verify", json={"email": EMAIL, "code": code}).status_code == 400,
      "a code serves only once")

me = c.get(f"{API}/me", headers=H).json()
check(me["email"] == EMAIL and me["via"] == "device" and me["device"]["device_id"] == "TEL-CI",
      "the device token identifies the account")
check(c.get(f"{API}/me").json()["account"] is None, "without token: no account (Core)")
check(c.get(f"{API}/admin/users", headers=H).status_code == 403, "an account is not an administrator")
devices = c.get(f"{API}/admin/devices", headers=ADMIN).json()
check(any(d["id"] == device["id"] and d["email"] == EMAIL for d in devices), "enrolled device listed for the administrator")

r = c.post(f"{API}/admin/devices/{device['id']}/revoke", headers=ADMIN)
check(r.status_code == 200 and r.json()["active"] is False, "administrator revokes the device")
r = c.get(f"{API}/me", headers=H)
check(r.status_code == 401 and r.json()["detail"] == "device_revoked", "revoked token refused at once")
check(c.get(f"{API}/me", headers={"Authorization": "Bearer sdt_faux"}).status_code == 401, "unknown token refused")
check(c.get(f"{API}/admin/security-journal/verify", headers=ADMIN).json()["ok"], "security journal chain intact")
print("accounts: all checks passed")

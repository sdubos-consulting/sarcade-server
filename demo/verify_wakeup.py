"""Integration check of the wake-up of the devices (note « Réveil des
appareils »): server key, token registration, test button, a new message
that does not fail when a push service cannot be reached.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_wakeup.py
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=20)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


config = c.get(f"{API}/wakeup/config").json()
check(len(config["webpush_public_key"]) > 80, "the server has its Web Push key")
check(c.get(f"{API}/wakeup/config").json()["webpush_public_key"] == config["webpush_public_key"], "the key stays the same")
check(config["fcm"] is False, "Android not woken without a Firebase service account")

event = c.post(f"{API}/events", json={"name": "CI réveil", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"
# Unreachable push service: the sending fails, nothing else does.
sub = json.dumps({"endpoint": "https://push.sarcade.invalid/x",
                  "keys": {"p256dh": "BOrQpEmlgVnpnHGiG8RBM9EPC3SR0MJ6mEXqMFhJR8ka2RCgrRVsxFNULDf6xmvFX7sKS_FZVqx7NPSa4XL2GOg",
                           "auth": "MDEyMzQ1Njc4OWFiY2RlZg"}})
r = c.put(f"{E}/devices/TEL-01/wakeup", json={"platform": "webpush", "token": sub})
check(r.status_code == 200, "Web Push subscription registered")
check(c.put(f"{E}/devices/TEL-01/wakeup", json={"platform": "webpush", "token": "not json"}).status_code == 422,
      "malformed subscription refused")
check(c.put(f"{E}/devices/TEL-01/wakeup", json={"platform": "sms", "token": "x"}).status_code == 422, "unknown platform refused")
check(c.get(f"{E}/wakeup/devices").json()["devices"] == ["TEL-01"], "the PCO sees who can be woken")
test = c.post(f"{E}/devices/TEL-01/wakeup/test").json()
check(test == {"sent": 0, "failed": 1, "removed": 0}, f"test button reports the failure ({test})")

now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
mid = str(uuid.uuid4())
r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": mid,
    "object_type": "message", "action": "create", "client_time": now,
    "payload": {"id": mid, "event_id": event, "sender_id": "PCO", "recipient_ids": [], "priority": "immediate",
                "body": "Réveil CI", "created_at": now}}])
check(r.json()[0]["status"] == "accepted", "the message is stored even though the wake-up fails")
time.sleep(2)
check(c.get(f"{BASE}/health").status_code == 200, "server still healthy after the wake-up attempt")
check(c.delete(f"{E}/devices/TEL-01/wakeup").json()["removed"] == 1, "token removed at sign-out")
print("wakeup: all checks passed")

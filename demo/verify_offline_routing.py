"""Integration check of navigation on the device (level 3) and of the
navigation app chosen by the organisation (level 1): the server builds the
road graph from the OSM extract and serves it; the setting reaches clients.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_offline_routing.py
"""
import gzip
import json
import os
import struct
import sys
import time

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


info = {}
for _ in range(30):
    info = c.get(f"{API}/routing/graph/info").json()
    if info.get("available"):
        break
    time.sleep(1)
if not info.get("available"):
    print(f"::error title=road graph::not built: {info}")
check(info.get("available") and info["vertices"] == 5 and info["edges"] == 6, f"road graph built from the OSM extract {info}")
r = c.get(f"{API}/routing/graph")
check(r.status_code == 200 and len(r.content) == info["size"], "road graph downloaded")
data = gzip.decompress(r.content)
(hlen,) = struct.unpack_from("<I", data, 4)
header = json.loads(data[8:8 + hlen])
check(data[:4] == b"SRG1" and header["attribution"].startswith("© OpenStreetMap"), "SRG1 package with attribution")
check(c.get(f"{API}/routing/graph", headers={"If-None-Match": r.headers["etag"]}).status_code == 304,
      "not downloaded again when unchanged")

check(c.get(f"{API}/clients/config").json()["navigation"]["app"] == "operator", "operator choice by default")
s = c.patch(f"{API}/admin/settings", headers=ADMIN,
            json={"settings": {"navigation": {"app": "organic_maps", "hide_tracking_apps": True}}})
check(s.status_code == 200, "organisation chooses Organic Maps and hides Google apps")
check(c.patch(f"{API}/admin/settings", headers=ADMIN,
              json={"settings": {"navigation": {"app": "waze", "hide_tracking_apps": True}}}).status_code == 422,
      "a hidden app cannot be the recommended one")
nav = c.post(f"{API}/clients/check", json={"device_id": "TEL-NAV", "platform": "android", "app_version": "0.1.0"}).json()
check(nav["organization"]["navigation"] == {"app": "organic_maps", "hide_tracking_apps": True},
      "setting sent to the clients")
c.patch(f"{API}/admin/settings", headers=ADMIN, json={"settings": {"navigation": {"app": "operator", "hide_tracking_apps": False}}})
print("offline routing: all checks passed")

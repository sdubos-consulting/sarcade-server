"""Integration check of the readable location: nearest commune from the
embedded list (offline), nearest road from the reverse geocoding stand-in.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_places.py
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


r = c.get(f"{API}/places/describe", params={"lat": 48.82308, "lon": 1.90972})
check(r.status_code == 200, "describe answers")
d = r.json()
check(d["commune"]["name"] == "Saint-Germain-de-la-Grange", "nearest commune from the embedded list")
check(d["label"] == "1,2 km au sud-est de Saint-Germain-de-la-Grange", f"French label: {d['label']}")
check(d["label_en"].startswith("1.2 km southeast of"), "English label")
check(d["road"] and d["road"]["name"] == "Rue des Côteaux de Chatron", "nearest road (stand-in of the IGN)")
check(d["altitude_m"] == 112.4, "altitude from the IGN altimetry (stand-in)")
check(d["department"] == "78", "department of the commune")
off = c.get(f"{API}/places/describe", params={"lat": 48.82308, "lon": 1.90972, "online": False}).json()
check(off["road"] is None and off["altitude_m"] is None and off["label"] == d["label"], "offline form: commune only")
check(c.get(f"{API}/places/describe", params={"lat": 91, "lon": 0}).status_code == 422, "latitude checked")
print("places: all checks passed")

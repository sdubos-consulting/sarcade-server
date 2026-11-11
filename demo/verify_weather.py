"""Integration check of the weather: forecast point, refresh, cache, compact
form, vigilance alert (once), bulletin, forecast of a designated point.

Needs the weather stub of the demo stack (no Internet in the CI).
Usage: SARCADE_URL=http://localhost:8000 python demo/verify_weather.py
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


event = c.post(f"{API}/events", json={"name": "CI météo", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"

check(c.get(f"{E}/weather").json()["forecast"] is None, "no forecast before the first refresh")
check(c.post(f"{E}/weather/refresh").status_code == 409, "no forecast point yet")

c.patch(f"{E}/settings", json={"actor_id": "PCO", "settings": {"weather_lat": 48.80, "weather_lon": 2.10,
                                                               "department": "78"}})
r = c.post(f"{E}/weather/refresh")
check(r.status_code == 200 and r.json()["provider"] == "meteofrance", "forecast fetched from Météo-France first")

w = c.get(f"{E}/weather").json()
check(w["forecast"] and len(w["forecast"]["hourly"]) >= 48 and w["stale"] is False, "forecast cached, not stale")
check(w["vigilance"]["color"] == "orange" and w["vigilance"]["phenomena"] == ["orages"], "department vigilance")
check(len(c.get(f"{E}/weather", params={"compact": True}).json()["forecast"]["hourly"]) <= 4,
      "compact form for the low-bandwidth mode")

c.post(f"{E}/weather/refresh")
log = [x["summary"] for x in c.get(f"{E}/logbook").json()]
alerts = [x for x in log if x.startswith("Vigilance orange")]
check(len(alerts) == 1, "orange vigilance logged once, not at every refresh")

text = c.get(f"{E}/weather/bulletin").json()["text"]
check(text.startswith("Météo, prochaines 12 h") and "Vigilance orange" in text, "bulletin for Diffusion PCO")

p = c.get(f"{API}/weather", params={"lat": 48.9, "lon": 2.2})
check(p.status_code == 200 and p.json()["forecast"]["hourly"], "forecast of a designated point")
print("weather: all checks passed")

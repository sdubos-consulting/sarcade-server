"""Integration check of the modules per event (card « Séparer le Core et les
modules », step 2, 31 Oct 2026): event models, modules in the event and in
the heartbeat, change by the PCO with its logbook line, bearings refused in
an event without the ADRASEC module, accepted with it.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_modules.py
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=10)
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check(cond, msg):
    if not cond:
        print(f"::error::FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def event(kind, **kw):
    r = c.post(f"{API}/events", json={"name": f"CI modules {kind}", "kind": kind, **kw})
    r.raise_for_status()
    return r.json()["id"]


def settings(eid):
    return c.get(f"{API}/events/{eid}").json()["settings"]


def bearing(eid):
    fid = str(uuid.uuid4())
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": eid, "object_id": fid,
                                     "object_type": "map_feature", "action": "create", "client_time": NOW,
                                     "payload": {"id": fid, "event_id": eid, "kind": "bearing", "points": [[48.8, 2.1]],
                                                 "color": 0xFF3DD6F5, "stroke_width": 2, "label": "",
                                                 "bearing": {"azimuth_deg": 42, "taken_at": NOW},
                                                 "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": NOW}}])
    r.raise_for_status()
    return r.json()[0]["status"]


cat = c.get(f"{API}/modules").json()
check({m["id"] for m in cat["modules"]} == {"adrasec", "race"}, "catalogue of the modules")
check(cat["templates"]["family"] == [] and cat["templates"]["sater"] == ["adrasec"], "event models")

fam = event("family")
check(settings(fam)["modules"] == [], "a family event has the Core only")
check(bearing(fam) == "rejected", "bearing refused without the ADRASEC module")

ex = event("exercise")
check(settings(ex)["modules"] == ["adrasec"], "an exercise has the ADRASEC module")
check(bearing(ex) == "accepted", "bearing accepted with the ADRASEC module")

own = event("family", modules=["adrasec"])
check(settings(own)["modules"] == ["adrasec"], "modules asked at creation win over the event model")
check(c.post(f"{API}/events", json={"name": "x", "kind": "family", "modules": ["sar"]}).status_code == 422,
      "unknown module refused at creation")

r = c.patch(f"{API}/events/{fam}/settings", json={"actor_id": "PCO", "settings": {"modules": ["adrasec"]}})
check(r.status_code == 200 and settings(fam)["modules"] == ["adrasec"], "the PCO enables ADRASEC")
check(bearing(fam) == "accepted", "bearing accepted once ADRASEC is on")
log = c.get(f"{API}/events/{fam}/logbook").json()
check(any("Modules de l'événement : ADRASEC" in (e.get("summary") or "") for e in log), "change in the logbook")

hb = c.post(f"{API}/events/{fam}/devices/TEL-09/heartbeat", json={"label": "TEL-09", "platform": "android"}).json()
check(hb["settings"]["modules"] == ["adrasec"], "modules sent to the applications in the heartbeat")
off = event("family")
r = c.patch(f"{API}/events/{off}/settings", json={"actor_id": "PCO", "settings": {"aprs_callsigns": ["F4ABC"]}})
check(r.status_code == 422 and "module_inactive" in r.text, "APRS settings refused without ADRASEC")
check(c.get(f"{API}/events/{off}/aprs/callsigns").json()["callsigns"] == [], "no APRS station followed without ADRASEC")
r = c.put(f"{API}/events/{off}/aprs/classes/aprs:F4ABC", json={"actor_id": "PCO", "kind": "fixed"})
check(r.status_code == 409, "APRS station class refused without ADRASEC")
box = {"device_id": "TEL-09", "lamin": 48.7, "lomin": 1.9, "lamax": 48.9, "lomax": 2.2}
r = c.post(f"{API}/events/{off}/adsb/watch", json=box)
check(r.status_code == 409, "ADS-B refused without ADRASEC")
r = c.post(f"{API}/events/{fam}/adsb/watch", json=box)
check(r.status_code == 200 and r.json()["interval_s"] >= 30 and "aircraft" in r.json(), "ADS-B watch accepted with ADRASEC")
check(c.get(f"{API}/adsb/status").json()["watched_events"] >= 1, "ADS-B status counts the watched event")
c.post(f"{API}/events/{fam}/adsb/watch", json={**box, "on": False})
r = c.get(f"{API}/reference-sites", params={"event_id": off})
check(r.status_code == 200 and r.json() == [], "no reference site for an event without ADRASEC")
# Lot 24: mission or exercise SATER, messages types of the car.
sat = event("sater")
check(settings(sat)["sater"] is True, "an event of kind sater offers « Signal entendu ici »")
check(settings(ex)["sater"] is False, "an exercise does not, until the PCO says so")
r = c.patch(f"{API}/events/{ex}/settings", json={"actor_id": "PCO", "settings": {"sater": True}})
check(r.status_code == 200 and settings(ex)["sater"] is True, "the PCO marks the exercise SATER")
r = c.patch(f"{API}/events/{off}/settings", json={"actor_id": "PCO", "settings": {"sater": True}})
check(r.status_code == 422 and "module_inactive" in r.text, "SATER refused without ADRASEC")
quick = settings(ex)["car_quick_messages"]
check([m["text"] for m in quick] == ["Bien reçu", "J'arrive", "Besoin d'aide", "Retard"]
      and quick[2]["priority"] == "urgent", "default messages types of the car")
new = [{"text": "En place", "priority": "routine"}, {"text": "Besoin d'aide", "priority": "urgent"}]
r = c.patch(f"{API}/events/{ex}/settings", json={"actor_id": "PCO", "settings": {"car_quick_messages": new}})
check(r.status_code == 200 and settings(ex)["car_quick_messages"] == new, "the PCO sets the messages types")
r = c.patch(f"{API}/events/{ex}/settings", json={"actor_id": "PCO", "settings": {"car_quick_messages": ["a", "b", "c", "d", "e"]}})
check(r.status_code == 422, "5 messages types refused")
hb = c.post(f"{API}/events/{ex}/devices/CAR-09/heartbeat", json={"label": "CAR-09", "platform": "android"}).json()
check(hb["settings"]["car_quick_messages"] == new and hb["settings"]["sater"] is True, "read by every participant")
log = [e.get("summary") or "" for e in c.get(f"{API}/events/{ex}/logbook").json()]
check("Messages types de la voiture : En place, Besoin d'aide (urgent)" in log, "messages types in the logbook")
print("modules per event: all checks passed")

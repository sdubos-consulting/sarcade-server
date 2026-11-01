"""Integration check of the TV screen, lot 4 (30 Oct 2026): pairing with the
public address noted, codes of the same network offered to PCO only (none
without a checked email), messages column of one screen at once, view with
the opacity per layer and its sender, probable zone and crossings computed
by the server, colours of the beacons, default view; lot 7: name of each
screen, « Identifier », day / night mode chosen per screen.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_tv.py
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


event = c.post(f"{API}/events", json={"name": "CI TV", "kind": "exercise"}).json()["id"]
E = f"{API}/events/{event}"
IP = {"cf-connecting-ip": "203.0.113.7"}

p = c.post(f"{API}/tv/pair", headers=IP).json()
check(len(p["code"]) == 6, "the TV gets a code")
check([n["code"] for n in c.get(f"{API}/tv/nearby", headers=IP).json()] == [p["code"]], "same network: code offered to every participant")
check(c.get(f"{API}/tv/nearby", headers={"cf-connecting-ip": "198.51.100.9"}).json() == [], "other network: nothing offered")
check(c.post(f"{API}/tv/approve", json={"code": p["code"], "event_id": event}).status_code == 200, "code approved")
token = c.get(f"{API}/tv/pair/{p['pair_id']}").json()["token"]
TV = {"authorization": f"Display {token}"}
[d] = c.get(f"{E}/displays").json()
check(d["messages"] is True and d["revoked"] is False, "screen listed, messages shown by default")

# Three bearings of the exercise beacon crossing near (48.80, 2.10).
for lat, lon, az in ((48.75, 2.10, 0), (48.80, 2.03, 90), (48.75, 2.04, 38)):
    fid = str(uuid.uuid4())
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": fid,
                                      "object_type": "map_feature", "action": "create", "client_time": NOW,
                                      "payload": {"id": fid, "event_id": event, "kind": "bearing", "points": [[lat, lon]],
                                                  "color": 0, "stroke_width": 2, "label": "121,375 MHz · Exercice",
                                                  "created_by": "TEL-01", "updated_by": "TEL-01", "updated_at": NOW,
                                                  "bearing": {"azimuth_deg": az, "length_m": 10000, "cone_deg": 5,
                                                              "search": "121,375 MHz · Exercice", "exercise": True}}}])
    check(r.status_code == 200 and r.json()[0]["status"] == "accepted", f"bearing {az}° accepted")

s = c.get(f"{API}/tv/state", headers=TV).json()
check(len(s["bearings"]) == 3 and all(b["color"] == "#1E88E5" and b["cone_deg"] == 5 for b in s["bearings"]),
      "bearings in the colour of the exercise beacon, with their cone")
[z] = s["zones"]
check(len(z["crossings"]) == 3 and abs(z["lat"] - 48.80) < 0.01 and z["label"].endswith("3 relevés"),
      "probable zone computed by the server")
check(s["view"] is None and s["default_base"]["tile_url"].startswith("https://"), "default view: base map of the event")

# Traces shown on the phone go to the TV (1 Nov 2026).
for i in range(4):
    c.post(f"{API}/positions", json={"id": str(uuid.uuid4()), "event_id": event, "device_id": "TEL-TR",
                                     "lat": 48.80 + i * 0.001, "lon": 2.10, "time": NOW})
c.put(f"{E}/displays/{d['id']}/view", json={"view": {"lat": 48.8, "lon": 2.1, "zoom": 13, "layers": {"traces": True},
                                                    "traces_window": 60}})
s = c.get(f"{API}/tv/state", headers=TV).json()
check(any(t["device_id"] == "TEL-TR" and len(t["points"]) == 4 for t in s["traces"]), "traces of the phone on the TV")
c.put(f"{E}/displays/{d['id']}/view", json={"view": None})

check(c.put(f"{E}/displays/{d['id']}/messages", json={"messages": False}).status_code == 200, "messages hidden on the screen")
check(c.get(f"{API}/tv/state", headers=TV).json()["options"]["messages"] is False, "applied at once, without a view")

view = {"lat": 48.8, "lon": 2.1, "zoom": 13, "from": "PCO-Web", "messages": True,
        "layers": {"bearings": True}, "opacity": {"aprs": 0.4, "high": 0.3, "relay": 0.6, "brg": 0.5}}
check(c.put(f"{E}/displays/{d['id']}/view", json={"view": view}).status_code == 200, "view sent")
check(c.get(f"{API}/tv/state", headers=TV).json()["options"]["messages"] is False, "a view keeps the screen's messages choice")
c.put(f"{E}/displays/{d['id']}/view", json={"view": view, "messages": True})
s = c.get(f"{API}/tv/state", headers=TV).json()
check(s["view"]["from"] == "PCO-Web" and s["options"]["opacity"]["aprs"] == 0.4 and s["options"]["opacity"]["relay"] == 0.6,
      "view with its sender and one opacity per layer")
check(s["options"]["messages"] is True, "messages column ticked on purpose when sending")
view["layers"] = {"bearings": False}
c.put(f"{E}/displays/{d['id']}/view", json={"view": view})
check(c.get(f"{API}/tv/state", headers=TV).json()["zones"] == [], "zone follows the bearings layer")
check(c.put(f"{E}/displays/{d['id']}/view", json={"view": None}).json() == {"view": None}, "back to the default view")
page = c.get(f"{BASE}/tv").text
check("Outils › Afficher sur une TV" in page and 'id="legend"' in page, "TV page: new instructions and legend")

# Lot 7 (30 Oct 2026): name of each screen, « Identifier », day / night mode.
q = c.post(f"{API}/tv/pair", json={"name": "TV Samsung · 1920×1080"}).json()
check(c.get(f"{API}/tv/code/{q['code']}").json()["name"] == "TV Samsung · 1920×1080", "default name of the TV proposed")
check(c.post(f"{API}/tv/approve", json={"code": q["code"], "event_id": event, "name": "Salle PCO"}).status_code == 200,
      "name chosen at pairing")
tok2 = c.get(f"{API}/tv/pair/{q['pair_id']}").json()["token"]
TV2 = {"authorization": f"Display {tok2}"}
s = c.get(f"{API}/tv/state", headers=TV2).json()
check(s["name"] == "Salle PCO" and s["identify"] is None and s["options"]["theme"] == "auto", "TV knows its name, auto mode")
d2 = next(x for x in c.get(f"{E}/displays").json() if x["code"] == q["code"])
check(d2["name"] == "Salle PCO" and d2["last_seen"], "list: name and last activity")
check(c.put(f"{E}/displays/{d2['id']}/name", json={"name": "Mur d'images"}).json() == {"name": "Mur d'images"}, "renamed")
check(c.post(f"{E}/displays/{d2['id']}/identify").status_code == 200, "identify asked")
check(c.get(f"{API}/tv/state", headers=TV2).json()["identify"]["name"] == "Mur d'images", "TV shows its name")
# Mode chosen per screen (1 Nov 2026, 8 h 27): a view sent no longer changes it.
c.put(f"{E}/displays/{d2['id']}/theme", json={"theme": "day"})
c.put(f"{E}/displays/{d2['id']}/view", json={"view": {"lat": 48.8, "lon": 2.1, "zoom": 12, "theme": "night"}})
check(c.get(f"{API}/tv/state", headers=TV2).json()["night"] is False, "day mode of the screen kept after a view")
c.put(f"{E}/displays/{d2['id']}/theme", json={"theme": "night"})
check(c.get(f"{API}/tv/state", headers=TV2).json()["night"] is True, "night mode of the screen")
check(c.put(f"{E}/displays/{d2['id']}/view", json={"view": {"lat": 48.8, "lon": 2.1, "zoom": 12, "theme": "x"}}).status_code == 422,
      "unknown mode refused")
print("TV lot 7 verified")

# Lot 11: « Écrans » button of the map (mine) and « Suivre ma vue » (live).
check("mine" in c.get(f"{E}/displays").json()[0], "screens say whether they are mine")
for z in (12, 13):
    check(c.put(f"{E}/displays/{d2['id']}/view", json={"view": {"lat": 48.8, "lon": 2.1, "zoom": z}, "live": True}).status_code == 200,
          "live view followed")
check(c.put(f"{E}/displays/{d2['id']}/view", json={"view": None}).json() == {"view": None}, "follow stopped")
print("TV lot 11 verified")

# Lot 17: SOS on the map of the TV, last known position of each open distress,
# even when the operators layer is hidden on the screen.
def say(sender, body, priority):
    mid = str(uuid.uuid4())
    p = {"id": mid, "event_id": event, "sender_id": sender, "recipient_ids": [], "priority": priority,
         "body": body, "created_at": NOW}
    r = c.post(f"{API}/sync", json=[{"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": mid,
                                      "object_type": "message", "action": "create", "client_time": NOW, "payload": p}])
    check(r.status_code == 200 and r.json()[0]["status"] == "accepted", f"message « {body[:20]} » accepted")


c.put(f"{E}/displays/{d['id']}/view", json={"view": {"lat": 48.8, "lon": 2.1, "zoom": 13, "layers": {"operators": False}}})
say("TEL-TR", "🆘 DÉTRESSE — TEL-TR — position 48.80300, 2.10000", "immediate")
say("TEL-SOS2", "🆘 DÉTRESSE — TEL-SOS2 — position inconnue", "immediate")
s = c.get(f"{API}/tv/state", headers=TV).json()
marks = {m["device_id"]: m for m in s["distress"]}
check(s["positions"] == [] or all(p["source"] == "aprs" for p in s["positions"]), "operators layer hidden on the screen")
check(marks["TEL-TR"]["known"] and abs(marks["TEL-TR"]["lat"] - 48.80) < 0.01 and marks["TEL-TR"]["stale"] is False,
      "SOS with its last known position, despite the hidden layer")
check(marks["TEL-SOS2"]["known"] is False, "SOS without position: « position inconnue »")
say("TEL-TR", "Fin de détresse — TEL-TR", "urgent")
check([m["device_id"] for m in c.get(f"{API}/tv/state", headers=TV).json()["distress"]] == ["TEL-SOS2"], "end of distress removes the halo")
say("TEL-SOS2", "Fin de détresse — TEL-SOS2", "urgent")
c.put(f"{E}/displays/{d['id']}/view", json={"view": None})
print("TV lot 17 (SOS) verified")

# Lot 18: no rule of the page moves a marker icon out of the placement of
# Leaflet (aircraft shifted south on the TV); diagnostic mode on ?diag=1.
import re  # noqa: E402

page = c.get(f"{BASE}/tv?diag=1").text
css = "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S))
icons = set(re.findall(r"L\.divIcon\(\{className:'([\w-]+)", page))
moved = [sel for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css) if re.search(r"(^|;)\s*position\s*:", body)
         and set(re.findall(r"\.([\w-]+)", re.split(r"[\s>+~]+", sel.strip())[-1])) & icons]
check({"ac", "sosm"} <= icons and moved == [], "TV page: marker icons placed by Leaflet only")
check('<div id="diag"></div>' in page and "diag=1" in page, "TV page: diagnostic mode on ?diag=1")
print("TV lot 18 (ADS-B) verified")

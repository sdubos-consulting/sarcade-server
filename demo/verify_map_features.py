"""Integration check of map feature synchronisation (ADR-001).

Runs against a live server: create, retransmission, newer and stale updates,
change feed, delete, undo of a delete, invalid payloads, GeoJSON and logbook.
Usage: SARCADE_URL=http://localhost:8000 python demo/verify_map_features.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
c = httpx.Client(timeout=10)
T0 = datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


event = c.post(f"{BASE}/api/v0.1/events", json={"name": "CI objets", "kind": "exercise"}).json()["id"]
fid = str(uuid.uuid4())


def feature(at, by="TERRAIN-01", **kw):
    f = {"id": fid, "event_id": event, "kind": "zone",
         "points": [[48.70, 2.00], [48.71, 2.00], [48.71, 2.01]],
         "radius_m": None, "color": 0xFFE53935, "stroke_width": 4, "label": "Secteur A",
         "created_by": "TERRAIN-01", "updated_by": by, "updated_at": iso(at)}
    f.update(kw)
    return f


def op(action, payload, opid=None):
    return {"operation_id": opid or str(uuid.uuid4()), "event_id": event, "object_id": fid,
            "object_type": "map_feature", "action": action, "client_time": iso(T0), "payload": payload}


def sync(*ops):
    r = c.post(f"{BASE}/api/v0.1/sync", json=list(ops))
    r.raise_for_status()
    return [x["status"] for x in r.json()]


def listing(**params):
    r = c.get(f"{BASE}/api/v0.1/events/{event}/map-features", params=params)
    r.raise_for_status()
    return r.json()


create = op("create", feature(T0))
check(sync(create) == ["accepted"], "creation accepted")
check(sync(create) == ["duplicate"], "retransmission is a duplicate")
check(len(listing()) == 1 and listing()[0]["revision"] == 1, "one feature, revision 1")

newer = op("update", feature(T0 + timedelta(seconds=30), by="PCO", label="Secteur A Nord", color=0xFF1E88E5))
stale = op("update", feature(T0 + timedelta(seconds=10), by="TERRAIN-02", label="Ancienne version"))
check(sync(newer, stale) == ["accepted", "conflict"], "newer update wins, stale one is a conflict")
f = listing()[0]
check(f["label"] == "Secteur A Nord" and f["updated_by"] == "PCO" and f["revision"] == 2, "stored state is the newest")

feed = c.get(f"{BASE}/api/v0.1/events/{event}/sync/changes", params={"after": 0}).json()["changes"]
labels = [ch["payload"].get("label") for ch in feed if ch["object_type"] == "map_feature"]
check(labels == ["Secteur A", "Secteur A Nord"], "change feed holds accepted states only, in order")
check(all("revision" in ch["payload"] for ch in feed), "feed payload is the server state")

delete = op("delete", {"id": fid, "event_id": event, "updated_by": "PCO", "updated_at": iso(T0 + timedelta(seconds=60))})
check(sync(delete) == ["accepted"], "delete accepted")
check(listing() == [], "deleted feature is hidden")
check(listing(include_deleted=True)[0]["deleted"] is True, "tombstone kept")

late = op("update", feature(T0 + timedelta(seconds=45), by="TERRAIN-02", label="Trop tard"))
check(sync(late) == ["conflict"], "edit older than the delete does not resurrect")
undo = op("update", feature(T0 + timedelta(seconds=90), by="PCO", label="Secteur A Nord"))
check(sync(undo) == ["accepted"], "newer change after delete (undo) accepted")
check(len(listing()) == 1, "feature restored")

# A device two days ahead would win every conflict for two days. Its time is
# clamped to the server clock, older than the last edit (T0+90 s), so it loses,
# and a correct device editing later still wins.
future = op("update", feature(T0 + timedelta(days=2), by="TERRAIN-03", label="Horloge fausse"))
check(sync(future) == ["conflict"], "far-future clock clamped, cannot override a newer edit")
later = op("update", feature(T0 + timedelta(seconds=120), by="TERRAIN-01", label="Secteur A Nord"))
check(sync(later) == ["accepted"], "correct device still wins afterwards")

bad = op("create", feature(T0, kind="zone", points=[[48.7, 2.0]]), opid=str(uuid.uuid4()))
check(sync(bad) == ["rejected"], "invalid geometry rejected")
other = dict(op("create", feature(T0)), event_id="unknown-event")
check(sync(other) == ["unknown_event"], "unknown event reported as unknown_event")

gj = c.get(f"{BASE}/api/v0.1/events/{event}/map-features.geojson")
gj.raise_for_status()
geo = gj.json()
coords = geo["features"][0]["geometry"]["coordinates"][0]
check(gj.headers["content-type"].startswith("application/geo+json"), "GeoJSON media type")
check(coords[0] == [2.0, 48.7] and coords[0] == coords[-1], "GeoJSON polygon closed, lon/lat order")

log = [x["summary"] for x in c.get(f"{BASE}/api/v0.1/events/{event}/logbook").json() if x["kind"] == "map_feature"]
check(log == ["Objet cartographique créé : Zone « Secteur A »",
              "Objet cartographique supprimé : Zone « Secteur A Nord »",
              "Objet cartographique restauré : Zone « Secteur A Nord »"], "logbook: create, delete, restore")

# Bearings (note « Relevés et croisements »): author or PCO only, every change in the logbook.
bid = str(uuid.uuid4())


def bearing(at, by, az):
    return {"operation_id": str(uuid.uuid4()), "event_id": event, "object_id": bid, "object_type": "map_feature",
            "action": "create", "client_time": iso(T0), "payload": {
                "id": bid, "event_id": event, "kind": "bearing", "points": [[48.80, 2.10]], "color": 0xFF3DD6F5,
                "stroke_width": 2, "label": "", "created_by": "ADR78-01", "updated_by": by, "updated_at": iso(at),
                "bearing": {"azimuth_deg": az, "strength": 7, "target": "121,500 MHz", "method": "compass"}}}


check(sync(bearing(T0, "ADR78-01", 38)) == ["accepted"], "bearing created by its author")
check(sync(bearing(T0 + timedelta(minutes=1), "ADR78-02", 40)) == ["rejected"], "another operator cannot change it")
check(sync(bearing(T0 + timedelta(minutes=2), "PCO", 41)) == ["accepted"], "the PCO can change it")
b = next(f for f in listing() if f["id"] == bid)
check(b["bearing"]["azimuth_deg"] == 41 and b["bearing"]["cone_deg"] == 5 and b["bearing"]["length_m"] == 10000,
      "bearing details kept, 10 km and ±5° by default")
log = [x["summary"] for x in c.get(f"{BASE}/api/v0.1/events/{event}/logbook").json() if x["object_id"] == bid]
check(log == ["Objet cartographique créé : Relevé", "Objet cartographique modifié : Relevé"], "bearing changes in the logbook")

# TV display: drawings and bearings by layer, revisions (« Envoyer ma vue », step 2).
pair = c.post(f"{BASE}/api/v0.1/tv/pair").json()
check(c.post(f"{BASE}/api/v0.1/tv/approve", json={"code": pair["code"], "event_id": event}).status_code == 200,
      "TV paired for the event")
tok = c.get(f"{BASE}/api/v0.1/tv/pair/{pair['pair_id']}").json()["token"]
TVH = {"authorization": f"Display {tok}"}
st = c.get(f"{BASE}/api/v0.1/tv/state", headers=TVH).json()
check(len(st["bearings"]) == 1 and st["bearings"][0]["azimuth_deg"] == 41 and st["bearings"][0]["color"].startswith("#"),
      "TV receives the bearings")
check(isinstance(st["drawings"], list) and st["references"] == [] and "weather" in st, "TV layers by default")
again = c.get(f"{BASE}/api/v0.1/tv/state", headers=TVH,
              params={"fr": st["features_rev"], "rr": st["references_rev"]}).json()
check("drawings" not in again and "bearings" not in again, "unchanged layers not sent again")
disp = [d for d in c.get(f"{BASE}/api/v0.1/events/{event}/displays").json() if d["active"]][0]
r = c.put(f"{BASE}/api/v0.1/events/{event}/displays/{disp['id']}/view",
          json={"view": {"lat": 48.8, "lon": 2.1, "zoom": 12, "layers": {"bearings": False}, "opacity": {"draw": 0.5}}})
check(r.status_code == 200 and r.json()["view"]["layers"]["bearings"] is False, "view with layers sent to the TV")
st = c.get(f"{BASE}/api/v0.1/tv/state", headers=TVH, params={"fr": st["features_rev"]}).json()
check(st["bearings"] == [] and st["options"]["opacity"]["draw"] == 0.5, "hidden layer not drawn by the TV")
check("logbook" not in st and "alert" in st and isinstance(st["sos"], list), "TV: no logbook, SOS banner state")
r = c.post(f"{BASE}/api/v0.1/events/{event}/displays/{disp['id']}/alert/clear")
check(r.status_code == 200 and c.get(f"{BASE}/api/v0.1/tv/state", headers=TVH).json()["alert"] is None,
      "SOS banner cleared on the screen")

# Lot 8: font size of a Texte, several objects in one logbook line.
tid = str(uuid.uuid4())
txt = dict(feature(T0, id=tid, kind="text", points=[[48.7, 2.0]], label="PC"), font_size=24)
check(sync(dict(op("create", txt), object_id=tid)) == ["accepted"], "text with a font size accepted")
got = [x for x in listing() if x["id"] == tid]
check(got and got[0].get("font_size") == 24, "font size kept and served")
gid = str(uuid.uuid4())
ids = [str(uuid.uuid4()) for _ in range(3)]
check(sync(*[dict(op("create", feature(T0, id=i)), object_id=i) for i in ids]) == ["accepted"] * 3, "3 objects created")
grp = {"id": gid, "n": 3, "action": "delete"}
dels = [dict(op("delete", {"id": i, "event_id": event, "updated_by": "TERRAIN-01",
                           "updated_at": iso(T0 + timedelta(minutes=9)), "group": grp}), object_id=i) for i in ids]
check(sync(*dels) == ["accepted"] * 3, "3 objects deleted together")
log = [x["summary"] for x in c.get(f"{BASE}/api/v0.1/events/{event}/logbook").json() if x["object_id"] in [gid, *ids]]
check(log.count("3 objets supprimés") == 1 and not any("supprimé :" in x for x in log), "one logbook line for the group")

# Lot 24: listening point SATER (« Signal entendu ici »), one touch from the car.
def listening(lid, at, by="CAR-01"):
    return {"id": lid, "event_id": event, "kind": "listening", "points": [[48.75, 2.05]], "label": "",
            "listening": {"heard_at": iso(at), "heading_deg": 135, "speed_mps": 12.5},
            "created_by": by, "updated_by": by, "updated_at": iso(at)}


def gone(lid, at, by="CAR-01"):
    return dict(op("delete", {"id": lid, "event_id": event, "updated_by": by, "updated_at": iso(at)}), object_id=lid)


l1, l2 = str(uuid.uuid4()), str(uuid.uuid4())
LT = T0 - timedelta(minutes=5)  # in the past: never brought back to the server time
check(sync(dict(op("create", listening(l1, LT)), object_id=l1)) == ["accepted"], "listening point sent without style")
got = [x for x in listing() if x["id"] == l1]
check(got and got[0]["listening"]["heading_deg"] == 135 and got[0]["color"] == 0xFFFF9F1C,
      "listening point served with heading and default colour")
gj = c.get(f"{BASE}/api/v0.1/events/{event}/map-features.geojson").json()
check(any(f["id"] == l1 and f["properties"].get("listening:heading_deg") == 135 for f in gj["features"]),
      "listening point in the GeoJSON")
check(sync(gone(l1, LT + timedelta(seconds=3), by="TERRAIN-02")) == ["rejected"], "another operator cannot cancel it")
check(sync(gone(l1, LT + timedelta(seconds=6))) == ["accepted"], "its author cancels it within 10 s")
check(sync(dict(op("create", listening(l2, LT)), object_id=l2)) == ["accepted"], "second listening point")
check(sync(gone(l2, LT + timedelta(seconds=40))) == ["rejected"], "too late for its author to cancel it")
check(sync(gone(l2, LT + timedelta(seconds=41), by="PCO")) == ["accepted"], "the PCO deletes it at any time")
log = [x["summary"] for x in c.get(f"{BASE}/api/v0.1/events/{event}/logbook").json() if x["object_id"] in (l1, l2)]
check("Signal de balise entendu ici par CAR-01, cap 135° (point d'écoute)" in log
      and "Point d'écoute de CAR-01 annulé" in log, "listening points in the logbook")

print("PASS: map feature synchronisation follows ADR-001")

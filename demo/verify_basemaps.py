"""Integration check of base maps: catalog, custom base map, offline
package upload, tiles served on the local network, event default.

Usage: SARCADE_URL=http://localhost:8000 python demo/verify_basemaps.py
"""
import os
import sqlite3
import sys
import tempfile
import uuid

import httpx

BASE = os.getenv("SARCADE_URL", "http://localhost:8000")
API = f"{BASE}/api/v0.1"
c = httpx.Client(timeout=30)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


catalog = {b["id"]: b for b in c.get(f"{API}/basemaps").json()}
check({"osm", "topo", "ign-plan", "ign-photos"} <= set(catalog), "four base maps in the first version")
check(catalog["osm"]["default"] and catalog["osm"]["attribution"].startswith("©"), "OpenStreetMap by default, attributed")

cid = f"pref-{uuid.uuid4().hex[:6]}"
r = c.post(f"{API}/basemaps", json={"actor_id": "ADMIN", "id": cid, "name": "Carte préfecture",
                                     "attribution": "© Préfecture des Yvelines"})
check(r.status_code == 201 and r.json()["custom"], "custom base map added (Core)")
check(c.post(f"{API}/basemaps", json={"actor_id": "ADMIN", "id": "osm", "name": "x", "attribution": "y"}).status_code == 422,
      "built-in ids are reserved")

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "p.mbtiles")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metadata (name text, value text)")
    con.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob)")
    con.executemany("INSERT INTO metadata VALUES (?, ?)", [("name", "Yvelines"), ("format", "png")])
    con.execute("INSERT INTO tiles VALUES (12, 2072, 2685, ?)", (b"\x89PNG-test",))  # XYZ y = 4095 - 2685 = 1410
    con.commit()
    con.close()
    with open(path, "rb") as f:
        r = c.put(f"{API}/basemaps/{cid}/package", data={"actor_id": "ADMIN"},
                  files={"file": ("p.mbtiles", f, "application/x-sqlite3")})
    check(r.status_code == 200 and r.json()["tiles"] == 1, "offline package uploaded")
r = c.put(f"{API}/basemaps/{cid}/package", data={"actor_id": "ADMIN"}, files={"file": ("x.mbtiles", b"junk")})
check(r.status_code == 422, "non-MBTiles file refused")

tile = c.get(f"{API}/basemaps/{cid}/tiles/12/2072/1410")
check(tile.status_code == 200 and tile.content == b"\x89PNG-test" and tile.headers["content-type"] == "image/png",
      "tile served from the package")
check(c.get(f"{API}/basemaps/{cid}/tiles/12/2072/1411").status_code == 404, "missing tile: 404")
listing = {b["id"]: b for b in c.get(f"{API}/basemaps").json()}
check(listing[cid]["available_offline"] and listing[cid]["offline_package"]["tiles"] == 1, "catalog shows the package")
check(c.get(f"{API}/basemaps/{cid}/package").status_code == 200, "package downloadable by the clients")

event = c.post(f"{API}/events", json={"name": "CI fonds", "kind": "exercise"}).json()["id"]
r = c.patch(f"{API}/events/{event}/settings", json={"actor_id": "PCO", "settings": {"basemap": "ign-plan"}})
check(r.json()["settings"]["basemap"] == "ign-plan", "the PCO sets the default base map of the event")

check(c.delete(f"{API}/basemaps/{cid}").status_code == 204, "custom base map deleted with its package")
check(c.get(f"{API}/basemaps/{cid}/package").status_code == 404, "package removed")
print("basemaps: all checks passed")

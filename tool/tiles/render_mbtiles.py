# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Raster MBTiles of a zone, rendered by a local TileServer GL (card « Cartes
hors connexion sur le téléphone », validated on 9 Nov 2026).

The OpenStreetMap data (Geofabrik extract, ODbL) are turned into vector
tiles by Planetiler, then drawn as PNG images by TileServer GL on the i9;
this script asks for every image of the zone and writes them in one
MBTiles file, the format the SARCADE server and the application read.
Never the public tile server of OpenStreetMap (bulk download forbidden).

    python -I tool/tiles/render_mbtiles.py --url http://localhost:8080/styles/basic-preview/256/{z}/{x}/{y}.png \\
        --bounds 1.44,48.43,2.24,49.09 --minzoom 6 --maxzoom 16 --out osm.mbtiles --name "OpenStreetMap Yvelines"
"""
from __future__ import annotations

import argparse
import concurrent.futures
import math
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path


def tile_range(bounds: tuple[float, float, float, float], z: int) -> tuple[range, range]:
    """Columns and rows of the tiles of zoom [z] covering [bounds]
    (west, south, east, north), XYZ numbering."""
    w, s, e, n = bounds
    n2 = 1 << z

    def x_of(lon: float) -> int:
        return min(n2 - 1, max(0, int((lon + 180) / 360 * n2)))

    def y_of(lat: float) -> int:
        r = math.radians(lat)
        return min(n2 - 1, max(0, int((1 - math.asinh(math.tan(r)) / math.pi) / 2 * n2)))

    return range(x_of(w), x_of(e) + 1), range(y_of(n), y_of(s) + 1)


def count_tiles(bounds, minzoom: int, maxzoom: int) -> int:
    total = 0
    for z in range(minzoom, maxzoom + 1):
        xs, ys = tile_range(bounds, z)
        total += len(xs) * len(ys)
    return total


def create(path: Path, name: str, bounds, minzoom: int, maxzoom: int, attribution: str) -> sqlite3.Connection:
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    w, s, e, n = bounds
    meta = {"name": name, "format": "png", "type": "baselayer", "version": "1",
            "bounds": f"{w},{s},{e},{n}", "center": f"{(w + e) / 2},{(s + n) / 2},{min(12, maxzoom)}",
            "minzoom": str(minzoom), "maxzoom": str(maxzoom), "attribution": attribution,
            "description": f"{name} : rendu SARCADE depuis les données OpenStreetMap (ODbL)"}
    db.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    return db


def fetch(url: str, z: int, x: int, y: int, tries: int = 3) -> bytes:
    u = url.format(z=z, x=x, y=y)
    for i in range(tries):
        try:
            with urllib.request.urlopen(u, timeout=60) as r:
                return r.read()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(1 + i)
    raise RuntimeError("unreachable")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--bounds", required=True, help="ouest,sud,est,nord")
    p.add_argument("--minzoom", type=int, default=6)
    p.add_argument("--maxzoom", type=int, default=16)
    p.add_argument("--out", required=True)
    p.add_argument("--name", default="OpenStreetMap")
    p.add_argument("--attribution", default="© OpenMapTiles © OpenStreetMap contributors")
    p.add_argument("--workers", type=int, default=12)
    a = p.parse_args(argv)
    bounds = tuple(float(v) for v in a.bounds.split(","))
    total = count_tiles(bounds, a.minzoom, a.maxzoom)
    print(f"{total} images, zooms {a.minzoom} à {a.maxzoom}", flush=True)
    out = Path(a.out)
    tmp = out.with_suffix(".partial")
    db = create(tmp, a.name, bounds, a.minzoom, a.maxzoom, a.attribution)
    done, t0 = 0, time.time()
    jobs = [(z, x, y) for z in range(a.minzoom, a.maxzoom + 1)
            for xs, ys in [tile_range(bounds, z)] for x in xs for y in ys]
    with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        for (z, x, y), data in zip(jobs, pool.map(lambda j: fetch(a.url, *j), jobs)):
            # MBTiles rows are numbered from the south (TMS).
            db.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, x, (1 << z) - 1 - y, sqlite3.Binary(data)))
            done += 1
            if done % 2000 == 0 or done == total:
                db.commit()
                rate = done / max(1e-6, time.time() - t0)
                print(f"{done}/{total} ({rate:.0f} images/s)", flush=True)
    db.commit()
    db.execute("VACUUM")
    db.close()
    tmp.replace(out)
    print(f"Fichier : {out} ({out.stat().st_size / 1024 ** 2:.0f} Mo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

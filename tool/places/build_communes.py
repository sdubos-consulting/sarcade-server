"""Build ``src/sarcade/places/communes.csv.gz`` (code;name;lat;lon) from the
commune outlines of france-geojson (github.com/gregoiredavid/france-geojson,
``communes-version-simplifiee.geojson``; IGN Admin Express + INSEE COG,
Licence Ouverte). The point kept is the centroid of the commune (of its
largest part), or a point inside it when the centroid falls outside.

Usage: python tool/places/build_communes.py communes-version-simplifiee.geojson
"""
import csv
import gzip
import io
import json
import sys
from pathlib import Path

from shapely.geometry import shape

OUT = Path(__file__).resolve().parents[2] / "src/sarcade/places/communes.csv.gz"


def main(src: str) -> None:
    rows = []
    for f in json.load(open(src, encoding="utf-8"))["features"]:
        if not f.get("geometry"):
            continue
        g = shape(f["geometry"])
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda p: p.area)
        c = g.centroid
        if not g.contains(c):
            c = g.representative_point()
        p = f["properties"]
        rows.append((p["code"], p["nom"], f"{c.y:.5f}", f"{c.x:.5f}"))
    rows.sort()
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", lineterminator="\n").writerows(rows)
    OUT.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), mtime=0))
    print(f"{len(rows)} communes -> {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main(sys.argv[1])

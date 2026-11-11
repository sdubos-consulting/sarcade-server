"""Measures a real road graph (navigation on the device, level 3): size of
the package, counts, parsing time and routing time on random trips.

The routing here is a straightforward A* in Python, slower than the Dart
code compiled in the application: the times are an upper bound.

Usage: python scripts/measure_road_graph.py graph.srg.gz [--trips 20] [--build-s 123.4]
Writes a Markdown summary to $GITHUB_STEP_SUMMARY when set.
"""
import argparse
import gzip
import heapq
import json
import math
import os
import random
import resource
import statistics
import time

from sarcade.routing.graph import MODE_BITS, haversine_m, parse


def adjacency(header, g, mode):
    bit = MODE_BITS[mode]
    speeds = header["speeds_kmh"][mode]
    adj = [[] for _ in g.vertices]
    for a, b, length, fwd, bwd, cls, _name, _geom in g.edges:
        kmh = speeds[cls] if cls < len(speeds) else 0
        if kmh <= 0:
            continue
        cost = length / (kmh / 3.6)
        if fwd & bit:
            adj[a].append((b, cost))
        if bwd & bit:
            adj[b].append((a, cost))
    return adj, max(speeds) / 3.6


def astar(g, adj, vmax, s, t):
    goal = g.vertices[t]
    dist = {s: 0.0}
    heap = [(haversine_m(g.vertices[s], goal) / vmax, s)]
    settled = 0
    while heap:
        _, u = heapq.heappop(heap)
        if u == t:
            return dist[u], settled
        settled += 1
        du = dist[u]
        for v, w in adj[u]:
            nd = du + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(heap, (nd + haversine_m(g.vertices[v], goal) / vmax, v))
    return None, settled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("graph")
    ap.add_argument("--trips", type=int, default=20)
    ap.add_argument("--build-s", type=float)
    ap.add_argument("--max-km", type=float, default=25.0)
    a = ap.parse_args()
    blob = open(a.graph, "rb").read()
    t0 = time.perf_counter()
    header, g = parse(blob)
    parse_s = time.perf_counter() - t0
    raw = len(gzip.decompress(blob))
    rng = random.Random(78)
    report = {"package_bytes": len(blob), "raw_bytes": raw, "vertices": header["vertices"], "edges": header["edges"],
              "geometry_points": header["geometry_points"], "names": header["names"], "bbox": header["bbox"],
              "build_s": a.build_s, "parse_s_python": round(parse_s, 2), "modes": {}}
    for mode in ("car", "foot", "offroad"):
        adj, vmax = adjacency(header, g, mode)
        usable = [i for i, nb in enumerate(adj) if nb]
        times, found, settled_all = [], 0, []
        tries = 0
        while len(times) < a.trips and tries < a.trips * 20:
            tries += 1
            s, t = rng.choice(usable), rng.choice(usable)
            if s == t or haversine_m(g.vertices[s], g.vertices[t]) > a.max_km * 1000:
                continue
            t1 = time.perf_counter()
            cost, settled = astar(g, adj, vmax, s, t)
            times.append(time.perf_counter() - t1)
            settled_all.append(settled)
            found += cost is not None
        report["modes"][mode] = {
            "trips": len(times), "found": found,
            "median_ms": round(statistics.median(times) * 1000, 1) if times else None,
            "max_ms": round(max(times) * 1000, 1) if times else None,
            "median_settled": int(statistics.median(settled_all)) if settled_all else None}
    report["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    print(json.dumps(report, indent=2))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Graphe routier réel (navigation sur l'appareil)\n\n")
            f.write(f"- Paquet : **{len(blob) / 1e6:.1f} Mo** (décompressé {raw / 1e6:.1f} Mo)\n")
            f.write(f"- Sommets {header['vertices']:,}, tronçons {header['edges']:,}, "
                    f"points de tracé {header['geometry_points']:,}, noms {header['names']:,}\n")
            if a.build_s:
                f.write(f"- Construction sur le serveur : {a.build_s:.0f} s\n")
            f.write(f"- Lecture en Python : {parse_s:.1f} s ; mémoire maximale {report['peak_rss_mb']} Mo\n\n")
            f.write("| Mode | Trajets | Trouvés | Médiane | Maximum | Sommets explorés (médiane) |\n|---|---|---|---|---|---|\n")
            for mode, m in report["modes"].items():
                f.write(f"| {mode} | {m['trips']} | {m['found']} | {m['median_ms']} ms | {m['max_ms']} ms | {m['median_settled']} |\n")
            f.write(f"\nTrajets tirés au hasard à moins de {a.max_km:.0f} km ; A* en Python, borne haute du temps "
                    "de l'application (Dart compilé).\n")


if __name__ == "__main__":
    main()

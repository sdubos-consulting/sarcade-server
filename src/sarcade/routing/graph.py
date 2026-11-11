"""Compact road graph for navigation on the device (level 3, variant B,
decision of 10 Oct 2026).

Built from the same OpenStreetMap extract as Valhalla (ADRASEC department
plus 10 km): roads, tracks and paths with the modes allowed on each edge
(car, foot, off-road) and the travel speeds per road class, so that the app
computes itineraries itself when the server cannot be reached.

Package « SRG1 », gzip-compressed, little-endian:

    b"SRG1", uint32 header length, header (UTF-8 JSON)
    vertices  : n x (int32 lat*1e6, int32 lon*1e6)
    edges     : m x (uint32 from, uint32 to, uint32 length in dm,
                     uint8 modes forward, uint8 modes backward,
                     uint16 class, uint32 name index,
                     uint32 first geometry point, uint16 geometry points)
    geometry  : k x (int32 lat*1e6, int32 lon*1e6)  intermediate points
    names     : uint32 count, then uint16 length + UTF-8 bytes each

Mode bits: 1 car, 2 foot, 4 off-road. An edge from A to B is usable from
A to B with « modes forward », from B to A with « modes backward ».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import gzip
import hashlib
import json
import math
import struct

MAGIC = b"SRG1"
FORMAT_VERSION = 1
CAR, FOOT, OFFROAD = 1, 2, 4
MODE_BITS = {"car": CAR, "foot": FOOT, "offroad": OFFROAD}

CLASSES = ["motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
           "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
           "service", "road", "track", "path", "footway", "pedestrian", "steps", "cycleway", "bridleway"]
_CLASS_INDEX = {c: i for i, c in enumerate(CLASSES)}
_CAR_CLASSES = set(CLASSES[:15])
_NO_FOOT = {"motorway", "motorway_link", "trunk", "trunk_link"}
_VEHICLE_ONEWAY_CLASSES = {"motorway", "motorway_link"}

# Speeds in km/h per mode and class (0: not allowed by default). Sent in the
# header so that the app uses the same values as the server.
SPEEDS = {
    "car": {"motorway": 110, "motorway_link": 60, "trunk": 90, "trunk_link": 50, "primary": 80,
            "primary_link": 50, "secondary": 70, "secondary_link": 45, "tertiary": 60, "tertiary_link": 40,
            "unclassified": 50, "residential": 30, "living_street": 10, "service": 20, "road": 40},
    "foot": {c: 4.5 for c in CLASSES if c not in _NO_FOOT} | {"steps": 2.0},
    "offroad": {"motorway": 100, "motorway_link": 55, "trunk": 80, "trunk_link": 45, "primary": 70,
                "primary_link": 45, "secondary": 60, "secondary_link": 40, "tertiary": 50, "tertiary_link": 35,
                "unclassified": 45, "residential": 30, "living_street": 10, "service": 20, "road": 35, "track": 20},
}

_NO = {"no", "private"}
_YES = {"yes", "designated", "permissive", "destination"}


def way_modes(tags: dict) -> tuple[int, int]:
    """(forward modes, backward modes) of a way from its OSM tags, or (0, 0)
    when it is not part of the graph."""
    highway = tags.get("highway")
    if highway not in _CLASS_INDEX:
        return 0, 0
    modes = 0
    if highway in _CAR_CLASSES:
        modes |= CAR | OFFROAD
    if highway == "track":
        modes |= OFFROAD
    if highway not in _NO_FOOT:
        modes |= FOOT
    access = tags.get("access")
    if access in _NO:
        modes = 0
    vehicle = tags.get("motor_vehicle") or tags.get("motorcar") or tags.get("vehicle")
    if vehicle in _NO:
        modes &= ~(CAR | OFFROAD)
    elif vehicle in _YES and highway == "track":
        modes |= CAR | OFFROAD
    elif vehicle in _YES and access in _NO and highway in _CAR_CLASSES:
        modes |= CAR | OFFROAD
    foot = tags.get("foot")
    if foot in _NO:
        modes &= ~FOOT
    elif foot in _YES:
        modes |= FOOT
    if tags.get("area") == "yes" and highway not in ("pedestrian", "footway"):
        return 0, 0
    if not modes:
        return 0, 0
    oneway = tags.get("oneway")
    vehicles = modes & (CAR | OFFROAD)
    forward = backward = modes
    is_oneway = oneway in ("yes", "true", "1") or tags.get("junction") in ("roundabout", "circular") \
        or (highway in _VEHICLE_ONEWAY_CLASSES and oneway not in ("no", "-1"))
    if oneway == "-1":
        forward = modes & ~vehicles
    elif is_oneway:
        backward = modes & ~vehicles
    return forward, backward


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371008.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


@dataclass
class Way:
    nodes: list[tuple[int, float, float]]  # (osm id, lat, lon)
    highway: str
    name: str
    forward: int
    backward: int


@dataclass
class Graph:
    vertices: list[tuple[float, float]] = field(default_factory=list)
    edges: list[tuple[int, int, float, int, int, int, int, list[tuple[float, float]]]] = field(default_factory=list)
    names: list[str] = field(default_factory=lambda: [""])


def build_graph(ways: list[Way]) -> Graph:
    """Vertices at the ends of ways and where ways meet; edges between them
    keep the intermediate points as geometry."""
    usage: dict[int, int] = {}
    for w in ways:
        for i, (nid, _, _) in enumerate(w.nodes):
            usage[nid] = usage.get(nid, 0) + (2 if i in (0, len(w.nodes) - 1) else 1)
    g = Graph()
    vertex_index: dict[int, int] = {}
    name_index = {"": 0}

    def vertex(nid: int, lat: float, lon: float) -> int:
        if nid not in vertex_index:
            vertex_index[nid] = len(g.vertices)
            g.vertices.append((lat, lon))
        return vertex_index[nid]

    for w in ways:
        if len(w.nodes) < 2:
            continue
        name = w.name[:200]
        if name not in name_index:
            name_index[name] = len(g.names)
            g.names.append(name)
        cls = _CLASS_INDEX[w.highway]
        start = 0
        for i in range(1, len(w.nodes)):
            nid = w.nodes[i][0]
            if i == len(w.nodes) - 1 or usage.get(nid, 0) > 1:
                seg = w.nodes[start:i + 1]
                a = vertex(seg[0][0], seg[0][1], seg[0][2])
                b = vertex(seg[-1][0], seg[-1][1], seg[-1][2])
                length = sum(haversine_m((p[1], p[2]), (q[1], q[2])) for p, q in zip(seg, seg[1:]))
                if a != b or length > 0:
                    g.edges.append((a, b, length, w.forward, w.backward, cls, name_index[name],
                                    [(p[1], p[2]) for p in seg[1:-1]]))
                start = i
    return g


def read_osm(path: str) -> list[Way]:
    """Ways of the graph from an OSM file (.osm.pbf or .osm), with the
    coordinates of their nodes."""
    import osmium

    ways: list[Way] = []

    class Handler(osmium.SimpleHandler):
        def way(self, w):
            tags = {t.k: t.v for t in w.tags}
            forward, backward = way_modes(tags)
            if not (forward or backward):
                return
            nodes = []
            for n in w.nodes:
                if n.location.valid():
                    nodes.append((n.ref, n.location.lat, n.location.lon))
            if len(nodes) >= 2:
                ways.append(Way(nodes, tags["highway"], tags.get("name") or tags.get("ref") or "",
                                forward, backward))

    Handler().apply_file(path, locations=True)
    return ways


def _e6(v: float) -> int:
    return int(round(v * 1e6))


def serialize(g: Graph, source: str = "", built_at: datetime | None = None) -> bytes:
    built_at = built_at or datetime.now(UTC)
    lats = [v[0] for v in g.vertices] or [0.0]
    lons = [v[1] for v in g.vertices] or [0.0]
    geometry: list[tuple[float, float]] = []
    edge_bytes = bytearray()
    for a, b, length, fwd, bwd, cls, name, geom in g.edges:
        edge_bytes += struct.pack("<IIIBBHIIH", a, b, min(int(round(length * 10)), 0xFFFFFFFF), fwd, bwd, cls,
                                  name, len(geometry), min(len(geom), 0xFFFF))
        geometry.extend(geom[:0xFFFF])
    header = {
        "format": "SRG1", "version": FORMAT_VERSION, "built_at": built_at.isoformat().replace("+00:00", "Z"),
        "source": source, "attribution": "© OpenStreetMap contributors (ODbL)",
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "vertices": len(g.vertices), "edges": len(g.edges), "geometry_points": len(geometry),
        "names": len(g.names), "modes": MODE_BITS, "classes": CLASSES,
        "speeds_kmh": {mode: [SPEEDS[mode].get(c, 0) for c in CLASSES] for mode in SPEEDS},
    }
    head = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
    out = bytearray(MAGIC + struct.pack("<I", len(head)) + head)
    for lat, lon in g.vertices:
        out += struct.pack("<ii", _e6(lat), _e6(lon))
    out += edge_bytes
    for lat, lon in geometry:
        out += struct.pack("<ii", _e6(lat), _e6(lon))
    out += struct.pack("<I", len(g.names))
    for n in g.names:
        raw = n.encode()[:0xFFFF]
        out += struct.pack("<H", len(raw)) + raw
    return gzip.compress(bytes(out), compresslevel=9, mtime=0)


def parse(blob: bytes) -> tuple[dict, Graph]:
    """Reads a package (tests and tools)."""
    data = gzip.decompress(blob)
    if data[:4] != MAGIC:
        raise ValueError("not_srg1")
    (hlen,) = struct.unpack_from("<I", data, 4)
    header = json.loads(data[8:8 + hlen])
    pos = 8 + hlen
    g = Graph(names=[])
    for _ in range(header["vertices"]):
        lat, lon = struct.unpack_from("<ii", data, pos)
        g.vertices.append((lat / 1e6, lon / 1e6))
        pos += 8
    raw_edges = []
    size = struct.calcsize("<IIIBBHIIH")
    for _ in range(header["edges"]):
        raw_edges.append(struct.unpack_from("<IIIBBHIIH", data, pos))
        pos += size
    geometry = []
    for _ in range(header["geometry_points"]):
        lat, lon = struct.unpack_from("<ii", data, pos)
        geometry.append((lat / 1e6, lon / 1e6))
        pos += 8
    (count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    for _ in range(count):
        (n,) = struct.unpack_from("<H", data, pos)
        g.names.append(data[pos + 2:pos + 2 + n].decode())
        pos += 2 + n
    for a, b, dm, fwd, bwd, cls, name, gs, gc in raw_edges:
        g.edges.append((a, b, dm / 10, fwd, bwd, cls, name, geometry[gs:gs + gc]))
    return header, g


def build_package(osm_path: str, out_path: str) -> dict:
    """Builds the package from an OSM extract; returns its description."""
    ways = read_osm(osm_path)
    g = build_graph(ways)
    blob = serialize(g, source=osm_path.rsplit("/", 1)[-1])
    with open(out_path + ".tmp", "wb") as f:
        f.write(blob)
    import os
    os.replace(out_path + ".tmp", out_path)
    header, _ = parse(blob)
    info = {k: header[k] for k in ("built_at", "bbox", "vertices", "edges", "version")}
    info.update(size=len(blob), sha256=hashlib.sha256(blob).hexdigest())
    with open(out_path + ".json", "w") as f:
        json.dump(info, f)
    return info


if __name__ == "__main__":  # python -m sarcade.routing.graph extract.osm.pbf graph.srg.gz
    import sys
    print(json.dumps(build_package(sys.argv[1], sys.argv[2]), indent=2))

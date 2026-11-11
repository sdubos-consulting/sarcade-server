"""Itinerary computation with Valhalla (navigation level 2, note
« Navigation vers un point désigné »).

Valhalla runs next to the server (``SARCADE_VALHALLA_URL``, default
``http://valhalla:8002``) with the OpenStreetMap data of the ADRASEC
department and a 10 km margin. Roads closed by the PCO are avoided with
``exclude_polygons``.
"""
from __future__ import annotations

import math
import os

import httpx
from shapely.geometry import LineString, Point, Polygon, mapping

MODES = {
    "car": {"costing": "auto"},
    "foot": {"costing": "pedestrian"},
    # Tracks and forest roads allowed: searches in rural areas.
    "offroad": {"costing": "auto", "costing_options": {"auto": {"use_tracks": 1.0}}},
}
CLOSURE_BUFFER_M = 15.0
# Variants (decision of 10 Oct 2026): at most two, near-identical ones dropped.
MAX_ALTERNATIVES = 2
SAME_ROUTE_SHARE = 0.85
SAME_ROUTE_DISTANCE_M = 30.0


class RoutingUnavailable(RuntimeError):
    pass


class NoRoute(ValueError):
    pass


def valhalla_url() -> str:
    return os.getenv("SARCADE_VALHALLA_URL", "http://valhalla:8002").rstrip("/")


def decode_polyline6(encoded: str) -> list[list[float]]:
    """Valhalla shapes: Google polyline with 6-digit precision → [[lat, lon]]."""
    coords, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lat / 1e6, lon / 1e6])
    return coords


def encode_polyline6(points: list[list[float]]) -> str:
    out, plat, plon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e6), round(lon * 1e6)
        for delta in (ilat - plat, ilon - plon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def closure_polygons(lines: list[list[list[float]]]) -> list[list[list[float]]]:
    """Closed road lines ([[lat, lon], ...]) → small polygons ([[lon, lat]])
    around them, as Valhalla's ``exclude_polygons`` expects."""
    polygons = []
    for line in lines:
        if len(line) < 2:
            continue
        lat0 = sum(p[0] for p in line) / len(line)
        kx = 111_320 * math.cos(math.radians(lat0))
        ky = 110_540
        local = LineString([(p[1] * kx, p[0] * ky) for p in line])
        shape = local.buffer(CLOSURE_BUFFER_M, cap_style="flat")
        if not isinstance(shape, Polygon):
            continue
        ring = [[x / kx, y / ky] for x, y in shape.exterior.coords]
        polygons.append(ring)
    return polygons


def build_request(points: list[list[float]], mode: str, closures: list[list[list[float]]] | None = None,
                  language: str = "fr-FR", alternatives: int = 0) -> dict:
    if mode not in MODES:
        raise ValueError("invalid_mode")
    if len(points) < 2:
        raise ValueError("not_enough_points")
    body = {
        "locations": [{"lat": p[0], "lon": p[1]} for p in points],
        "costing": MODES[mode]["costing"],
        "directions_options": {"language": language, "units": "kilometers"},
    }
    if "costing_options" in MODES[mode]:
        body["costing_options"] = MODES[mode]["costing_options"]
    polygons = closure_polygons(closures or [])
    if polygons:
        body["exclude_polygons"] = polygons
    # Valhalla only proposes variants between two locations (no via points).
    if alternatives and len(points) == 2:
        body["alternates"] = min(int(alternatives), MAX_ALTERNATIVES)
    return body


def shared_share(candidate: list[list[float]], reference: list[list[float]], within_m: float = SAME_ROUTE_DISTANCE_M,
                 step_m: float = 25.0) -> float:
    """Share of the candidate's length lying within ``within_m`` of the
    reference line, sampled every ``step_m`` (local metric projection)."""
    if len(candidate) < 2 or len(reference) < 2:
        return 0.0
    kx, ky = 111_320 * math.cos(math.radians(candidate[0][0])), 110_540

    def xy(p):
        return (p[1] * kx, p[0] * ky)

    ref = LineString([xy(p) for p in reference])
    total = near = 0.0
    for a, b in zip(candidate, candidate[1:]):
        (ax, ay), (bx, by) = xy(a), xy(b)
        seg = math.hypot(bx - ax, by - ay)
        n = max(1, int(seg // step_m))
        for i in range(n):
            t = (i + 0.5) / n
            total += seg / n
            if ref.distance(Point(ax + (bx - ax) * t, ay + (by - ay) * t)) <= within_m:
                near += seg / n
    return near / total if total else 0.0


def distinct_alternatives(main: dict, alternatives: list[dict]) -> list[dict]:
    """Keeps at most two variants, dropping those that are nearly the same
    road as the main itinerary or as a variant already kept."""
    kept: list[dict] = []
    for alt in alternatives:
        if any(shared_share(alt["geometry"], other["geometry"]) >= SAME_ROUTE_SHARE
               for other in [main, *kept]):
            continue
        kept.append(alt)
        if len(kept) == MAX_ALTERNATIVES:
            break
    return kept


def parse_response(data: dict) -> dict:
    trip = (data or {}).get("trip")
    if not trip or not trip.get("legs"):
        raise NoRoute("no_route")
    geometry: list[list[float]] = []
    maneuvers = []
    legs = []
    for leg in trip["legs"]:
        shape = decode_polyline6(leg.get("shape", ""))
        offset = len(geometry)
        if geometry and shape and geometry[-1] == shape[0]:
            shape, offset = shape[1:], offset - 1
        geometry.extend(shape)
        legs.append({"length_m": round(leg.get("summary", {}).get("length", 0) * 1000, 1),
                     "duration_s": round(leg.get("summary", {}).get("time", 0), 1)})
        for m in leg.get("maneuvers", []):
            maneuvers.append({"instruction": m.get("instruction", ""), "type": m.get("type"),
                              "length_m": round(m.get("length", 0) * 1000, 1),
                              "duration_s": round(m.get("time", 0), 1),
                              "begin_index": max(0, offset + m.get("begin_shape_index", 0))})
    summary = trip.get("summary", {})
    return {"length_m": round(summary.get("length", 0) * 1000, 1), "duration_s": round(summary.get("time", 0), 1),
            "geometry": geometry, "maneuvers": maneuvers, "legs": legs}


async def route(points: list[list[float]], mode: str, closures=None, client: httpx.AsyncClient | None = None,
                alternatives: int = 0) -> dict:
    body = build_request(points, mode, closures, alternatives=alternatives)
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.post(f"{valhalla_url()}/route", json=body)
    except httpx.HTTPError as exc:
        raise RoutingUnavailable(str(exc) or type(exc).__name__) from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code == 400:
        raise NoRoute("no_route")
    if r.status_code >= 500 or r.status_code != 200:
        raise RoutingUnavailable(f"valhalla_status_{r.status_code}")
    data = r.json()
    result = parse_response(data)
    if alternatives:
        others = []
        for alt in (data or {}).get("alternates") or []:
            try:
                others.append(parse_response(alt))
            except NoRoute:
                continue
        result["alternatives"] = distinct_alternatives(result, others)
    return result

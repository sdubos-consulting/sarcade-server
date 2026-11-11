"""Géorepérage (ATAK 5, validated on 28 Oct 2026): on a zone, rectangle or
circle drawn and marked « géorepérage », the PCO is warned when a team
enters or leaves it. Pure functions plus a small memory of who is inside
(after a restart the first position only sets the state, no alert)."""
from __future__ import annotations

import math

KINDS = {"zone", "rectangle", "circle"}
_inside: dict[tuple[str, str, str], bool] = {}


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6_371_000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def contains(data: dict, lat: float, lon: float) -> bool:
    kind, pts = data.get("kind"), data.get("points") or []
    if kind == "circle":
        return bool(pts) and data.get("radius_m") is not None and _distance_m((lat, lon), tuple(pts[0])) <= data["radius_m"]
    if kind == "rectangle" and len(pts) == 2:
        (a1, o1), (a2, o2) = pts
        return min(a1, a2) <= lat <= max(a1, a2) and min(o1, o2) <= lon <= max(o1, o2)
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def transitions(event_id: str, device_id: str, lat: float, lon: float, fences: list[dict]) -> list[tuple[dict, bool]]:
    """(fence, entered) for each fence whose side changed for this device."""
    out = []
    for f in fences:
        key = (event_id, f["id"], device_id)
        now_in = contains(f, lat, lon)
        before = _inside.get(key)
        _inside[key] = now_in
        if before is not None and before != now_in:
            out.append((f, now_in))
    return out


def message(device_id: str, fence: dict, entered: bool) -> str:
    name = fence.get("label") or "zone sans nom"
    return f"Géorepérage : {device_id} {'est entré dans' if entered else 'est sorti de'} « {name} »"

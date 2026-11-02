"""Routes with waypoints (note « Route avec points de passage »): pure rules.

Three synchronised object types, each with the ADR-001 rule:

* ``route``: name, assignment, default leg mode and profile, point order;
* ``route_waypoint``: one object per waypoint, so that two people editing
  two different points never overwrite each other (decision of 10 Oct 2026);
* ``route_passage``: passage of a team at a waypoint (automatic within the
  approach radius, or manual), cancellable.

Validated decisions: legs straight or along paths from the first version,
50 m approach radius by default, an assigned route is changed only by its
author and the PCO, the team is warned of any change by an urgent message
with acknowledgement, concurrent order changes: last one wins, PCO warned.
"""
from __future__ import annotations

import math

from sarcade.features.service import InvalidFeature, parse_time
from sarcade.groups.service import is_pco

TYPES = {"route", "route_waypoint", "route_passage", "road_closure", "itinerary", "move_order"}
NAV_MODES = {"car", "foot", "offroad"}
WAYPOINT_TYPES = {"start", "pass", "checkpoint", "supply", "finish"}
LEG_MODES = {"straight", "paths"}
PROFILES = {"foot", "vehicle"}
STATUSES = {"draft", "active", "done"}
DEFAULT_RADIUS_M = 50.0
MAX_WAYPOINTS = 500
MAX_LEG_POINTS = 20000

TYPE_NAMES = {"start": "départ", "pass": "passage", "checkpoint": "point de contrôle",
              "supply": "ravitaillement", "finish": "arrivée"}


def _text(value, field: str, maximum: int, required: bool = False) -> str:
    value = value or ""
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise InvalidFeature(f"invalid_{field}")
    return value.strip()


def _meta(payload: dict, event_id: str, object_id: str) -> dict:
    if not isinstance(payload, dict):
        raise InvalidFeature("invalid_payload")
    if payload.get("id") != object_id or payload.get("event_id") != event_id:
        raise InvalidFeature("id_mismatch")
    updated_by = payload.get("updated_by") or payload.get("created_by")
    if not isinstance(updated_by, str) or not updated_by or len(updated_by) > 64:
        raise InvalidFeature("invalid_updated_by")
    created_by = payload.get("created_by") if isinstance(payload.get("created_by"), str) else updated_by
    return {"id": object_id, "event_id": event_id, "created_by": created_by[:64],
            "updated_by": updated_by, "updated_at": parse_time(payload.get("updated_at"))}


def _coord(value, limit: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not -limit <= value <= limit:
        raise InvalidFeature(f"invalid_{field}")
    return float(value)


def validate_route(payload: dict, *, event_id: str, object_id: str) -> dict:
    data = _meta(payload, event_id, object_id)
    order = payload.get("point_order") or []
    if not isinstance(order, list) or len(order) > MAX_WAYPOINTS or not all(
            isinstance(x, str) and 0 < len(x) <= 64 for x in order) or len(set(order)) != len(order):
        raise InvalidFeature("invalid_point_order")
    base = payload.get("base_point_order")
    if base is not None and (not isinstance(base, list) or not all(isinstance(x, str) for x in base)):
        raise InvalidFeature("invalid_base_point_order")
    mode = payload.get("default_leg_mode", "straight")
    profile = payload.get("profile", "foot")
    status = payload.get("status", "draft")
    if mode not in LEG_MODES:
        raise InvalidFeature("invalid_leg_mode")
    if profile not in PROFILES:
        raise InvalidFeature("invalid_profile")
    if status not in STATUSES:
        raise InvalidFeature("invalid_status")
    color = payload.get("color", 0xFF1E88E5)
    if isinstance(color, bool) or not isinstance(color, int) or not 0 <= color <= 0xFFFFFFFF:
        raise InvalidFeature("invalid_color")
    data.update({
        "name": _text(payload.get("name"), "name", 80, required=True),
        "color": color, "default_leg_mode": mode, "profile": profile, "status": status,
        "point_order": order, "base_point_order": base,
        "assigned_team_id": _text(payload.get("assigned_team_id"), "assigned_team_id", 64) or None,
        "assigned_group_id": _text(payload.get("assigned_group_id"), "assigned_group_id", 64) or None,
    })
    return data


def validate_waypoint(payload: dict, *, event_id: str, object_id: str) -> dict:
    data = _meta(payload, event_id, object_id)
    kind = payload.get("type", "pass")
    if kind not in WAYPOINT_TYPES:
        raise InvalidFeature("invalid_type")
    radius = payload.get("radius_m", DEFAULT_RADIUS_M)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 5 <= radius <= 1000:
        raise InvalidFeature("invalid_radius")
    leg_mode = payload.get("leg_mode", "straight")
    if leg_mode not in LEG_MODES:
        raise InvalidFeature("invalid_leg_mode")
    geometry = payload.get("leg_geometry")
    if geometry is not None:
        if not isinstance(geometry, list) or len(geometry) > MAX_LEG_POINTS:
            raise InvalidFeature("invalid_leg_geometry")
        geometry = [[_coord(p[0], 90, "leg_geometry"), _coord(p[1], 180, "leg_geometry")]
                    for p in geometry if isinstance(p, (list, tuple)) and len(p) == 2]
    planned = payload.get("planned_time")
    data.update({
        "route_id": _text(payload.get("route_id"), "route_id", 64, required=True),
        "name": _text(payload.get("name"), "name", 40, required=True),
        "type": kind,
        "lat": _coord(payload.get("lat"), 90, "lat"),
        "lon": _coord(payload.get("lon"), 180, "lon"),
        "comment": _text(payload.get("comment"), "comment", 300),
        "planned_time": parse_time(planned).isoformat() if planned else None,
        "radius_m": float(radius),
        "leg_mode": leg_mode,
        "leg_geometry": geometry,
        "leg_needs_routing": bool(payload.get("leg_needs_routing", False)) and leg_mode == "paths",
    })
    return data


def validate_passage(payload: dict, *, event_id: str, object_id: str) -> dict:
    data = _meta(payload, event_id, object_id)
    mode = payload.get("mode", "manual")
    if mode not in ("auto", "manual"):
        raise InvalidFeature("invalid_mode")
    cancelled = payload.get("cancelled", False)
    if not isinstance(cancelled, bool):
        raise InvalidFeature("invalid_cancelled")
    data.update({
        "route_id": _text(payload.get("route_id"), "route_id", 64, required=True),
        "waypoint_id": _text(payload.get("waypoint_id"), "waypoint_id", 64, required=True),
        "team_id": _text(payload.get("team_id"), "team_id", 64) or None,
        "device_id": _text(payload.get("device_id"), "device_id", 64) or None,
        "time": parse_time(payload.get("time") or payload.get("updated_at")).isoformat(),
        "mode": mode, "cancelled": cancelled,
    })
    return data


def _line(raw, field: str, minimum: int, maximum: int) -> list[list[float]]:
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise InvalidFeature(f"invalid_{field}")
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise InvalidFeature(f"invalid_{field}")
        out.append([_coord(p[0], 90, field), _coord(p[1], 180, field)])
    return out


def validate_closure(payload: dict, *, event_id: str, object_id: str) -> dict:
    """Road closed by the PCO (flood, landslide, roadblock): avoided by every
    itinerary of the event."""
    data = _meta(payload, event_id, object_id)
    active = payload.get("active", True)
    if not isinstance(active, bool):
        raise InvalidFeature("invalid_active")
    data.update({"label": _text(payload.get("label"), "label", 120, required=True),
                 "points": _line(payload.get("points"), "points", 2, 500), "active": active})
    return data


def validate_itinerary(payload: dict, *, event_id: str, object_id: str) -> dict:
    """Itinerary shared by an operator: the PCO sees the planned path and the
    estimated arrival time, updated on the way."""
    data = _meta(payload, event_id, object_id)
    mode = payload.get("mode", "car")
    if mode not in NAV_MODES:
        raise InvalidFeature("invalid_mode")
    status = payload.get("status", "active")
    if status not in ("active", "arrived", "cancelled"):
        raise InvalidFeature("invalid_status")
    dest = payload.get("destination") or {}
    if not isinstance(dest, dict):
        raise InvalidFeature("invalid_destination")
    eta = payload.get("eta")
    data.update({
        "device_id": _text(payload.get("device_id"), "device_id", 64, required=True),
        "team_id": _text(payload.get("team_id"), "team_id", 64) or None,
        "mode": mode, "status": status,
        "destination": {"lat": _coord(dest.get("lat"), 90, "destination"),
                        "lon": _coord(dest.get("lon"), 180, "destination"),
                        "label": _text(dest.get("label"), "destination", 120)},
        "geometry": _line(payload.get("geometry") or [], "geometry", 0, MAX_LEG_POINTS),
        "length_m": float(payload.get("length_m") or 0),
        "remaining_m": float(payload.get("remaining_m") or 0),
        "eta": parse_time(eta).isoformat() if eta else None,
    })
    return data


# Order of the PCO to one operator: go to a point of the map (decision of
# the porteur, 29 Oct 2026). The operator answers accepted / refused /
# arrived; the PCO (or the author) can cancel.
MOVE_ORDER_STATUSES = {"sent", "accepted", "refused", "arrived", "cancelled"}
MOVE_ORDER_ANSWERS = {"accepted", "refused", "arrived"}


def validate_move_order(payload: dict, *, event_id: str, object_id: str) -> dict:
    data = _meta(payload, event_id, object_id)
    status = payload.get("status", "sent")
    if status not in MOVE_ORDER_STATUSES:
        raise InvalidFeature("invalid_status")
    dest = payload.get("destination") or {}
    if not isinstance(dest, dict):
        raise InvalidFeature("invalid_destination")
    data.update({
        "target_id": _text(payload.get("target_id"), "target_id", 64, required=True),
        "status": status,
        "destination": {"lat": _coord(dest.get("lat"), 90, "destination"),
                        "lon": _coord(dest.get("lon"), 180, "destination"),
                        "label": _text(dest.get("label"), "destination", 120)},
        "note": _text(payload.get("note"), "note", 200),
    })
    return data


def same_person(callsign_a: str | None, callsign_b: str | None) -> bool:
    """Two terminals of the same person: same callsign without its SSID
    (« F4ABC-7 » and « f4abc »), never two terminals without callsign
    (lot 20, card « Android Auto : ordres de déplacement peu utilisables… »,
    choice 2)."""
    def base(c: str | None) -> str:
        return (c or "").strip().upper().split("-")[0]
    return bool(base(callsign_a)) and base(callsign_a) == base(callsign_b)


def move_order_allowed(before: dict | None, created_by: str | None, change: dict, actor: str,
                       action: str, pco: bool, same_person_as_target: bool = False) -> bool:
    """The PCO gives the order; the operator only answers it (same order,
    status accepted / refused / arrived), from the targeted terminal or
    another terminal of the same person (lot 20, e.g. the phone in the car
    when the order went to the PC); the PCO or the author changes or
    cancels it."""
    if before is None:
        return pco and action != "delete"
    if pco or actor == created_by:
        return True
    return (action != "delete" and (actor == before.get("target_id") or same_person_as_target)
            and change.get("status") in MOVE_ORDER_ANSWERS
            and change.get("target_id") == before.get("target_id")
            and change.get("destination") == before.get("destination")
            and change.get("note", "") == before.get("note", ""))


# Default name of a move order destination (card « Fond et affichage », 30 Oct 2026).
DEFAULT_ORDER_LABEL = "Point de la carte"


def move_order_text(data: dict) -> str:
    d = data.get("destination") or {}
    label = d.get("label") or DEFAULT_ORDER_LABEL
    text = f"📍 Ordre de déplacement : rejoindre « {label} » ({d.get('lat', 0):.5f}, {d.get('lon', 0):.5f})"
    return f"{text} — {data['note']}" if data.get("note") else text


VALIDATORS = {"route": validate_route, "route_waypoint": validate_waypoint, "route_passage": validate_passage,
              "road_closure": validate_closure, "itinerary": validate_itinerary, "move_order": validate_move_order}


def is_assigned(route_data: dict | None) -> bool:
    return bool(route_data) and bool(route_data.get("assigned_team_id") or route_data.get("assigned_group_id"))


def can_edit(route_data: dict | None, route_created_by: str | None, actor_id: str) -> bool:
    """An assigned route is changed only by its author and the PCO."""
    if route_data is None or not is_assigned(route_data):
        return True
    return is_pco(actor_id) or actor_id == route_created_by


def concurrent_order_change(stored_order: list | None, base_order: list | None, new_order: list) -> bool:
    """True when the order was changed by someone else since the client read it."""
    if base_order is None or stored_order is None:
        return False
    return stored_order != base_order and new_order != stored_order


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def leg_length_m(previous: dict, waypoint: dict) -> float:
    """Length of the leg arriving at ``waypoint``: along the computed path
    when it exists, straight otherwise."""
    geometry = waypoint.get("leg_geometry") if waypoint.get("leg_mode") == "paths" else None
    if geometry and len(geometry) >= 2:
        return sum(haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(geometry, geometry[1:]))
    return haversine_m(previous["lat"], previous["lon"], waypoint["lat"], waypoint["lon"])


def change_summary(route_name: str, changes: list[str]) -> str:
    """Body of the automatic message sent to the team (urgent, ACK)."""
    detail = "; ".join(changes[:5]) + (" ; …" if len(changes) > 5 else "")
    return f"Route « {route_name} » modifiée : {detail}. Merci d'accuser réception."


def gpx(route: dict, waypoints: list[dict]) -> str:
    """GPX 1.1: the route (named points) and, for legs along paths, a track."""
    from xml.sax.saxutils import escape

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="SARCADE" xmlns="http://www.topografix.com/GPX/1/1">',
             f"  <rte><name>{escape(route['name'])}</name>"]
    for w in waypoints:
        lines.append(f'    <rtept lat="{w["lat"]:.6f}" lon="{w["lon"]:.6f}"><name>{escape(w["name"])}</name>'
                     f"<type>{escape(TYPE_NAMES.get(w['type'], w['type']))}</type>"
                     + (f"<desc>{escape(w['comment'])}</desc>" if w.get("comment") else "") + "</rtept>")
    lines.append("  </rte>")
    if any(w.get("leg_mode") == "paths" and w.get("leg_geometry") for w in waypoints):
        lines.append(f"  <trk><name>{escape(route['name'])} (tracé)</name><trkseg>")
        previous = None
        for w in waypoints:
            pts = w.get("leg_geometry") if w.get("leg_mode") == "paths" and w.get("leg_geometry") else (
                [[previous["lat"], previous["lon"]], [w["lat"], w["lon"]]] if previous else [[w["lat"], w["lon"]]])
            for p in pts:
                lines.append(f'    <trkpt lat="{p[0]:.6f}" lon="{p[1]:.6f}"/>')
            previous = w
        lines.append("  </trkseg></trk>")
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"

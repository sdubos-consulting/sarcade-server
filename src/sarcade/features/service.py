"""Map features: drawn, structured map objects (zones, lines, circles, texts...).

Conflict rule (ADR-001): last writer wins per object. Every change carries the
full object state with ``updated_at`` and ``updated_by``. A change applies only
if (updated_at, updated_by) is strictly newer than the stored pair. Create and
update are both upserts. Delete is a newer change that sets a tombstone, and a
change newer than the tombstone brings the object back (undo of a delete).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math

# « bearing »: radio direction-finding bearing (note « Relevés et croisements »):
# the point is where it was taken, the line is drawn by the clients.
# « listening »: listening point SATER (« Signal entendu ici », lot 24): a
# beacon heard at this place, without azimuth (« relevé sans azimut »).
KINDS = {"point", "line", "arrow", "circle", "rectangle", "zone", "text", "freehand", "measure", "bearing",
         "listening"}
POINT_KINDS = {"point", "text", "circle", "bearing", "listening"}
# Default style of a listening point sent from the car in one touch.
LISTENING_COLOR = 0xFFFF9F1C
LISTENING_STROKE = 2.0
# Its author may cancel it (delete) during 10 s after the time it was heard
# (« Annuler » on the confirmation alert), with 5 s of margin for the
# network; the PCO may delete it at any time.
LISTENING_CANCEL_S = 10
LISTENING_CANCEL_GRACE_S = 5
POLYGON_KINDS = {"rectangle", "zone"}
ACTIONS = {"create", "update", "delete"}

MAX_POINTS = 5000
MAX_LABEL = 160
DEFAULT_FONT_SIZE = 15.0
MAX_FUTURE_SKEW = timedelta(minutes=5)


class InvalidFeature(ValueError):
    """Payload that can never be applied: the operation is rejected."""


def parse_time(value) -> datetime:
    if isinstance(value, datetime):
        t = value
    elif isinstance(value, str):
        try:
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidFeature("invalid_updated_at") from exc
    else:
        raise InvalidFeature("invalid_updated_at")
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def clamp_time(t: datetime, now: datetime) -> datetime:
    """A device clock far in the future must not win every conflict."""
    return now if t > now + MAX_FUTURE_SKEW else t


def _points(raw) -> list[tuple[float, float]]:
    if not isinstance(raw, list) or not raw:
        raise InvalidFeature("invalid_points")
    if len(raw) > MAX_POINTS:
        raise InvalidFeature("too_many_points")
    points = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise InvalidFeature("invalid_points")
        lat, lon = item
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)):
            raise InvalidFeature("invalid_points")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise InvalidFeature("point_out_of_range")
        points.append((float(lat), float(lon)))
    return points


def validate_upsert(payload: dict, *, event_id: str, object_id: str) -> dict:
    """Checks a create/update payload and returns its canonical form."""
    if not isinstance(payload, dict):
        raise InvalidFeature("invalid_payload")
    if payload.get("id") != object_id or payload.get("event_id") != event_id:
        raise InvalidFeature("id_mismatch")
    kind = payload.get("kind")
    if kind not in KINDS:
        raise InvalidFeature("invalid_kind")
    points = _points(payload.get("points"))
    minimum = 1 if kind in POINT_KINDS else 3 if kind == "zone" else 2
    if len(points) < minimum:
        raise InvalidFeature("not_enough_points")
    radius = payload.get("radius_m")
    if kind == "circle":
        if not isinstance(radius, (int, float)) or not math.isfinite(radius) or not (0 < radius <= 500_000):
            raise InvalidFeature("invalid_radius")
        radius = float(radius)
    else:
        radius = None
    color = payload.get("color")
    if kind == "listening" and color is None:
        color = LISTENING_COLOR
    if isinstance(color, bool) or not isinstance(color, int) or not (0 <= color <= 0xFFFFFFFF):
        raise InvalidFeature("invalid_color")
    width = payload.get("stroke_width")
    if kind == "listening" and width is None:
        width = LISTENING_STROKE
    if not isinstance(width, (int, float)) or not (0.5 <= width <= 30):
        raise InvalidFeature("invalid_stroke_width")
    label = payload.get("label") or ""
    if not isinstance(label, str) or len(label) > MAX_LABEL:
        raise InvalidFeature("invalid_label")
    updated_by = payload.get("updated_by") or payload.get("created_by")
    if not isinstance(updated_by, str) or not updated_by or len(updated_by) > 64:
        raise InvalidFeature("invalid_updated_by")
    created_by = payload.get("created_by") if isinstance(payload.get("created_by"), str) else updated_by
    extra = {"bearing": validate_bearing(payload.get("bearing"))} if kind == "bearing" else {}
    if kind == "listening":
        extra["listening"] = validate_listening(payload.get("listening"), payload.get("updated_at"))
    # Géorepérage (ATAK 5): the PCO is warned when a team enters or leaves.
    if payload.get("geofence") is True and kind in ("zone", "rectangle", "circle"):
        extra["geofence"] = True
    # Transparency of the object (29 Oct 2026): kept only below 100 %.
    opacity = payload.get("opacity")
    if opacity is not None:
        opacity = _num(opacity, 0.1, 1.0, "invalid_opacity")
        if opacity < 1.0:
            extra["opacity"] = round(opacity, 2)
    # Font size of a Texte object (card « Dessin, objet Texte : pouvoir changer
    # la taille de la police »), kept only when it is not the default one.
    size = payload.get("font_size")
    if size is not None and kind == "text":
        size = _num(size, 8, 72, "invalid_font_size")
        if size != DEFAULT_FONT_SIZE:
            extra["font_size"] = round(size, 1)
    return {
        **extra,
        "id": object_id,
        "event_id": event_id,
        "kind": kind,
        "points": [[lat, lon] for lat, lon in points],
        "radius_m": radius,
        "color": color,
        "stroke_width": float(width),
        "label": label.strip(),
        "created_by": created_by[:64],
        "updated_by": updated_by,
        "updated_at": parse_time(payload.get("updated_at")),
    }


def _num(v, lo, hi, error):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise InvalidFeature(error)
    return float(v)


def _text(v, n, error):
    if v is None:
        return ""
    if not isinstance(v, str) or len(v) > n:
        raise InvalidFeature(error)
    return v.strip()


def validate_bearing(raw) -> dict:
    """Bearing details: true azimuth, line length (10 km by default), cone
    (±5° by default), magnetic azimuth and declination when taken with the
    compass, target, signal strength S1–S9 (0 = not given), comment, search
    it belongs to (one colour per search), time it was taken."""
    if not isinstance(raw, dict):
        raise InvalidFeature("invalid_bearing")
    out = {
        "azimuth_deg": _num(raw.get("azimuth_deg"), 0, 360, "invalid_azimuth") % 360,
        "length_m": _num(raw.get("length_m", 10_000), 100, 200_000, "invalid_length"),
        "cone_deg": _num(raw.get("cone_deg", 5), 0, 45, "invalid_cone"),
        "strength": int(_num(raw.get("strength", 0), 0, 9, "invalid_strength")),
        "target": _text(raw.get("target"), 60, "invalid_target"),
        "comment": _text(raw.get("comment"), 300, "invalid_comment"),
        "search": _text(raw.get("search"), 80, "invalid_search"),
        "method": raw.get("method") if raw.get("method") in ("compass", "manual", "map", "auto") else "manual",
        "taken_at": iso(parse_time(raw.get("taken_at"))) if raw.get("taken_at") else None,
    }
    for key in ("magnetic_deg", "declination_deg", "accuracy_m"):
        if raw.get(key) is not None:
            out[key] = _num(raw.get(key), -360, 100_000, f"invalid_{key}")
    # Exercise signal (kept only when true) and frequency listened to.
    exercise = raw.get("exercise")
    if exercise is not None and not isinstance(exercise, bool):
        raise InvalidFeature("invalid_exercise")
    if exercise:
        out["exercise"] = True
    # In the probable zone or not, decided by hand (card « Zone probable »,
    # 31 Oct 2026): « in » kept, « out » set aside; absent: automatic.
    if raw.get("zone") is not None:
        if raw.get("zone") not in ("in", "out", "auto"):
            raise InvalidFeature("invalid_zone")
        if raw["zone"] != "auto":
            out["zone"] = raw["zone"]
    if raw.get("frequency_mhz") is not None:
        out["frequency_mhz"] = _num(raw.get("frequency_mhz"), 1, 10_000, "invalid_frequency")
    return out


def validate_listening(raw, updated_at=None) -> dict:
    """Listening point SATER: time the signal was heard (``heard_at``,
    default the time of the change), heading of the vehicle (optional,
    0–360°), speed, accuracy of the position, exercise, searched signal,
    frequency and comment (all optional)."""
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise InvalidFeature("invalid_listening")
    heard = raw.get("heard_at") or updated_at
    out = {"heard_at": iso(parse_time(heard))}
    if raw.get("heading_deg") is not None:
        out["heading_deg"] = _num(raw.get("heading_deg"), 0, 360, "invalid_heading") % 360
    if raw.get("speed_mps") is not None:
        out["speed_mps"] = _num(raw.get("speed_mps"), 0, 150, "invalid_speed")
    if raw.get("accuracy_m") is not None:
        out["accuracy_m"] = _num(raw.get("accuracy_m"), 0, 100_000, "invalid_accuracy_m")
    exercise = raw.get("exercise")
    if exercise is not None and not isinstance(exercise, bool):
        raise InvalidFeature("invalid_exercise")
    if exercise:
        out["exercise"] = True
    for key, n in (("search", 80), ("comment", 300)):
        text = _text(raw.get(key), n, f"invalid_{key}")
        if text:
            out[key] = text
    if raw.get("frequency_mhz") is not None:
        out["frequency_mhz"] = _num(raw.get("frequency_mhz"), 1, 10_000, "invalid_frequency")
    return out


def listening_cancel_allowed(data: dict, delete_at: datetime) -> bool:
    """The author cancels his listening point within 10 s (+ 5 s of margin)
    after the time it was heard (device times, so a cancellation queued
    offline with its point still counts)."""
    try:
        heard = parse_time((data.get("listening") or {}).get("heard_at"))
    except InvalidFeature:
        return False
    return delete_at - heard <= timedelta(seconds=LISTENING_CANCEL_S + LISTENING_CANCEL_GRACE_S)


def validate_delete(payload: dict, *, event_id: str, object_id: str) -> dict:
    if not isinstance(payload, dict):
        raise InvalidFeature("invalid_payload")
    if payload.get("id", object_id) != object_id or payload.get("event_id", event_id) != event_id:
        raise InvalidFeature("id_mismatch")
    updated_by = payload.get("updated_by")
    if not isinstance(updated_by, str) or not updated_by or len(updated_by) > 64:
        raise InvalidFeature("invalid_updated_by")
    return {"id": object_id, "event_id": event_id, "updated_by": updated_by,
            "updated_at": parse_time(payload.get("updated_at"))}


def geometry_wkt(data: dict) -> str:
    """PostGIS geometry of a canonical feature, in lon/lat order."""
    pts = [(lon, lat) for lat, lon in data["points"]]
    kind = data["kind"]
    if kind in POINT_KINDS:
        lon, lat = pts[0]
        return f"POINT({lon} {lat})"
    if kind == "rectangle":
        (lon1, lat1), (lon2, lat2) = pts[0], pts[1]
        ring = [(lon1, lat1), (lon2, lat1), (lon2, lat2), (lon1, lat2), (lon1, lat1)]
        return "POLYGON((" + ",".join(f"{x} {y}" for x, y in ring) + "))"
    if kind == "zone":
        ring = pts + [pts[0]]
        return "POLYGON((" + ",".join(f"{x} {y}" for x, y in ring) + "))"
    return "LINESTRING(" + ",".join(f"{x} {y}" for x, y in pts) + ")"


def is_newer(incoming_at: datetime, incoming_by: str, stored_at: datetime | None, stored_by: str | None) -> bool:
    """Total order on changes: time first, author as a deterministic tie-break."""
    if stored_at is None:
        return True
    return (incoming_at, incoming_by) > (stored_at, stored_by or "")


@dataclass
class Decision:
    status: str  # accepted | conflict
    tombstone: bool = False


def decide(action: str, change: dict, stored) -> Decision:
    """Applies ADR-001 to an incoming change against the stored row (or None)."""
    if stored is not None and not is_newer(change["updated_at"], change["updated_by"], stored.updated_at, stored.updated_by):
        return Decision("conflict")
    # A delete of an object this server never saw still stores a tombstone,
    # so a late create with an older timestamp cannot bring it back.
    return Decision("accepted", tombstone=action == "delete")


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def feature_dict(row) -> dict:
    """Client representation, used by the API, the change feed and realtime."""
    data = dict(row.data)
    data.update({
        "id": row.id, "event_id": row.event_id, "kind": row.kind,
        "updated_at": iso(row.updated_at), "updated_by": row.updated_by,
        "created_by": row.created_by, "revision": row.revision,
        "deleted": row.deleted_at is not None,
    })
    return data


def feature_geojson(row) -> dict:
    data = feature_dict(row)
    pts = [[lon, lat] for lat, lon in data["points"]]
    if row.kind in POINT_KINDS:
        geometry = {"type": "Point", "coordinates": pts[0]}
    elif row.kind == "rectangle":
        (lon1, lat1), (lon2, lat2) = pts[0], pts[1]
        geometry = {"type": "Polygon", "coordinates": [[[lon1, lat1], [lon2, lat1], [lon2, lat2], [lon1, lat2], [lon1, lat1]]]}
    elif row.kind == "zone":
        geometry = {"type": "Polygon", "coordinates": [pts + [pts[0]]]}
    else:
        geometry = {"type": "LineString", "coordinates": pts}
    properties = {
        "sarcade:kind": row.kind, "event_id": row.event_id,
        "stroke": "#%06x" % (data["color"] & 0xFFFFFF), "stroke-width": data["stroke_width"],
        "updated_at": data["updated_at"], "updated_by": data["updated_by"],
    }
    if data.get("label"):
        properties["name"] = data["label"]
    if data.get("opacity") is not None:
        properties["stroke-opacity"] = properties["fill-opacity"] = data["opacity"]
    if row.kind == "circle":
        properties["radius_m"] = data["radius_m"]
    if row.kind == "bearing":
        properties.update({f"bearing:{k}": v for k, v in (data.get("bearing") or {}).items()})
    if row.kind == "listening":
        properties.update({f"listening:{k}": v for k, v in (data.get("listening") or {}).items()})
    return {"type": "Feature", "id": row.id, "geometry": geometry, "properties": properties}


def logbook_summary(kind: str, label: str, action: str, *, exercise: bool = False,
                    author: str = "", heading: float | None = None) -> str:
    if kind == "listening":
        cap = f", cap {round(heading) % 360}°" if heading is not None else ""
        text = {"create": f"Signal de balise entendu ici par {author or '?'}{cap} (point d'écoute)",
                "delete": f"Point d'écoute de {author or '?'} annulé",
                "restore": f"Point d'écoute de {author or '?'} rétabli"}.get(action,
                                                                      f"Point d'écoute de {author or '?'} modifié")
        return f"EXERCICE · {text}" if exercise else text
    names = {"point": "Point", "line": "Ligne", "arrow": "Flèche", "circle": "Cercle",
             "rectangle": "Rectangle", "zone": "Zone", "text": "Texte", "freehand": "Tracé libre",
             "measure": "Mesure", "bearing": "Relevé"}
    what = names.get(kind, "Objet")
    if label:
        what += f" « {label} »"
    verb = {"create": "créé", "delete": "supprimé", "restore": "restauré"}.get(action, "modifié")
    text = f"Objet cartographique {verb} : {what}"
    return f"EXERCICE · {text}" if exercise and kind == "bearing" else text


# One logbook line for a change of several objects at once (card « Dessin,
# sélection multiple », proposal 5): « 5 objets supprimés ».
GROUP_VERBS = {"delete": "supprimés", "move": "déplacés", "style": "modifiés", "duplicate": "dupliqués",
               "undo": "modifiés (annulation)", "redo": "modifiés (rétablissement)"}


def group_of(payload) -> dict | None:
    """Group tag of an operation (``{"id", "n", "action"}``), or None."""
    g = payload.get("group") if isinstance(payload, dict) else None
    if not isinstance(g, dict):
        return None
    gid, n, action = g.get("id"), g.get("n"), g.get("action")
    if not isinstance(gid, str) or not gid or len(gid) > 64 or isinstance(n, bool) or not isinstance(n, int) \
            or not 2 <= n <= MAX_POINTS or action not in GROUP_VERBS:
        return None
    return {"id": gid, "n": n, "action": action}


def group_summary(group: dict) -> str:
    return f"{group['n']} objets {GROUP_VERBS[group['action']]}"


def utcnow() -> datetime:
    return datetime.now(UTC)

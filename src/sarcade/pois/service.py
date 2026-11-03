# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Points of interest: rename and delete by synchronisation (decision of
29 Oct 2026, option A). Everyone changes his own points, the PCO all of them;
a deletion is written in the logbook and propagated to the other clients."""
from datetime import UTC, datetime
import math

from geoalchemy2.shape import to_shape

from sarcade.groups.service import is_pco

ACTIONS = ("create", "update", "delete")
MAX_LABEL = 160
# Shape of the icon (card « Points d'intérêt, création claire et icônes »,
# 30 Oct 2026): a simple set; None = the default pin (older points).
ICONS = ("pin", "gathering", "command_post", "danger", "parking", "water", "helipad",
         "vehicle", "viewpoint", "medical", "radio", "shelter", "flag")


class InvalidPoi(ValueError):
    pass


def can_edit(created_by: str | None, actor: str | None, pco: bool = False) -> bool:
    """The author or the PCO (a point without author: the PCO only)."""
    if not actor:
        return False
    return pco or is_pco(actor) or (created_by is not None and actor == created_by)


def clean_color(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise InvalidPoi("invalid_color")
    return value


def clean_icon(value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in ICONS:
        raise InvalidPoi("invalid_icon")
    return value


def clean_label(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidPoi("invalid_label")
    value = value.strip()
    if len(value) > MAX_LABEL:
        raise InvalidPoi("invalid_label")
    return value or None


def clean_position(lat, lon) -> tuple[float, float]:
    """New place of a moved point (lot 25, « Point d'intérêt : le déplacer
    facilement »): finite latitude and longitude within their bounds."""
    if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, int | float) or not isinstance(lon, int | float):
        raise InvalidPoi("invalid_position")
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise InvalidPoi("invalid_position")
    return lat, lon


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(min(1.0, math.sqrt(a)))


def format_distance(metres: float) -> str:
    """« 120 m », « 1,4 km » (French, as the logbook)."""
    if metres < 1000:
        return f"{round(metres)} m"
    return f"{metres / 1000:.1f} km".replace(".", ",")


def move_summary(label: str | None, metres: float) -> str:
    """Logbook line of a move: « Point d'intérêt « X » déplacé de 120 m »."""
    name = f" « {label} »" if label else ""
    return f"Point d'intérêt{name} déplacé de {format_distance(metres)}"


def actor_of(payload: dict) -> str | None:
    for key in ("updated_by", "created_by"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:64]
    return None


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def state(row) -> dict:
    """JSON state of a point, as journaled in the change feed and broadcast."""
    p = to_shape(row.point)
    out = {"id": row.id, "event_id": row.event_id, "kind": row.kind, "label": row.label,
           "lat": p.y, "lon": p.x, "created_at": _iso(row.created_at), "version": row.version,
           "created_by": row.created_by, "color": row.color, "icon": row.icon,
           "updated_by": row.updated_by, "updated_at": _iso(row.updated_at)}
    if row.deleted_at is not None:
        out["deleted"] = True
        out["deleted_at"] = _iso(row.deleted_at)
    return out


def logbook_summary(label: str | None, action: str) -> str:
    name = f" « {label} »" if label else ""
    return {"delete": f"Point d'intérêt{name} supprimé", "update": f"Point d'intérêt modifié{name}"}.get(
        action, f"Point d'intérêt{name} créé")

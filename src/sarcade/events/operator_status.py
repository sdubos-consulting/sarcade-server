"""Status of an operator in one touch (card « Android Auto : statut en un
appui », lot 24, 3 Nov 2026): « En route », « Sur place », « Disponible »,
« Pause », or none. Kept on the device of the event (``DeviceRow``) with the
time it was chosen, until the next one; the same status is shown by the
phone, the PC, the car and the TV.

Pure functions: validation and the « newer wins » rule, so that a status
queued offline never replaces a more recent one.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Order = order of the grid in the applications.
STATUSES = ("en_route", "sur_place", "disponible", "pause")
LABELS = {"en_route": "En route", "sur_place": "Sur place", "disponible": "Disponible", "pause": "Pause"}
# Colours of the pastille (TV, and suggested to the applications).
COLORS = {"en_route": "#FFB020", "sur_place": "#4ADE80", "disponible": "#3DD6F5", "pause": "#9AA3AD"}

MAX_FUTURE_SKEW = timedelta(minutes=5)


class InvalidStatus(ValueError):
    pass


def clean(value) -> str | None:
    """A known status, or None (no status)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in STATUSES:
        raise InvalidStatus("invalid_status")
    return value


def parse_time(value, now: datetime) -> datetime:
    """Time the status was chosen on the device; absent: now. A device clock
    far in the future is brought back to the server time."""
    if value is None:
        return now
    if isinstance(value, datetime):
        t = value
    elif isinstance(value, str):
        try:
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidStatus("invalid_time") from exc
    else:
        raise InvalidStatus("invalid_time")
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    return now if t > now + MAX_FUTURE_SKEW else t


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


as_utc = _utc


def apply(device, status: str | None, at: datetime) -> bool:
    """Sets the status of [device] when [at] is newer than the stored one.
    Returns True when it changed; the same status again keeps its first
    time (« depuis 8 h 10 »)."""
    stored_at = _utc(device.operator_status_at)
    if stored_at is not None and at <= stored_at:
        return False
    if device.operator_status == status:
        return False
    device.operator_status, device.operator_status_at = status, at
    return True


def state(device) -> dict:
    """What the clients get: ``operator_status`` and its time."""
    at = _utc(device.operator_status_at)
    return {"event_id": device.event_id, "device_id": device.device_id,
            "operator_status": device.operator_status,
            "operator_status_at": at.isoformat().replace("+00:00", "Z") if at else None}


def logbook_summary(who: str, status: str | None, reason: str | None = None) -> str:
    """« Statut de F4ABC : En route », and why when the server set it
    (lot 27: « Statut de F4ABC : En route, ordre accepté »)."""
    text = f"Statut de {who} : {LABELS[status]}" if status else f"Statut de {who} retiré"
    return f"{text}, {reason}" if reason else text

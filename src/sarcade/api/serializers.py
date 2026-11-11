from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import DeviceRow, POIRow, PositionRow, ReferenceSiteRow
from sarcade.events import operator_status


def position_dict(row: PositionRow) -> dict:
    p = to_shape(row.point)
    return {
        "id": row.id, "event_id": row.event_id, "device_id": row.device_id,
        "lat": p.y, "lon": p.x, "alt_m": row.alt_m,
        "accuracy_m": row.accuracy_m, "heading_deg": row.heading_deg,
        "speed_mps": row.speed_mps, "time": row.time,
        "battery_pct": row.battery_pct, "source": row.source or "device",
        "aprs_via": row.aprs_via, "aprs_symbol": row.aprs_symbol, "aprs_path": row.aprs_path,
        "callsign": row.aprs_callsign,
    }


def with_operator_status(db: Session, event_id: str, positions: list[dict]) -> list[dict]:
    """Latest positions with the status of each operator (lot 24) and his
    colour (card « Carte : la trace d'un opérateur dans la couleur choisie
    dans son profil », 10 Nov 2026; None = palette of the clients)."""
    devices = db.scalars(select(DeviceRow).where(
        DeviceRow.event_id == event_id,
        DeviceRow.operator_status.is_not(None) | DeviceRow.color.is_not(None))).all()
    states = {d.device_id: operator_status.state(d) for d in devices if d.operator_status is not None}
    colours = {d.device_id: d.color for d in devices if d.color is not None}
    for p in positions:
        s = states.get(p["device_id"])
        p["operator_status"] = s["operator_status"] if s else None
        p["operator_status_at"] = s["operator_status_at"] if s else None
        p["color"] = colours.get(p["device_id"])
    return positions


def device_colours(db: Session, event_id: str, ids) -> dict[str, int]:
    """Colours shared by the devices [ids] of the event (10 Nov 2026)."""
    ids = list(ids)
    if not ids:
        return {}
    return {d.device_id: d.color for d in db.scalars(select(DeviceRow).where(
        DeviceRow.event_id == event_id, DeviceRow.device_id.in_(ids), DeviceRow.color.is_not(None))).all()}


def poi_dict(row: POIRow) -> dict:
    p = to_shape(row.point)
    return {
        "id": row.id, "event_id": row.event_id, "kind": row.kind,
        "label": row.label, "lat": p.y, "lon": p.x,
        "created_at": row.created_at, "version": row.version,
        "created_by": row.created_by, "color": row.color, "icon": row.icon,
        "updated_by": row.updated_by, "updated_at": row.updated_at,
    }


def reference_site_dict(row: ReferenceSiteRow) -> dict:
    p = to_shape(row.point)
    return {
        "id": row.id,
        "category": row.category,
        "subtype": row.subtype,
        "name": row.name,
        "callsign": row.callsign,
        "lat": p.y,
        "lon": p.x,
        "alt_m": row.alt_m,
        "access": row.access,
        "clearance": row.clearance,
        "mode": row.mode,
        "rx_mhz": row.rx_mhz,
        "tx_mhz": row.tx_mhz,
        "ctcss_rx": row.ctcss_rx,
        "ctcss_tx": row.ctcss_tx,
        "offset": row.offset,
        "description": row.description,
        "verified_at": row.verified_at,
        "source": row.source,
        "source_layer": row.source_layer,
        "status": row.status,
    }

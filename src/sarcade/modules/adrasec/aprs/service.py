"""APRS on the server (note « APRS » in the vault).

The SARCADE server is the only one to talk to APRS and redistributes the
positions to the clients. Two inputs, merged and deduplicated:

* APRS-IS over the Internet, subscribed with a filter restricted to the
  selected callsigns (other stations are not even received);
* local radio through the Gateway: a KISS TCP modem (Dire Wolf) or decoded
  lines pushed over HTTP; frames from non-selected stations are dropped.

Stations outside the selected callsign groups are neither shown nor stored.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import logging
import os
import uuid

from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import AprsGroupRow, DeviceRow, EventRow, PositionRow
from sarcade.events import settings as event_settings

from sarcade.domain import callsigns as calls
from . import encode, kiss, parser

log = logging.getLogger("sarcade.aprs")

DEDUP_WINDOW = timedelta(seconds=30)
TX_MIN_INTERVAL = timedelta(minutes=2)


class Deduplicator:
    """The same packet heard on radio and on APRS-IS (or by two digipeaters)
    is kept once."""

    def __init__(self, window: timedelta = DEDUP_WINDOW, size: int = 5000):
        self.window, self.size = window, size
        self._seen: OrderedDict[str, datetime] = OrderedDict()

    def first_time(self, key: str, now: datetime) -> bool:
        while self._seen:
            oldest_key, at = next(iter(self._seen.items()))
            if now - at <= self.window and len(self._seen) < self.size:
                break
            self._seen.pop(oldest_key)
        if key in self._seen:
            return False
        self._seen[key] = now
        return True


def packet_key(line: str) -> str:
    packet = parser.parse_tnc2(line)
    return hashlib.sha256(f"{packet.source}|{packet.info}".encode("utf-8", "replace")).hexdigest()


def event_callsigns(db: Session, event: EventRow) -> list[str]:
    """Callsigns followed by an event: its groups plus the PCO additions;
    none without the ADRASEC module (31 Oct 2026)."""
    s = event_settings.merged(event.settings)
    if "adrasec" not in s["modules"]:
        return []
    entries = list(s["aprs_callsigns"])
    if s["aprs_groups"]:
        for group in db.scalars(select(AprsGroupRow).where(AprsGroupRow.id.in_(s["aprs_groups"]))):
            entries.extend(group.callsigns or [])
    return list(dict.fromkeys(entries))


def active_events(db: Session) -> list[EventRow]:
    return list(db.scalars(select(EventRow).where(EventRow.ended_at.is_(None))))


def all_followed_callsigns(db: Session) -> list[str]:
    entries: list[str] = []
    for event in active_events(db):
        entries.extend(event_callsigns(db, event))
    return sorted(set(entries))


def _device_for(db: Session, event_id: str, callsign: str) -> DeviceRow | None:
    rows = db.scalars(select(DeviceRow).where(DeviceRow.event_id == event_id,
                                              DeviceRow.callsign.is_not(None))).all()
    # Exact callsign, SSID included (decision of 28 Oct 2026): a terminal
    # declaring « F4JPO » no longer takes over F4JPO-5, the aprs.fi beacon of
    # the same operator elsewhere; « F4JPO » and « F4JPO-0 » are the same.
    wanted = callsign.strip().upper().removesuffix("-0")
    for row in rows:
        if (row.callsign or "").strip().upper().removesuffix("-0") == wanted:
            return row
    return None


def ingest(db: Session, line: str, via: str, now: datetime, dedup: Deduplicator | None) -> list[tuple[str, dict]]:
    """Stores the position of a selected station in every active event that
    follows it. Returns (event_id, position) pairs to broadcast."""
    try:
        pos = parser.decode(line)
        key = packet_key(line)
    except parser.NotAPosition:
        return []
    if dedup is not None and not dedup.first_time(key, now):
        return []
    try:
        path = ",".join(parser.parse_tnc2(line).path)
    except Exception:  # noqa: BLE001 - the path is a detail, never a reason to drop
        path = ""
    results = []
    for event in active_events(db):
        if not calls.matches(pos.callsign, event_callsigns(db, event)):
            continue
        device = _device_for(db, event.id, pos.callsign)
        device_id = device.device_id if device else f"aprs:{pos.callsign}"
        bucket = int(now.timestamp() // DEDUP_WINDOW.total_seconds())
        pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sarcade:aprs:{event.id}:{key}:{bucket}"))
        if db.get(PositionRow, pid) is not None:
            continue
        row = PositionRow(
            id=pid, event_id=event.id, device_id=device_id,
            point=WKTElement(f"POINT({pos.lon} {pos.lat})", srid=4326),
            alt_m=pos.alt_m, accuracy_m=None, heading_deg=(pos.course_deg % 360) if pos.course_deg else None,
            speed_mps=pos.speed_mps, time=now, source="aprs", aprs_via=via,
            aprs_symbol=(pos.symbol or None) and pos.symbol[:2], aprs_path=path[:160] or None,
            aprs_callsign=pos.callsign[:12],
        )
        db.add(row)
        if device is not None and (device.last_position_at is None or now > device.last_position_at):
            device.last_position_at = now
        results.append((event.id, {
            "id": pid, "event_id": event.id, "device_id": device_id, "lat": pos.lat, "lon": pos.lon,
            "alt_m": pos.alt_m, "accuracy_m": None, "heading_deg": row.heading_deg,
            "speed_mps": pos.speed_mps, "time": now.isoformat().replace("+00:00", "Z"),
            "battery_pct": None, "source": "aprs", "aprs_via": via, "callsign": pos.callsign,
            "symbol": pos.symbol, "comment": pos.comment,
            "aprs_symbol": row.aprs_symbol, "aprs_path": row.aprs_path,
        }))
    db.flush()
    return results


@dataclass
class LinkStatus:
    enabled: bool = False
    connected: bool = False
    detail: str = ""
    last_packet_at: datetime | None = None
    packets: int = 0


@dataclass
class AprsRuntime:
    """Shared state of the APRS links of this server process."""
    dedup: Deduplicator = field(default_factory=Deduplicator)
    aprs_is: LinkStatus = field(default_factory=LinkStatus)
    radio: LinkStatus = field(default_factory=LinkStatus)
    stored: int = 0
    tx_queue: asyncio.Queue | None = None
    last_tx: dict = field(default_factory=dict)
    filter: str = ""


runtime = AprsRuntime()


def server_callsign() -> str:
    return os.getenv("SARCADE_APRS_CALLSIGN", "").strip().upper()


def queue_transmission(event_settings_data: dict, device: DeviceRow | None, lat: float, lon: float,
                       now: datetime) -> bool:
    """Local radio transmission of a consenting operator's position, as an
    object under the callsign chosen by the PCO. Never sent to APRS-IS."""
    s = event_settings.merged(event_settings_data)
    # Callsign chosen by the PCO of the event; the server one as fallback.
    call = s["aprs_tx_callsign"] or server_callsign()
    if (not s["aprs_tx_rf"] or "adrasec" not in s["modules"] or device is None or not device.aprs_tx_consent or not device.callsign
            or not call or runtime.tx_queue is None or not runtime.radio.connected):
        return False
    last = runtime.last_tx.get(device.callsign)
    if last is not None and now - last < TX_MIN_INTERVAL:
        return False
    runtime.last_tx[device.callsign] = now
    info = encode.object_report(device.callsign[:9], lat, lon, now, comment="SARCADE")
    try:
        frame = kiss.kiss_frame(kiss.encode_ui(call, encode.TOCALL, ["WIDE1-1"], info))
    except kiss.InvalidFrame:
        return False
    runtime.tx_queue.put_nowait(frame)
    return True

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Profile photo in place of the initials (lot 29, group 7).

The device reduces the photo to 256 px before sending it; the server only
checks the type and the size. The photo belongs to the event: it is seen
by its participants and removed with it. APRS, MeshCore and Meshtastic
stations keep their symbols: they never send a photo."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import DevicePhotoRow

MAX_BYTES = 64 * 1024
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def mime_of(data: bytes) -> str | None:
    """Type read from the first bytes, not from the declared one."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def store(db: Session, event_id: str, device_id: str, data: bytes, now: datetime,
          by: str | None) -> DevicePhotoRow:
    """Sets the photo; ``ValueError("size")`` or ``ValueError("type")``."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("size")
    mime = mime_of(data)
    if mime is None:
        raise ValueError("type")
    row = db.get(DevicePhotoRow, (event_id, device_id))
    if row is None:
        row = DevicePhotoRow(event_id=event_id, device_id=device_id)
        db.add(row)
    row.data, row.mime, row.updated_at, row.updated_by = data, mime, now, by
    return row


def remove(db: Session, event_id: str, device_id: str) -> bool:
    row = db.get(DevicePhotoRow, (event_id, device_id))
    if row is None:
        return False
    db.delete(row)
    return True


def get(db: Session, event_id: str, device_id: str) -> DevicePhotoRow | None:
    return db.get(DevicePhotoRow, (event_id, device_id))


def of_event(db: Session, event_id: str) -> list[dict]:
    """Who has a photo and since when, without the images: the device
    downloads only the ones it does not have yet."""
    rows = db.execute(select(DevicePhotoRow.device_id, DevicePhotoRow.updated_at)
                      .where(DevicePhotoRow.event_id == event_id)
                      .order_by(DevicePhotoRow.device_id)).all()
    return [{"device_id": d, "updated_at": t.isoformat().replace("+00:00", "Z")} for d, t in rows]

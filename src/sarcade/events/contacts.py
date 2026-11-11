# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Contact of the devices in the logbook (main courante): arrival in the
event, loss of contact beyond the event's ``sync_alert_minutes``, contact
restored, and change of the application version.

The contact state of a device is the last ``contact`` logbook line written
for it: one line per state change, no extra column."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sarcade.db.models import DeviceRow, EventRow, LogbookRow

from . import settings as event_settings

CONTACT_KIND = "contact"
VERSION_KIND = "device_version"
ARRIVED = "Arrivée dans l'événement"
LOST = "Perte de contact"
SWEEP_SECONDS = 60


def aware(t: datetime | None) -> datetime | None:
    """SQLite gives naive datetimes back; the server always stores UTC."""
    if t is None:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t


def _line(db: Session, event_id: str, device_id: str, summary: str, now: datetime) -> None:
    db.add(LogbookRow(event_id=event_id, kind=CONTACT_KIND, object_id=device_id[:64],
                      actor_id=device_id[:64], summary=summary, time=now))


def last_state(db: Session, event_id: str, device_id: str) -> str | None:
    """Summary of the last contact line of this device, or None."""
    return db.scalar(select(LogbookRow.summary).where(
        LogbookRow.event_id == event_id, LogbookRow.kind == CONTACT_KIND, LogbookRow.object_id == device_id[:64],
    ).order_by(LogbookRow.seq.desc()).limit(1))


def restored_summary(gap: timedelta) -> str:
    return f"Contact rétabli (après {max(1, round(gap.total_seconds() / 60))} min)"


def on_contact(db: Session, event_id: str, device_id: str, previous: datetime | None, now: datetime,
               *, new: bool) -> None:
    """Called on every contact (heartbeat, position). Writes the arrival on
    the first contact, « Contact rétabli » when the device was lost."""
    previous = aware(previous)
    if new or previous is None:
        _line(db, event_id, device_id, ARRIVED, now)
        return
    # The sweep marks a device lost only after at least one sweep period
    # without contact: no lookup for the frequent contacts.
    gap = now - previous
    if gap >= timedelta(seconds=SWEEP_SECONDS) and last_state(db, event_id, device_id) == LOST:
        _line(db, event_id, device_id, restored_summary(gap), now)


def version_summary(label: str, old: str, new: str, platform: str | None) -> str:
    text = f"{label} : SARCADE {old} → {new}"
    return f"{text} ({platform})" if platform else text


def on_version(db: Session, row: DeviceRow, new_version: str | None, label: str | None,
               platform: str | None, now: datetime) -> None:
    """Version change reported by the heartbeat (not the first report)."""
    old = row.app_version
    if not old or not new_version or old == new_version:
        return
    db.add(LogbookRow(event_id=row.event_id, kind=VERSION_KIND, object_id=row.device_id,
                      actor_id=row.device_id, time=now,
                      summary=version_summary(label or row.label or row.device_id, old, new_version,
                                              platform or row.platform)))


def sweep_contacts(db: Session, now: datetime) -> int:
    """Writes « Perte de contact » for the devices of the open events not
    seen for more than the event's ``sync_alert_minutes``. Returns the
    number of lines written; the caller commits."""
    written = 0
    events = db.scalars(select(EventRow).where(EventRow.ended_at.is_(None))).all()
    for event in events:
        limit = timedelta(minutes=event_settings.merged(event.settings)["sync_alert_minutes"])
        cutoff = now - limit
        stale = [d for d in db.scalars(select(DeviceRow).where(
            DeviceRow.event_id == event.id, DeviceRow.last_contact_at.is_not(None))).all()
            if aware(d.last_contact_at) < cutoff]
        if not stale:
            continue
        latest = select(func.max(LogbookRow.seq)).where(
            LogbookRow.event_id == event.id, LogbookRow.kind == CONTACT_KIND).group_by(LogbookRow.object_id)
        states = dict(db.execute(select(LogbookRow.object_id, LogbookRow.summary)
                                 .where(LogbookRow.seq.in_(latest))).all())
        for device in stale:
            if states.get(device.device_id[:64]) != LOST:
                _line(db, event.id, device.device_id, LOST, now)
                written += 1
    return written


async def sweep_loop() -> None:
    import asyncio
    import logging

    from sarcade.db.session import SessionLocal

    log = logging.getLogger("sarcade.contacts")
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        db = SessionLocal()
        try:
            if sweep_contacts(db, datetime.now(UTC)):
                db.commit()
        except Exception as exc:  # noqa: BLE001 - never crash the server
            db.rollback()
            log.warning("contact sweep failed: %s", exc)
        finally:
            db.close()


def start(tasks: list) -> None:
    import asyncio
    tasks.append(asyncio.create_task(sweep_loop()))

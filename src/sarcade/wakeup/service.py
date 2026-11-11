"""Who to wake and how: tokens kept per device and event, signals without
content, routine messages grouped (one wake-up per minute and device)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sarcade.db.models import DeviceRow, WakeKeyRow, WakeTokenRow
from sarcade.db.session import SessionLocal
from . import fcm, webpush

log = logging.getLogger("sarcade.wakeup")

PLATFORMS = ("fcm", "fcm_ios", "webpush", "apns")
ROUTINE_SPACING_S = 60
TOKEN_MAX_AGE = timedelta(days=30)
GROUP_PREFIX = "group:"


@dataclass
class Stats:
    sent: int = 0
    failed: int = 0
    removed: int = 0
    last_error: str | None = None
    by_platform: dict = field(default_factory=dict)


stats = Stats()
_last_routine: dict[tuple[str, str], float] = {}
_fcm_token = fcm.AccessToken()


def targets(event_devices: list[str], sender_id: str, recipient_ids) -> list[str]:
    """Devices to wake for a message: direct recipients when the message
    names devices only; every device of the event for a broadcast or a
    group (the device itself decides what to show); never the sender."""
    recipients = [r for r in (recipient_ids or []) if isinstance(r, str)]
    direct = [r for r in recipients if not r.startswith(GROUP_PREFIX)]
    if recipients and len(direct) == len(recipients):
        wanted = set(direct)
        return [d for d in event_devices if d in wanted and d != sender_id]
    return [d for d in event_devices if d != sender_id]


def is_urgent(priority: str | None) -> bool:
    return priority in ("urgent", "immediate")


def routine_allowed(event_id: str, device_id: str, now: float | None = None) -> bool:
    """Routine: at most one wake-up per minute and device."""
    now = time.monotonic() if now is None else now
    last = _last_routine.get((event_id, device_id))
    if last is not None and now - last < ROUTINE_SPACING_S:
        return False
    _last_routine[(event_id, device_id)] = now
    return True


def signal(event_id: str, priority: str, kind: str = "message", tag: str | None = None) -> dict[str, str]:
    """The whole content of a wake-up: no text, no callsign, no position.
    [tag]: key of the notification, the same as the one the application
    gives to the message (one notification per message, per distress)."""
    data = {"type": "wake", "kind": kind, "event_id": event_id, "priority": priority}
    if tag:
        data["tag"] = tag
    return data


def _fnv1a(text: str) -> int:
    """FNV-1a 32 bits over UTF-16 code units, as the application (Dart)."""
    h = 0x811C9DC5
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        h ^= raw[i] | (raw[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def sos_key(event_id: str, sender_id: str) -> str:
    """Key of the distress notifications of a sender (no callsign in it)."""
    return f"sos-{_fnv1a(f'{event_id}|{sender_id}'):08x}"


def notification_tag(event_id: str, message: dict) -> str | None:
    """Card « Détresse, une seule notification » (30 Oct 2026, 12 h 45): a
    distress (immediate « 🆘 » to everyone) and its « Fin de détresse »
    share the key of their sender, so that the end replaces the distress;
    any other message: its identifier."""
    body = str(message.get("body") or "")
    recipients = message.get("recipient_ids") or []
    low = body.strip().lower()
    distress = message.get("priority") == "immediate" and body.lstrip().startswith("🆘")
    end = low.startswith("fin de détresse") or low.startswith("end of distress")
    if not recipients and (distress or end):
        return sos_key(event_id, str(message.get("sender_id") or ""))
    mid = message.get("id")
    return str(mid) if mid else None


def vapid_key(db: Session):
    """The server's own Web Push key pair, created at first use."""
    row = db.get(WakeKeyRow, "vapid")
    if row is None:
        row = WakeKeyRow(id="vapid", private_pem=webpush.new_private_key_pem(), created_at=datetime.now(UTC))
        db.add(row)
        db.commit()
    return webpush.load_key(row.private_pem)


def config(db: Session) -> dict:
    return {"webpush_public_key": webpush.public_key_b64(vapid_key(db)),
            "fcm": fcm.load_account() is not None, "apns": False}


def register(db: Session, event_id: str, device_id: str, platform: str, token: str, now: datetime) -> None:
    if platform not in PLATFORMS:
        raise ValueError("invalid_platform")
    token = token.strip()
    if not token or len(token) > 4096:
        raise ValueError("invalid_token")
    if platform == "webpush":
        webpush.parse_subscription(token)
    row = db.scalars(select(WakeTokenRow).where(WakeTokenRow.event_id == event_id, WakeTokenRow.device_id == device_id,
                                                WakeTokenRow.platform == platform)).first()
    if row is None:
        db.add(WakeTokenRow(event_id=event_id, device_id=device_id, platform=platform, token=token,
                            created_at=now, last_seen_at=now))
    else:
        row.token, row.last_seen_at = token, now
    db.execute(delete(WakeTokenRow).where(WakeTokenRow.last_seen_at < now - TOKEN_MAX_AGE))
    db.commit()


def unregister(db: Session, event_id: str, device_id: str, platform: str | None = None) -> int:
    q = delete(WakeTokenRow).where(WakeTokenRow.event_id == event_id, WakeTokenRow.device_id == device_id)
    if platform:
        q = q.where(WakeTokenRow.platform == platform)
    n = db.execute(q).rowcount or 0
    db.commit()
    return n


def wakeable(db: Session, event_id: str) -> set[str]:
    return set(db.scalars(select(WakeTokenRow.device_id).where(WakeTokenRow.event_id == event_id)).all())


async def _send_one(client: httpx.AsyncClient, row: WakeTokenRow, data: dict[str, str], urgent: bool,
                    vapid, subject: str, account: dict | None) -> str:
    """'sent', 'gone' (token removed) or 'failed'."""
    try:
        if row.platform == "webpush":
            sub = webpush.parse_subscription(row.token)
            body = webpush.encrypt(json.dumps(data).encode(), sub["p256dh"], sub["auth"])
            r = await client.post(sub["endpoint"], content=body, timeout=15, headers={
                "Authorization": webpush.vapid_header(sub["endpoint"], vapid, subject),
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                "TTL": "3600", "Urgency": "high" if urgent else "normal"})
            if r.status_code in (404, 410):
                return "gone"
            return "sent" if r.status_code < 300 else "failed"
        if row.platform in ("fcm", "fcm_ios"):
            if account is None:
                return "failed"
            access = await _fcm_token.get(client, account)
            r = await client.post(f"https://fcm.googleapis.com/v1/projects/{account['project_id']}/messages:send",
                                  json=fcm.message(row.token, data, urgent, ios=row.platform == "fcm_ios"), timeout=15,
                                  headers={"Authorization": f"Bearer {access}"})
            if fcm.is_gone(r.status_code, r.text):
                return "gone"
            return "sent" if r.status_code < 300 else "failed"
        return "failed"  # apns: with the iOS signing, later
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        stats.last_error = f"{row.platform}: {type(exc).__name__}"
        return "failed"


async def wake(event_id: str, device_ids: list[str], priority: str, kind: str = "message",
               force: bool = False, tag: str | None = None) -> dict:
    """Sends the signal to every token of these devices. Routine messages are
    grouped unless [force] (test button)."""
    urgent = is_urgent(priority)
    db = SessionLocal()
    try:
        if not urgent and not force:
            device_ids = [d for d in device_ids if routine_allowed(event_id, d)]
        if not device_ids:
            return {"sent": 0, "failed": 0, "removed": 0}
        rows = db.scalars(select(WakeTokenRow).where(WakeTokenRow.event_id == event_id,
                                                     WakeTokenRow.device_id.in_(device_ids))).all()
        if not rows:
            return {"sent": 0, "failed": 0, "removed": 0}
        vapid = vapid_key(db)
        subject = os.getenv("SARCADE_VAPID_SUBJECT") or "mailto:github@sdubos.com"
        account = fcm.load_account()
        data = signal(event_id, priority, kind, tag)
        result = {"sent": 0, "failed": 0, "removed": 0}
        async with httpx.AsyncClient() as client:
            outcomes = await asyncio.gather(*[_send_one(client, r, data, urgent, vapid, subject, account) for r in rows])
        now = datetime.now(UTC)
        for row, outcome in zip(rows, outcomes):
            stats.by_platform[row.platform] = stats.by_platform.get(row.platform, 0) + (outcome == "sent")
            if outcome == "sent":
                result["sent"] += 1; row.last_used_at = now
            elif outcome == "gone":
                result["removed"] += 1; db.delete(row)
            else:
                result["failed"] += 1
        db.commit()
        stats.sent += result["sent"]; stats.failed += result["failed"]; stats.removed += result["removed"]
        return result
    finally:
        db.close()


def event_devices(db: Session, event_id: str) -> list[str]:
    return list(db.scalars(select(DeviceRow.device_id).where(DeviceRow.event_id == event_id)).all())


async def wake_for_message(event_id: str, message: dict) -> None:
    """After a new message is stored: wakes its recipients (never raises)."""
    try:
        db = SessionLocal()
        try:
            devices = targets(event_devices(db, event_id) + sorted(wakeable(db, event_id)),
                              str(message.get("sender_id") or ""), message.get("recipient_ids"))
        finally:
            db.close()
        await wake(event_id, sorted(set(devices)), str(message.get("priority") or "routine"),
                   tag=notification_tag(event_id, message))
    except Exception:  # noqa: BLE001 - a wake-up failure never touches the message
        log.exception("wake-up for a message of %s failed", event_id)

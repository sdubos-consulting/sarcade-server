"""Email of the Access user in the main courante (decision 7 of 23 October
2026): once per operator, event and email every 12 hours, so the logbook
shows who really operates each identifier without one line per request."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import LogbookRow

from .verify import AccessIdentity

KIND = "access"
EVERY = timedelta(hours=12)
MAX_ACTOR = 64


def actor_of(object_type: str, payload: dict) -> str | None:
    """Identifier the operation is made under (device, sender or author)."""
    if not isinstance(payload, dict):
        return None
    keys = ("device_id",) if object_type == "position" else ()
    for key in (*keys, "sender_id", "updated_by", "created_by", "actor_id", "device_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_ACTOR]
    return None


def summary(actor: str, identity: AccessIdentity) -> str:
    if identity.kind == "service":
        return f"{actor} connecté par Cloudflare Access ({identity.label})"
    return f"{actor} connecté par Cloudflare Access : {identity.email}"


def record_access(db: Session, event_id: str, actor: str, identity: AccessIdentity, now: datetime) -> bool:
    """Adds the line when missing in the last 12 hours. Returns True if added."""
    text = summary(actor, identity)
    recent = db.scalar(select(LogbookRow.seq).where(
        LogbookRow.event_id == event_id, LogbookRow.kind == KIND, LogbookRow.actor_id == actor,
        LogbookRow.summary == text, LogbookRow.time >= now - EVERY).limit(1))
    if recent is not None:
        return False
    db.add(LogbookRow(event_id=event_id, kind=KIND, object_id=None, actor_id=actor, time=now, summary=text))
    return True

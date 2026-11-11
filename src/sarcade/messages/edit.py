# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Changing a sent message (card « Messages : modifier un message envoyé »,
lot 29, group 7): its author only, during 15 minutes; never a distress
message, an end of distress nor a move order, never a deleted message. Every
version is kept on the message and in the Main courante (PCO: « Historique »).
An acknowledgement already given stays (the application says « modifié
après accusé »).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from sarcade.db.models import LogbookRow, MessageRow

from . import deletion

EDIT_DELAY = timedelta(minutes=15)
MAX_BODY = 2048


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def allowed(row: MessageRow, actor: str, now: datetime) -> str | None:
    """None when the change is allowed, else the reason."""
    if deletion.protected(row):
        return "protected"
    if row.deleted_at is not None:
        return "deleted"
    if not actor or actor != row.sender_id:
        return "not_author"
    if now - _aware(row.created_at) > EDIT_DELAY:
        return "too_late"
    return None


def apply(db: Session, row: MessageRow, body: str, actor: str, now: datetime) -> dict:
    """Keeps the version before, writes the new text and its entry of the
    Main courante; returns the data of « message.updated »."""
    versions = list(row.versions or [])
    versions.append({"body": row.body, "at": _aware(row.edited_at or row.created_at).isoformat().replace("+00:00", "Z")})
    row.versions = versions
    row.body = body
    row.edited_at = now
    db.add(LogbookRow(event_id=row.event_id, kind="message_edited", object_id=row.id, actor_id=actor[:64],
                      summary=f"Message modifié : {body}"[:2000], time=now))
    db.flush()
    return {"id": row.id, "event_id": row.event_id, "body": body, "edited": True,
            "edited_at": now.isoformat().replace("+00:00", "Z"), "edited_by": actor}

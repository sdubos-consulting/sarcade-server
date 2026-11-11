# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Forwarded message (card « Messages : transférer un message », lot 29,
group 7): the new message carries ``forwarded_from`` = the original author
and time, read from the original on the server (never trusted from the
device); photos and positions follow in the body and attachments. The
forward is written in the Main courante."""
from __future__ import annotations

from datetime import UTC

from sqlalchemy.orm import Session

from sarcade.db.models import MessageRow


def clean_forwarded_from(db: Session, event_id: str, value: object) -> dict | None:
    """The origin to keep, or None (not a forward, unknown or foreign
    message). Raises ValueError("deleted") for a message deleted for all."""
    if not isinstance(value, dict):
        return None
    mid = str(value.get("message_id") or "").strip()[:64]
    if not mid:
        return None
    row = db.get(MessageRow, mid)
    if row is None or row.event_id != event_id:
        return None
    if row.deleted_at is not None:
        raise ValueError("deleted")
    at = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    return {"message_id": row.id, "sender_id": row.sender_id,
            "created_at": at.isoformat().replace("+00:00", "Z")}


def logbook_summary(summary: str, origin: dict | None) -> str:
    if not origin:
        return summary
    return f"Transféré (de {origin['sender_id']}, {origin['created_at']}) : {summary}"[:2000]

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Reactions on a message (card « Messages : réactions sur un message »,
lot 29, group 7): 👍 ❤️ 😂 😮 😢 🙏, one per person and message, changed or
removed by its author; never on a distress message nor an order; not an
acknowledgement (« Reçu », « Lu »… stay apart) and not in the Main courante.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import MessageRow, ReactionRow

from . import deletion

EMOJIS = ("👍", "❤️", "😂", "😮", "😢", "🙏")


def object_id(message_id: str, actor_id: str) -> str:
    return f"{message_id}:{actor_id}"


def apply(db: Session, event_id: str, payload: dict, now: datetime, remove: bool) -> dict:
    """Sets or removes the reaction; raises ValueError with the reason."""
    mid = str(payload.get("message_id") or "")[:64]
    actor = str(payload.get("actor_id") or "")[:64]
    msg = db.get(MessageRow, mid)
    if msg is None or msg.event_id != event_id or not actor:
        raise ValueError("unknown")
    if deletion.protected(msg) or msg.deleted_at is not None:
        raise ValueError("protected")
    row = db.get(ReactionRow, (mid, actor))
    if remove:
        if row is not None:
            db.delete(row)
        db.flush()
        return {"message_id": mid, "actor_id": actor, "emoji": None}
    emoji = str(payload.get("emoji") or "")
    if emoji not in EMOJIS:
        raise ValueError("emoji")
    if row is None:
        db.add(ReactionRow(message_id=mid, actor_id=actor, event_id=event_id, emoji=emoji, at=now))
    else:
        row.emoji, row.at = emoji, now
    db.flush()
    return {"message_id": mid, "actor_id": actor, "emoji": emoji}


def of_event(db: Session, event_id: str) -> list[dict]:
    rows = db.scalars(select(ReactionRow).where(ReactionRow.event_id == event_id).order_by(ReactionRow.at)).all()
    return [{"message_id": r.message_id, "actor_id": r.actor_id, "emoji": r.emoji, "at": r.at} for r in rows]

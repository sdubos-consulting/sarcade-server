# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Deletion of a message for everyone (decision of 29 Oct 2026).

The author may delete his message for everyone during 2 days after sending
it; the PCO may delete any message, at any time. Everyone then sees
« Ce message a été supprimé ». The original body stays on the server, in the
message row, for the PCO and the administrators only: it is removed from
the change feed and the logbook shown to the operators, and its photos can
no longer be downloaded by them.

Distress messages (« 🆘 … », priority ``distress``, their « Fin de détresse »)
and move orders (« 📍 Ordre de déplacement … ») are never deleted for
everyone, not even by the PCO (lot 21, 2 Nov 2026): « Supprimer pour moi »
stays possible on the device.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import LogbookRow, MessageRow, SyncOperationRow

AUTHOR_DELAY = timedelta(days=2)
DELETED_TEXT = "Ce message a été supprimé"
DELETED_BY_PCO_TEXT = "Ce message a été supprimé par le PCO"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


DISTRESS_END_PREFIXES = ("fin de détresse", "end of distress")
MOVE_ORDER_PREFIX = "📍 Ordre de déplacement"


def protected(row: MessageRow) -> bool:
    """Distress, end of distress and move orders: kept for everyone."""
    body = str(getattr(row, "body", "") or "").strip()
    priority = getattr(row, "priority", "") or ""
    if priority == "distress" or (priority == "immediate" and body.startswith("🆘")):
        return True
    return body.lower().startswith(DISTRESS_END_PREFIXES) or body.startswith(MOVE_ORDER_PREFIX)


def allowed(row: MessageRow, actor: str, pco: bool, now: datetime) -> tuple[bool, bool]:
    """(allowed, as_pco). The PCO deletes anything; the author during 2 days.
    Distress messages and move orders: nobody."""
    if protected(row):
        return False, False
    if pco:
        return True, True
    if actor and actor == row.sender_id and now - _aware(row.created_at) <= AUTHOR_DELAY:
        return True, False
    return False, False


def public_dict(row: MessageRow) -> dict:
    """The message as the operators see it: no body nor photo once deleted."""
    out = {"id": row.id, "event_id": row.event_id, "sender_id": row.sender_id,
           "recipient_ids": row.recipient_ids, "priority": row.priority, "body": row.body,
           "attachments": row.attachments or [], "created_at": row.created_at}
    if row.reply_to:
        out["reply_to"] = row.reply_to
    if getattr(row, "edited_at", None) is not None:
        out.update(edited=True, edited_at=row.edited_at)
    if row.deleted_at is not None:
        out.update(body="", attachments=[], deleted=True, deleted_at=row.deleted_at,
                   deleted_by=row.deleted_by, deleted_by_pco=bool(row.deleted_by_pco))
    return out


def pco_dict(row: MessageRow) -> dict:
    """Deleted message with its original content, for the PCO / administrators."""
    return {"id": row.id, "event_id": row.event_id, "sender_id": row.sender_id,
            "recipient_ids": row.recipient_ids, "priority": row.priority, "body": row.body,
            "attachments": row.attachments or [], "created_at": row.created_at,
            "reply_to": row.reply_to, "deleted_at": row.deleted_at, "deleted_by": row.deleted_by,
            "deleted_by_pco": bool(row.deleted_by_pco)}


def apply(db: Session, row: MessageRow, actor: str, as_pco: bool, now: datetime) -> dict:
    """Marks the message deleted and hides its content from the operators.
    Returns the realtime / change feed payload (without the content)."""
    row.deleted_at, row.deleted_by, row.deleted_by_pco = now, actor[:64], as_pco
    text = DELETED_BY_PCO_TEXT if as_pco else DELETED_TEXT
    # The creation stays in the change feed (cursor order), without content.
    for op in db.scalars(select(SyncOperationRow).where(
            SyncOperationRow.event_id == row.event_id, SyncOperationRow.object_id == row.id,
            SyncOperationRow.object_type == "message", SyncOperationRow.action == "create")).all():
        op.payload = {**(op.payload or {}), "body": "", "attachments": [], "deleted": True}
    for entry in db.scalars(select(LogbookRow).where(
            LogbookRow.event_id == row.event_id, LogbookRow.kind == "message",
            LogbookRow.object_id == row.id)).all():
        entry.summary = text
    db.add(LogbookRow(event_id=row.event_id, kind="message_deleted", object_id=row.id, actor_id=actor[:64],
                      summary=f"Message de {row.sender_id} supprimé pour tous" + (" (PCO)" if as_pco else ""),
                      time=now))
    db.flush()
    return {"id": row.id, "event_id": row.event_id, "deleted": True, "deleted_at": now.isoformat(),
            "deleted_by": row.deleted_by, "deleted_by_pco": as_pco}


def hidden_file_ids(db: Session, event_id: str) -> set[str]:
    """Photos of the deleted messages of the event."""
    ids: set[str] = set()
    for att in db.scalars(select(MessageRow.attachments).where(
            MessageRow.event_id == event_id, MessageRow.deleted_at.is_not(None))).all():
        ids.update(str(a.get("file_id")) for a in (att or []) if isinstance(a, dict))
    return ids

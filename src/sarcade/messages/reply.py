# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Reply to a message (card « répondre à un message », validated on
31 Oct 2026): a message may quote another message of the same event by its
id (``reply_to``). The quote is shown by the apps from the quoted message
they already have; a deleted quoted message reads « Message supprimé ».
An unknown or foreign id is dropped (the message is still delivered)."""

from sqlalchemy.orm import Session

from sarcade.db.models import MessageRow


def clean_reply_to(db: Session, event_id: str, value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    msg_id = value.strip()[:64]
    row = db.get(MessageRow, msg_id)
    if row is None or row.event_id != event_id:
        return None
    return msg_id

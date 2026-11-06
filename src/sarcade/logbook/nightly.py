# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Nightly check of the Main courante (card « Main courante infalsifiable »,
lot 29, group 8, choices 3 and 4).

Once a day, after 3 h (UTC), for every event with entries:
- the chain is verified; a break is written in the security journal
  (« suspicious ») and the administrators get an e-mail (the ticket);
- the fingerprint of the day (last entry, its hash) is written in the
  security journal, which the SIEM receives when it is plugged: the proof
  is kept outside the server.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.message import EmailMessage
import logging
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sarcade.db.models import LogbookRow
from sarcade.updates.service import get_value, set_value

from . import chain

log = logging.getLogger(__name__)
STATE_KEY = "logbook_nightly"
HOUR_UTC = 3
LOOP_S = 1800


def due(last_day: str | None, now: datetime) -> bool:
    """Not done today yet, and past 3 h UTC."""
    return now.hour >= HOUR_UTC and last_day != now.date().isoformat()


def broken_message(sender: str, to: str, event_id: str, result: dict) -> EmailMessage:
    from email.utils import formataddr, make_msgid, parseaddr
    msg = EmailMessage()
    name, address = parseaddr(sender)
    msg["From"] = formataddr((name or "SARCADE", address or sender))
    msg["To"] = to
    msg["Subject"] = f"SARCADE : main courante modifiée ({event_id})"
    msg["Message-ID"] = make_msgid(domain=(address or "sarcade.org").rpartition("@")[2] or None)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content("\n".join([
        "Bonjour,", "",
        f"Le contrôle de nuit a trouvé la main courante de l'événement {event_id} modifiée "
        f"à l'entrée n° {result['broken_at']} ({result['reason']}).", "",
        "Détail : Paramètres › Diagnostic › Main courante, et le journal de sécurité.", "", "SARCADE", ""]))
    return msg


def run(db: Session, now: datetime | None = None, sender=None) -> list[dict]:
    """The check of every event; returns one result per event. The caller
    commits."""
    from sarcade.accounts import mailer
    from sarcade.deadlines import admin_emails
    from sarcade.security import journal
    now = now or datetime.now(UTC)
    out = []
    for event_id in db.scalars(select(LogbookRow.event_id).distinct().order_by(LogbookRow.event_id)).all():
        result = chain.verify(db, event_id)
        last = db.execute(select(LogbookRow.seq, LogbookRow.hash).where(LogbookRow.event_id == event_id)
                          .order_by(LogbookRow.seq.desc()).limit(1)).first()
        count = db.scalar(select(func.count()).select_from(LogbookRow).where(LogbookRow.event_id == event_id)) or 0
        # Fingerprint of the day: to the SIEM with the security journal.
        journal.record(db, "journal", "logbook_daily_hash", "info" if result["ok"] else "failure", actor="SARCADE",
                       event_id=event_id, now=now,
                       details={"last_seq": last.seq if last else None, "last_hash": last.hash if last else None,
                                "entries": count, "intact": result["ok"]})
        emailed = 0
        if not result["ok"]:
            journal.record(db, "suspicious", "logbook_chain_broken", "failure", actor="SARCADE", event_id=event_id, now=now,
                           details={"broken_at": result["broken_at"], "reason": result["reason"]})
            cfg = mailer.config()
            if cfg is not None:
                send = sender or mailer.send
                for to in admin_emails(db):
                    try:
                        send(cfg, broken_message(cfg.sender, to, event_id, result))
                        emailed += 1
                    except mailer.MailError as exc:
                        mailer.record(db, False, now, str(exc))
                if emailed:
                    mailer.record(db, True, now)
        out.append({"event_id": event_id, **result, "entries": count, "emailed": emailed})
    set_value(db, STATE_KEY, {"day": now.date().isoformat(), "at": now.isoformat(),
                              "broken": [r["event_id"] for r in out if not r["ok"]]}, now)
    return out


def _run_if_due() -> None:
    from sarcade.db.session import SessionLocal
    db = SessionLocal()
    try:
        state = get_value(db, STATE_KEY) or {}
        now = datetime.now(UTC)
        if due(state.get("day") if isinstance(state, dict) else None, now):
            run(db, now)
            db.commit()
    finally:
        db.close()


async def loop() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await asyncio.to_thread(_run_if_due)
        except Exception as exc:  # noqa: BLE001
            log.warning("logbook nightly check failed: %s", exc)
        await asyncio.sleep(LOOP_S)


def start(tasks: list) -> None:
    if os.getenv("SARCADE_LOGBOOK_NIGHTLY", "1") != "0":
        tasks.append(asyncio.create_task(loop()))

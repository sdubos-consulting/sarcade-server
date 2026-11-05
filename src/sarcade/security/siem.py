"""Connection to a SIEM (SARCADE Pro, module « siem »). Wazuh is the
reference SIEM; syslog RFC 5424 and JSON keep Elastic, Splunk or Microsoft
Sentinel possible.

Transport: syslog over TCP, encrypted with TLS by default (RFC 5425, octet
counting framing), or a journal file collected by the SIEM agent. Entries
are kept in the local journal while the SIEM cannot be reached and are sent
when the connection comes back, without loss (cursor in server_settings).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import ssl
from datetime import UTC, datetime, timedelta  # noqa: I001 (lot 29: kept apart from the format PR)

from sqlalchemy import select

from sarcade import licensing
from sarcade.db.models import SecurityEventRow
from sarcade.db.session import SessionLocal
from sarcade.updates.service import get_value, set_value

from . import journal

log = logging.getLogger("sarcade.siem")
CURSOR_KEY = "siem_cursor"
state = {"enabled": False, "connected": False, "last_error": None, "last_error_at": None, "sent": 0}


def config() -> dict:
    return {"host": os.getenv("SARCADE_SIEM_HOST", ""), "port": int(os.getenv("SARCADE_SIEM_PORT", "6514")),
            "tls": os.getenv("SARCADE_SIEM_TLS", "1") != "0", "ca": os.getenv("SARCADE_SIEM_CA", ""),
            "format": os.getenv("SARCADE_SIEM_FORMAT", "syslog"), "file": os.getenv("SARCADE_SIEM_FILE", "")}


def frame(line: str) -> bytes:
    data = line.encode()
    return str(len(data)).encode() + b" " + data


def render(row: SecurityEventRow, fmt: str) -> str:
    return journal.to_json_line(row) if fmt == "json" else journal.to_syslog(row)


def pending(limit: int = 500) -> tuple[int, list[SecurityEventRow]]:
    db = SessionLocal()
    try:
        cursor = int((get_value(db, CURSOR_KEY, {}) or {}).get("seq", 0))
        rows = db.scalars(select(SecurityEventRow).where(SecurityEventRow.seq > cursor)
                          .order_by(SecurityEventRow.seq).limit(limit)).all()
        db.expunge_all()
        return cursor, rows
    finally:
        db.close()


def save_cursor(seq: int) -> None:
    db = SessionLocal()
    try:
        set_value(db, CURSOR_KEY, {"seq": seq})
        db.commit()
    finally:
        db.close()


async def _open(cfg: dict):
    context = None
    if cfg["tls"]:
        context = ssl.create_default_context(cafile=cfg["ca"] or None)
    return await asyncio.open_connection(cfg["host"], cfg["port"], ssl=context)


async def forward_loop() -> None:
    cfg = config()
    writer = None
    warned = False
    while True:
        # Pro module « siem »: forwarding follows the licence, checked every
        # day; entries wait in the journal while the module is not licensed.
        state["enabled"] = licensing.pro_enabled("siem")
        if not state["enabled"]:
            if not warned:
                log.warning("SIEM configured but the « siem » Pro module is not licensed: not forwarded")
                warned = True
            if writer is not None:
                writer.close()
                writer, state["connected"] = None, False
            await asyncio.sleep(15)
            continue
        warned = False
        try:
            _, rows = await asyncio.to_thread(pending)
            if rows:
                lines = [render(r, cfg["format"]) for r in rows]
                if cfg["file"]:
                    path = Path(cfg["file"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as fh:
                        fh.writelines(line + "\n" for line in lines)
                if cfg["host"]:
                    if writer is None:
                        _, writer = await _open(cfg)
                        state["connected"] = True
                    for line in lines:
                        writer.write(frame(line))
                    await writer.drain()
                await asyncio.to_thread(save_cursor, rows[-1].seq)
                state["sent"] += len(rows)
                state["last_error"] = None
                continue
        except (OSError, ssl.SSLError) as exc:
            state["connected"], state["last_error"] = False, type(exc).__name__
            state["last_error_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            log.warning("SIEM unreachable (%s), entries kept locally", exc)
            if writer is not None:
                writer.close()
            writer = None
            await asyncio.sleep(30)
            continue
        except Exception as exc:  # noqa: BLE001
            state["last_error"] = type(exc).__name__
            state["last_error_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            log.warning("SIEM forwarding error: %s", exc)
        await asyncio.sleep(5)


#: Entries waiting longer than this: orange; longer than RED_AFTER: red.
ORANGE_AFTER = timedelta(minutes=15)
RED_AFTER = timedelta(hours=24)


def status(db, now: datetime | None = None) -> dict:
    """Section « SIEM » of the administrator Diagnostic (card « Diagnostic :
    ligne SIEM pour l'administrateur », lot 29, group 8): state, destination,
    entries sent and waiting, last error."""
    from sqlalchemy import func
    now = now or datetime.now(UTC)
    cfg = config()
    configured = bool(cfg["host"] or cfg["file"])
    cursor = int((get_value(db, CURSOR_KEY, {}) or {}).get("seq", 0))
    waiting = db.scalar(select(func.count()).select_from(SecurityEventRow).where(SecurityEventRow.seq > cursor)) or 0
    oldest = db.scalar(select(func.min(SecurityEventRow.at)).where(SecurityEventRow.seq > cursor)) if waiting else None
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    licensed = licensing.pro_enabled("siem")
    if not configured:
        st = "off"
    elif not licensed:
        st = "unlicensed"
    elif state["connected"] or (cfg["file"] and not cfg["host"]):
        st = "connected"
    elif state["last_error"]:
        st = "unreachable"
    else:
        st = "waiting"
    age = now - oldest if oldest is not None else None
    tone = None if not configured else ("red" if age is not None and age > RED_AFTER else
                                        "orange" if (age is not None and age > ORANGE_AFTER) or st == "unreachable" else
                                        "green" if st == "connected" else "orange")
    return {"state": st, "tone": tone, "host": cfg["host"] or None, "port": cfg["port"] if cfg["host"] else None,
            "tls": cfg["tls"] if cfg["host"] else None, "format": cfg["format"], "file": bool(cfg["file"]),
            "sent": state["sent"], "waiting": waiting,
            "oldest_waiting_at": oldest.isoformat().replace("+00:00", "Z") if oldest else None,
            "last_error": state["last_error"], "last_error_at": state["last_error_at"]}


def start(tasks: list) -> None:
    cfg = config()
    if not (cfg["host"] or cfg["file"]):
        return
    tasks.append(asyncio.create_task(forward_loop()))

"""Local security journal (Core, free on every installation).

Distinct from the logbook: the logbook traces the operation, this journal
traces what touches access and integrity of the solution. Each entry is
chained to the previous one (SHA-256), so that a later modification or
deletion is detected. Kept one year by default (decision of 10 Oct 2026,
CNIL recommendation on logging, 2021).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import re
import socket

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from sarcade.db.models import SecurityEventRow

GENESIS = "0" * 64
CATEGORIES = ("auth", "device", "rights", "admin", "update", "suspicious", "journal")
OUTCOMES = ("success", "failure", "refused", "deferred", "info")
# Never in the journal: content of messages, photos, positions of operators.
_FORBIDDEN_KEYS = {"body", "text", "message", "photo", "photos", "attachments", "lat", "lon", "latitude",
                   "longitude", "position", "positions", "geometry", "points", "password", "token", "secret"}
_LOCK_KEY = 7_316_002  # PostgreSQL advisory lock serialising the chain


def server_id() -> str:
    return os.getenv("SARCADE_SERVER_ID") or socket.gethostname()


def sanitize(details: dict | None) -> dict:
    """Keeps short scalar details only, never forbidden keys."""
    out = {}
    for key, value in (details or {}).items():
        key = str(key)[:40]
        if key.lower() in _FORBIDDEN_KEYS:
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v)[:80] for v in value[:20]]
        else:
            out[key] = str(value)[:200]
    return out


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def fields(row) -> dict:
    return {"at": iso(row.at), "category": row.category, "action": row.action, "outcome": row.outcome,
            "actor": row.actor, "device_id": row.device_id, "event_id": row.event_id,
            "source_ip": row.source_ip, "details": row.details or {}}


def chain_hash(prev_hash: str, entry: dict) -> str:
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + "\n" + canonical).encode()).hexdigest()


def record(db: Session, category: str, action: str, outcome: str = "success", *, actor: str | None = None,
           device_id: str | None = None, event_id: str | None = None, source_ip: str | None = None,
           details: dict | None = None, now: datetime | None = None) -> SecurityEventRow:
    """Adds an entry; the caller commits. Entries are serialised so that the
    chain stays linear even with concurrent requests."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome}")
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    last = db.scalars(select(SecurityEventRow).order_by(SecurityEventRow.seq.desc()).limit(1)).first()
    prev = last.hash if last is not None else GENESIS
    at = now or datetime.now(UTC)
    last_at = None if last is None else (last.at if last.at.tzinfo else last.at.replace(tzinfo=UTC))
    if last_at is not None and at < last_at:
        at = last_at  # chronological order follows the chain
    row = SecurityEventRow(at=at, category=category, action=action[:64], outcome=outcome,
                           actor=(actor or None) and actor[:64], device_id=(device_id or None) and device_id[:64],
                           event_id=(event_id or None) and event_id[:64],
                           source_ip=(source_ip or None) and source_ip[:64], details=sanitize(details),
                           prev_hash=prev, hash="")
    row.hash = chain_hash(prev, fields(row))
    db.add(row)
    db.flush()
    return row


def record_now(category: str, action: str, outcome: str = "success", **kw) -> None:
    """Records in its own transaction (middleware, background tasks)."""
    from sarcade.db.session import SessionLocal
    db = SessionLocal()
    try:
        record(db, category, action, outcome, **kw)
        db.commit()
    finally:
        db.close()


def verify(db: Session) -> dict:
    """Checks the chain. After a retention purge, the first remaining entry
    is the anchor (its predecessor was purged and the purge is journaled)."""
    prev = None
    count = 0
    for row in db.scalars(select(SecurityEventRow).order_by(SecurityEventRow.seq)).yield_per(500):
        if prev is not None and row.prev_hash != prev:
            return {"ok": False, "count": count, "broken_at": row.seq, "reason": "link"}
        if chain_hash(row.prev_hash, fields(row)) != row.hash:
            return {"ok": False, "count": count, "broken_at": row.seq, "reason": "content"}
        prev = row.hash
        count += 1
    return {"ok": True, "count": count, "broken_at": None, "reason": None}


def purge(db: Session, retention_days: int, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    limit = now - timedelta(days=retention_days)
    n = db.scalar(select(func.count()).select_from(SecurityEventRow).where(SecurityEventRow.at < limit)) or 0
    if n:
        db.execute(delete(SecurityEventRow).where(SecurityEventRow.at < limit))
        record(db, "journal", "retention_purge", "info", actor="SARCADE",
               details={"deleted": n, "retention_days": retention_days}, now=now)
    return n


def as_dict(row: SecurityEventRow) -> dict:
    d = fields(row)
    d.update(seq=row.seq, server=server_id(), hash=row.hash, prev_hash=row.prev_hash)
    return d


# --- SIEM formats (Pro): structured JSON and syslog RFC 5424 ---------------

SEVERITY = {"failure": 4, "refused": 4, "deferred": 5, "info": 6, "success": 6}
FACILITY_AUDIT = 13  # « log audit »
# Private Enterprise Number used in structured data. 32473 is the number
# reserved for documentation (RFC 5612): to replace by an IANA number.
PEN = os.getenv("SARCADE_SYSLOG_PEN", "32473")
_SD_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")


def to_json_line(row: SecurityEventRow) -> str:
    return json.dumps(dict(as_dict(row), app="sarcade"), sort_keys=True, ensure_ascii=False)


def _sd_value(value) -> str:
    s = "" if value is None else (json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value))
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def to_syslog(row: SecurityEventRow) -> str:
    severity = 2 if row.category == "suspicious" else SEVERITY.get(row.outcome, 6)
    pri = FACILITY_AUDIT * 8 + severity
    params = {"seq": row.seq, "category": row.category, "outcome": row.outcome, "actor": row.actor,
              "device": row.device_id, "event": row.event_id, "src": row.source_ip, "hash": row.hash}
    sd = " ".join(f'{k}="{_sd_value(v)}"' for k, v in params.items() if v is not None)
    details = " ".join(f'{k}="{_sd_value(v)}"' for k, v in sorted((row.details or {}).items()) if _SD_NAME.match(k))
    host = server_id().replace(" ", "-")[:255] or "-"
    msg = f"{row.category}.{row.action} {row.outcome}"
    structured = f"[sarcade@{PEN} {sd}]" + (f"[details@{PEN} {details}]" if details else "")
    return f"<{pri}>1 {iso(row.at)} {host} sarcade - {row.category}.{row.action} {structured} {msg}"

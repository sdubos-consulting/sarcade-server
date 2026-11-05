# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Tamper-evident Main courante (card « Main courante infalsifiable », lot 29,
group 8): each new entry of an event carries the SHA-256 of the previous one
of the same event, like the security journal. A change or a deletion made
directly in the database breaks the chain and is found by :func:`verify`.
Entries written before this version have no hash (« avant chaînage »).
A correction is a new entry; nothing in SARCADE changes an entry.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import zlib

from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import Session

from sarcade.db.models import LogbookRow

GENESIS = "0" * 64


def _iso(t: datetime) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def entry_fields(row: LogbookRow) -> dict:
    return {"event_id": row.event_id, "kind": row.kind, "object_id": row.object_id, "actor_id": row.actor_id,
            "summary": row.summary, "time": _iso(row.time)}


def entry_hash(prev_hash: str, row: LogbookRow) -> str:
    canonical = json.dumps(entry_fields(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + "\n" + canonical).encode()).hexdigest()


def _last_hash(session: Session, event_id: str) -> str:
    with session.no_autoflush:
        last = session.scalars(select(LogbookRow.hash).where(LogbookRow.event_id == event_id, LogbookRow.hash.is_not(None))
                               .order_by(LogbookRow.seq.desc()).limit(1)).first()
    return last or GENESIS


def _before_flush(session: Session, _ctx, _instances) -> None:
    new = [o for o in session.new if isinstance(o, LogbookRow) and o.hash is None]
    if not new:
        return
    new.sort(key=lambda o: inspect(o).insert_order)
    postgres = session.get_bind().dialect.name == "postgresql"
    last: dict[str, str] = {}
    for row in new:
        if row.event_id not in last:
            if postgres:  # one chain per event, written one at a time
                session.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"),
                                {"a": 7_316_003, "b": zlib.crc32(row.event_id.encode()) & 0x7FFFFFFF})
            last[row.event_id] = _last_hash(session, row.event_id)
        row.prev_hash = last[row.event_id]
        row.hash = entry_hash(row.prev_hash, row)
        last[row.event_id] = row.hash


_installed = False


def install() -> None:
    """Chains every new entry of the Main courante (once per process)."""
    global _installed
    if not _installed:
        event.listen(Session, "before_flush", _before_flush)
        _installed = True


def verify(session: Session, event_id: str) -> dict:
    """« Main courante intègre », or the entry where the chain breaks."""
    prev = None
    chained = before = 0
    for row in session.scalars(select(LogbookRow).where(LogbookRow.event_id == event_id).order_by(LogbookRow.seq)).yield_per(500):
        if row.hash is None:
            if prev is None:
                before += 1
                continue
            return {"ok": False, "chained": chained, "before_chaining": before, "broken_at": row.seq, "reason": "missing"}
        if prev is not None and row.prev_hash != prev:
            return {"ok": False, "chained": chained, "before_chaining": before, "broken_at": row.seq, "reason": "link"}
        if prev is None and row.prev_hash != GENESIS:
            return {"ok": False, "chained": chained, "before_chaining": before, "broken_at": row.seq, "reason": "link"}
        if entry_hash(row.prev_hash, row) != row.hash:
            return {"ok": False, "chained": chained, "before_chaining": before, "broken_at": row.seq, "reason": "content"}
        prev = row.hash
        chained += 1
    return {"ok": True, "chained": chained, "before_chaining": before, "broken_at": None, "reason": None}

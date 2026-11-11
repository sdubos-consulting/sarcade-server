# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Common event format (card « SIEM, en avance de phase : format d'événement
commun », lot 29, group 8): one shape for the Main courante, the security
journal and the exports, with field names of ECS (Elastic Common Schema),
read as they are by Wazuh, Elastic, Splunk and Sentinel. See
docs/event-format-v0.1.md.
"""
from __future__ import annotations

from datetime import UTC, datetime
import re

from . import journal

_OUTCOME = {"success": "success", "failure": "failure", "refused": "failure"}
_CATEGORY = {"auth": ["authentication"], "device": ["host"], "rights": ["iam"], "admin": ["configuration"],
             "update": ["package"], "suspicious": ["intrusion_detection"], "journal": ["database"]}
_POSITION = re.compile(r"\((-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)\)")


def timestamp(t: datetime) -> str:
    """UTC to the millisecond: « 2026-11-05T21:17:04.123Z »."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _observer() -> dict:
    return {"name": journal.server_id(), "product": "SARCADE", "vendor": "SDUBOS Consulting"}


def _clean(d: dict) -> dict:
    """Drops the empty values, at every level."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = _clean(v)
        if v not in (None, {}, [], ""):
            out[k] = v
    return out


def from_security(row) -> dict:
    """An entry of the security journal in the common format."""
    return _clean({
        "@timestamp": timestamp(row.at),
        "event": {"id": f"sec-{row.seq}", "kind": "event", "dataset": "sarcade.security",
                  "category": _CATEGORY.get(row.category, []), "action": f"{row.category}.{row.action}",
                  "outcome": _OUTCOME.get(row.outcome, "unknown"), "sequence": row.seq, "hash": row.hash},
        "user": {"id": row.actor}, "host": {"id": row.device_id}, "source": {"ip": row.source_ip},
        "observer": _observer(),
        "sarcade": {"event_id": row.event_id, "outcome": row.outcome, "details": row.details or {}},
    })


def from_logbook(row) -> dict:
    """An entry of the Main courante in the common format; the position is
    read from its text when it carries one (« (48.80000, 2.13000) »)."""
    m = _POSITION.search(row.summary or "")
    return _clean({
        "@timestamp": timestamp(row.time),
        "event": {"id": f"log-{row.seq}", "kind": "event", "dataset": "sarcade.logbook", "action": row.kind,
                  "outcome": "unknown", "sequence": row.seq, "hash": getattr(row, "hash", None)},
        "message": row.summary,
        "user": {"id": row.actor_id},
        "geo": {"location": {"lat": float(m.group(1)), "lon": float(m.group(2))}} if m else {},
        "observer": _observer(),
        "sarcade": {"event_id": row.event_id, "object_id": row.object_id},
    })


#: Columns of a CSV in the common format, in this order.
CSV_COLUMNS = ["event.id", "@timestamp", "event.dataset", "event.action", "user.id", "host.id", "sarcade.event_id",
               "geo.location.lat", "geo.location.lon", "event.outcome", "message", "event.hash"]


def flat(doc: dict, prefix: str = "") -> dict:
    """« event.id »: one level, for the CSV."""
    out = {}
    for k, v in doc.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def csv_text(docs) -> str:
    """CSV in the common format (« ; » for the spreadsheets in French, a
    header row, the columns of [CSV_COLUMNS]); each row carries its
    fingerprint (event.hash)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for doc in docs:
        row = flat(doc)
        w.writerow(["" if row.get(c) is None else row.get(c) for c in CSV_COLUMNS])
    return buf.getvalue()

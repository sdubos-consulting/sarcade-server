"""SARCADE Pro modules (note 11 « Architecture modulaire et SARCADE Pro »).

The Pro modules (``siem``, ``assist``) are enabled by a signed
licence file verified offline (``core.py``); the code stays open source
(AGPL) and Core never depends on a licence. The licence is checked at start
and every day; the administrator uploads it in the administration tool.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path

from .core import GRACE_DAYS, MODULES, LicenceState, verify

log = logging.getLogger("sarcade.licensing")
KNOWN = set(MODULES)
CHECK_INTERVAL_S = 24 * 3600
_state: LicenceState | None = None


def licence_path() -> Path:
    default = Path(os.getenv("SARCADE_FILE_ROOT", "/var/lib/sarcade/files")) / "licence.json"
    return Path(os.getenv("SARCADE_LICENCE_FILE", str(default)))


def read_document() -> dict | None:
    path = licence_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"format": "unreadable"}


def check(now: datetime | None = None) -> LicenceState:
    """Verifies the installed licence (start, every day, after an upload)."""
    global _state
    previous = _state.status if _state else None
    _state = verify(read_document(), now)
    if _state.status != previous:
        _journal(_state, previous)
    return _state


def state() -> LicenceState:
    return _state if _state is not None else check()


def enabled_modules() -> set[str]:
    return set(state().modules)


def pro_enabled(module: str) -> bool:
    return module in enabled_modules()


def install(document: dict, now: datetime | None = None) -> LicenceState:
    """Checks then installs an uploaded licence; an invalid or expired one
    never replaces the installed licence."""
    candidate = verify(document, now)
    if candidate.status in ("invalid", "expired", "none"):
        return candidate
    path = licence_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return check(now)


def client_view() -> dict:
    """What the applications need: Pro features shown when the server says so."""
    s = state()
    return {"modules": sorted(s.modules), "status": s.status}


def _journal(s: LicenceState, previous: str | None) -> None:
    try:
        from sarcade.db.session import SessionLocal
        from sarcade.security import journal
    except Exception:  # noqa: BLE001 - journal unavailable (unit tests)
        return
    outcome = {"valid": "success", "expiring": "info", "grace": "info", "none": "info"}.get(s.status, "failure")
    try:
        db = SessionLocal()
        try:
            journal.record(db, "admin", "licence_checked", outcome, details={
                "status": s.status, "previous": previous, "reason": s.reason,
                "licence_id": (s.licence or {}).get("id"), "modules": ",".join(sorted(s.modules)),
                "test": s.test, "days_left": s.days_left})
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - database not ready yet
        log.debug("licence state not journaled: %s", exc)


async def check_loop() -> None:
    while True:
        s = await asyncio.to_thread(check)
        if s.status == "grace":
            log.warning("SARCADE Pro licence expired: Pro modules stop in %d days", GRACE_DAYS + (s.days_left or 0))
        elif s.status == "expiring":
            log.warning("SARCADE Pro licence expires in %d days", s.days_left or 0)
        elif s.status in ("invalid", "expired"):
            log.warning("SARCADE Pro licence %s (%s): Pro modules disabled", s.status, s.reason)
        await asyncio.sleep(CHECK_INTERVAL_S)


def start(tasks: list) -> None:
    tasks.append(asyncio.create_task(check_loop()))


__all__ = ["KNOWN", "MODULES", "check", "state", "enabled_modules", "pro_enabled", "install", "client_view",
           "start", "licence_path"]

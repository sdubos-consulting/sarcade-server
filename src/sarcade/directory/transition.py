# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Transition period (decision 6 of 3 Nov 2026): devices without account
keep the rights of the Core (Public) and offer « Se connecter »; until the
end date chosen by the administrator (``transition_until``), the PCO is
still recognised by its name (``PCO``, ``PCO-…``) and a device without
account keeps the behaviour of before (every event visible, PCO checks
without identity left open). No date: the transition goes on."""
from __future__ import annotations

from datetime import UTC, datetime
import time

from sqlalchemy.orm import Session

from sarcade.updates.service import get_value, set_value

KEY = "transition"
REFRESH_S = 30.0
_cached: dict = {"until": None, "read_at": 0.0}


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def until(db: Session) -> datetime | None:
    v = get_value(db, KEY, {}) or {}
    return _parse(v.get("until")) if isinstance(v, dict) else None


def set_until(db: Session, value: datetime | None, by: str, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    set_value(db, KEY, {"until": value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None,
                        "set_by": by[:64], "set_at": now.isoformat()}, now)
    db.flush()
    _cached.update(until=value, read_at=time.monotonic())


def refresh(db: Session) -> None:
    """Re-read the date at most every 30 s (called before every route)."""
    if time.monotonic() - _cached["read_at"] < REFRESH_S:
        return
    try:
        _cached.update(until=until(db), read_at=time.monotonic())
    except Exception:  # noqa: BLE001 - settings unavailable (unit tests): keep the last value
        _cached["read_at"] = time.monotonic()


def active(now: datetime | None = None, value: datetime | None = ...) -> bool:  # type: ignore[assignment]
    t = _cached["until"] if value is ... else value
    return t is None or (now or datetime.now(UTC)) < t


def state(db: Session | None = None, now: datetime | None = None) -> dict:
    t = until(db) if db is not None else _cached["until"]
    on = active(now, t)
    return {"active": on, "until": t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None,
            "pco_by_name": on, "anonymous_rights": "legacy" if on else "core"}


def reset_cache() -> None:
    """Tests."""
    _cached.update(until=None, read_at=0.0)

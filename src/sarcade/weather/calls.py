# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Calls to the weather sources: counters of the day and automatic pauses
(card « Météo : limiter les appels à Météo-France (et aux autres sources
météo) », validated on 10 Nov 2026, choices 4 and 5).

* After a 401 or 403 (key refused, API not subscribed): no call to this
  source for 6 hours.
* After a 429 (limit reached): wait for ``Retry-After`` (seconds or HTTP
  date), else 15 minutes.
* Counters per source and per day (Europe/Paris), in memory: shown to the
  administrator in Diagnostic › Serveur. A restart starts them again.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
import threading
from zoneinfo import ZoneInfo

SOURCES = ("radar", "vigilance", "forecast")
REFUSED_PAUSE = timedelta(hours=6)
RATE_PAUSE = timedelta(minutes=15)
MAX_RATE_PAUSE = timedelta(hours=24)
TZ = ZoneInfo("Europe/Paris")


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


def retry_after(value: str | None, now: datetime) -> timedelta:
    """Delay of a 429: ``Retry-After`` in seconds or as an HTTP date,
    15 minutes without it, 24 hours at most."""
    delay = None
    if value:
        value = value.strip()
        if value.isdigit():
            delay = timedelta(seconds=int(value))
        else:
            try:
                at = parsedate_to_datetime(value)
                delay = at.astimezone(UTC) - now if at.tzinfo else None
            except (TypeError, ValueError):
                delay = None
    if delay is None or delay <= timedelta(0):
        delay = RATE_PAUSE
    return min(delay, MAX_RATE_PAUSE)


@dataclass
class Source:
    day: date | None = None
    calls: int = 0
    calls_yesterday: int = 0
    failed: int = 0
    last_call_at: datetime | None = None
    last_status: int | None = None
    last_error: str | None = None
    last_ok_at: datetime | None = None
    paused_until: datetime | None = None
    pause_reason: str | None = None   # refused or rate_limited


class Calls:
    def __init__(self):
        self._lock = threading.Lock()
        self.sources: dict[str, Source] = {s: Source() for s in SOURCES}

    def reset(self) -> None:
        with self._lock:
            self.sources = {s: Source() for s in SOURCES}

    def _roll(self, s: Source, now: datetime) -> None:
        today = now.astimezone(TZ).date()
        if s.day != today:
            s.calls_yesterday = s.calls if s.day == today - timedelta(days=1) else 0
            s.day, s.calls, s.failed = today, 0, 0

    def paused(self, source: str, now: datetime | None = None) -> datetime | None:
        """End of the pause of ``source``, or None."""
        now = now or datetime.now(UTC)
        s = self.sources[source]
        return s.paused_until if s.paused_until and now < s.paused_until else None

    def allowed(self, source: str, now: datetime | None = None) -> bool:
        return self.paused(source, now) is None

    def record(self, source: str, status: int | None, now: datetime | None = None,
               retry: str | None = None, error: str | None = None) -> None:
        """One call to ``source``: HTTP status, or None when the network failed."""
        now = now or datetime.now(UTC)
        with self._lock:
            s = self.sources[source]
            self._roll(s, now)
            s.calls += 1
            s.last_call_at, s.last_status = now, status
            if status is not None and 200 <= status < 300:
                s.last_ok_at, s.last_error = now, None
                s.paused_until = s.pause_reason = None
                return
            s.failed += 1
            s.last_error = (error or (f"HTTP {status}" if status is not None else "réseau"))[:200]
            if status in (401, 403):
                s.paused_until, s.pause_reason = now + REFUSED_PAUSE, "refused"
            elif status == 429:
                s.paused_until, s.pause_reason = now + retry_after(retry, now), "rate_limited"

    def state(self, source: str, now: datetime) -> str:
        """ok, refused, rate_limited, error or idle (no call yet)."""
        s = self.sources[source]
        if self.paused(source, now):
            return s.pause_reason or "refused"
        if s.last_call_at is None:
            return "idle"
        return "ok" if s.last_error is None else "error"

    def snapshot(self, now: datetime | None = None) -> dict:
        """Diagnostic › Serveur of the administrator (choices 4 and 5)."""
        now = now or datetime.now(UTC)
        out = {}
        with self._lock:
            for name, s in self.sources.items():
                self._roll(s, now)
                out[name] = {"state": self.state(name, now), "calls_today": s.calls,
                             "calls_yesterday": s.calls_yesterday, "failed_today": s.failed,
                             "last_call_at": _iso(s.last_call_at), "last_status": s.last_status,
                             "last_ok_at": _iso(s.last_ok_at), "last_error": s.last_error,
                             "paused_until": _iso(self.paused(name, now))}
        return {"day": now.astimezone(TZ).date().isoformat(), "sources": out}


calls = Calls()

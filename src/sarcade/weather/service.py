"""Weather on the server: fetched when Internet is available, cached and
served to the clients over the local network (note « Prévisions météo »).

Card « Météo : limiter les appels à Météo-France (et aux autres sources
météo) », validated on 10 Nov 2026 (choices 1 to 11, choice 1 changed by
the porteur):

* only **active events** are followed: open, and not a draft without any
  position for 24 hours (choice 2); no active event, no call at all;
* **vigilance**: one national map every 5 minutes for all the events
  (choice 1 as changed), each event reads its departments in it;
* **forecasts**: shared by the events on the same point rounded to 0.1°
  (about 10 km), once an hour per point (choice 3);
* pauses after a refusal or a limit and counters of the day
  (``calls.py``, choices 4 and 5);
* **department** deduced from the forecast point and from the operators'
  positions (choice 9), unless the PCO sets one (choice 10); several
  departments: the highest vigilance, with the department concerned
  (choice 11).

Configuration (environment):

* ``SARCADE_WEATHER`` (default ``1``): refresh of the active events.
* ``SARCADE_OPEN_METEO_URL`` (default ``https://api.open-meteo.com``) and
  ``SARCADE_OPEN_METEO_API_KEY``: commercial use of Open-Meteo (SARCADE Pro)
  needs a subscription and the customer URL.
* ``SARCADE_METEOFRANCE_API_KEY``, ``SARCADE_VIGILANCE_URL``: Météo-France
  vigilance (portal key); ``SARCADE_DEPARTMENT``: department used when none
  is set nor deduced; ``SARCADE_METEOFRANCE_KEY_EXPIRES``: end of validity of
  the key (ISO date, optional), shown in the « Échéances ».
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
import logging
import os

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sarcade.db.models import EventRow, LogbookRow, PositionRow, WeatherCacheRow
from sarcade.db.session import SessionLocal
from sarcade.events import settings as event_settings
from sarcade.realtime.manager import manager

from . import forecast as fc
from . import vigilance as vg
from .calls import calls

log = logging.getLogger("sarcade.weather")
TICK_SECONDS = 60
VIGILANCE_INTERVAL = timedelta(minutes=5)
FORECAST_INTERVAL = timedelta(hours=1)
REFRESH_SECONDS = int(FORECAST_INTERVAL.total_seconds())
RETRY_AFTER_FAILURE = timedelta(minutes=15)
NATIONAL_MAX_AGE = timedelta(hours=1)
ACTIVE_WINDOW = timedelta(hours=24)
OPERATORS_WINDOW = timedelta(hours=2)
GRID = 0.1
MAX_DEPARTMENTS = 6


class WeatherUnavailable(RuntimeError):
    pass


class Paused(RuntimeError):
    """The source is paused after a refusal or a limit (choice 4)."""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _iso(t: datetime | None) -> str | None:
    return _aware(t).astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


async def _get(source: str, client: httpx.AsyncClient, url: str, headers: dict | None = None,
               now: datetime | None = None) -> httpx.Response:
    """One counted call; refused while the source is paused."""
    if not calls.allowed(source, now):
        raise Paused(source)
    try:
        r = await client.get(url, headers=headers or {})
    except httpx.HTTPError as exc:
        calls.record(source, None, now, error=type(exc).__name__)
        raise
    calls.record(source, r.status_code, now, retry=r.headers.get("retry-after"))
    return r


async def fetch_forecast(lat: float, lon: float, client: httpx.AsyncClient | None = None,
                         now: datetime | None = None) -> dict:
    """Météo-France first, Open-Meteo as fallback."""
    base = _env("SARCADE_OPEN_METEO_URL", "https://api.open-meteo.com")
    key = _env("SARCADE_OPEN_METEO_API_KEY") or None
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    errors = []
    try:
        for provider in fc.ORDER:
            try:
                r = await _get("forecast", client, fc.request_url(provider, lat, lon, base, key), now=now)
                r.raise_for_status()
                return fc.parse(provider, r.json(), datetime.now(UTC))
            except Paused:
                errors.append(f"{provider}: paused")
                break
            except (httpx.HTTPError, ValueError, fc.InvalidForecast) as exc:
                errors.append(f"{provider}: {exc}")
    finally:
        if own:
            await client.aclose()
    raise WeatherUnavailable("; ".join(errors))


async def fetch_national(client: httpx.AsyncClient | None = None, now: datetime | None = None) -> dict | None:
    """The national vigilance map, or None (no key, paused, refused)."""
    key = _env("SARCADE_METEOFRANCE_API_KEY")
    if not key:
        return None
    url = _env("SARCADE_VIGILANCE_URL", vg.DEFAULT_URL)
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await _get("vigilance", client, url, {"apikey": key, "accept": "application/json"}, now)
        r.raise_for_status()
        return r.json()
    except Paused:
        return None
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("vigilance unavailable: %s", exc)
        return None
    finally:
        if own:
            await client.aclose()


async def fetch_vigilance(department: str, client: httpx.AsyncClient | None = None) -> dict | None:
    if not _env("SARCADE_METEOFRANCE_API_KEY") or not department:
        return None
    data = await fetch_national(client)
    return vg.department_vigilance(data, department) if data is not None else None


@dataclass
class National:
    """The national map, kept for all the events (choice 1)."""
    data: dict | None = None
    tried_at: datetime | None = None
    ok_at: datetime | None = None


national = National()


async def national_map(now: datetime, client: httpx.AsyncClient | None = None) -> dict | None:
    """Fetched at most every 5 minutes, whatever the number of events."""
    if national.tried_at is None or now - national.tried_at >= VIGILANCE_INTERVAL:
        if _env("SARCADE_METEOFRANCE_API_KEY") and calls.allowed("vigilance", now):
            national.tried_at = now
            data = await fetch_national(client, now)
            if data is not None:
                national.data, national.ok_at = data, now
    if national.ok_at is None or now - national.ok_at > NATIONAL_MAX_AGE:
        return None
    return national.data


def cache_get(db: Session, key: str) -> WeatherCacheRow | None:
    return db.get(WeatherCacheRow, key)


def cache_put(db: Session, key: str, data: dict, now: datetime) -> None:
    row = db.get(WeatherCacheRow, key)
    if row is None:
        db.add(WeatherCacheRow(key=key, data=data, fetched_at=now))
    else:
        row.data, row.fetched_at = data, now


def event_point(db: Session, event: EventRow) -> tuple[float, float] | None:
    """Forecast point set by the PCO, or the centre of recent positions."""
    s = event_settings.merged(event.settings)
    if s["weather_lat"] is not None and s["weather_lon"] is not None:
        return s["weather_lat"], s["weather_lon"]
    geom = func.ST_GeomFromWKB(func.ST_AsBinary(PositionRow.point))
    row = db.execute(select(func.avg(func.ST_Y(geom)), func.avg(func.ST_X(geom)))
                     .where(PositionRow.event_id == event.id)).first()
    if row and row[0] is not None:
        return float(row[0]), float(row[1])
    return None


# --- Active events (choice 2) --------------------------------------------------------

def is_active(status: str | None, ended_at: datetime | None, created_at: datetime | None,
              last_position_at: datetime | None, now: datetime) -> bool:
    """Open, and not a draft without any position for 24 hours (a draft
    created less than 24 hours ago counts as active)."""
    if ended_at is not None or status == "closed":
        return False
    if status != "draft":
        return True
    recent = [_aware(t) for t in (created_at, last_position_at) if t is not None]
    return bool(recent) and now - max(recent) < ACTIVE_WINDOW


def last_position_at(db: Session, event_id: str) -> datetime | None:
    return db.scalar(select(func.max(PositionRow.time)).where(PositionRow.event_id == event_id))


def operator_points(db: Session, event_id: str, since: datetime) -> list[tuple[float, float]]:
    """Last position of each device of the event since ``since`` (APRS
    stations excluded: they may be far from the operators)."""
    geom = func.ST_GeomFromWKB(func.ST_AsBinary(PositionRow.point))
    rows = db.execute(select(PositionRow.device_id, func.ST_Y(geom), func.ST_X(geom))
                      .where(PositionRow.event_id == event_id, PositionRow.time >= since,
                             PositionRow.source == "device")
                      .order_by(PositionRow.time.desc()).limit(2000)).all()
    seen, out = set(), []
    for device, lat, lon in rows:
        if device not in seen and lat is not None:
            seen.add(device)
            out.append((float(lat), float(lon)))
    return out


# --- Department (choices 9 to 11) ------------------------------------------------------

@lru_cache(maxsize=4096)
def _department_cell(lat: float, lon: float) -> str | None:
    from sarcade import places
    c = places.nearest_commune(lat, lon)
    return places.department_of(c.code) if c else None


def department_at(lat: float, lon: float) -> str | None:
    """Department of the nearest commune, offline, kept per point rounded
    to 0.01° (about 1 km): no network call, computed once per place."""
    return _department_cell(round(lat, 2), round(lon, 2))


def event_departments(settings: dict | None, point: tuple[float, float] | None,
                      operators: list[tuple[float, float]] = ()) -> tuple[list[str], str]:
    """(departments, source): ``event`` (set by the PCO, choice 10),
    ``auto`` (forecast point, then where the operators are, choices 9 and
    11), ``server`` (``SARCADE_DEPARTMENT``) or ``none``."""
    forced = event_settings.merged(settings)["department"]
    if forced:
        return [forced], "event"
    found: list[str] = []
    for p in [point, *operators]:
        d = department_at(*p) if p else None
        if d and d not in found:
            found.append(d)
        if len(found) >= MAX_DEPARTMENTS:
            break
    if found:
        return found, "auto"
    env = _env("SARCADE_DEPARTMENT").upper()
    return ([env], "server") if env else ([], "none")


def event_department(event: EventRow) -> str:
    """First department of the event without the positions (older callers)."""
    deps, _ = event_departments(event.settings, None)
    return deps[0] if deps else ""


# --- Targets ------------------------------------------------------------------------------

@dataclass
class Target:
    event_id: str
    point: tuple[float, float] | None
    departments: list[str] = field(default_factory=list)
    source: str = "none"


#: Departments followed by each active event, for /events/{id}/weather.
followed: dict[str, dict] = {}
last_tick: dict = {"at": None, "active_events": 0, "points": 0}


def target_of(db: Session, event: EventRow, now: datetime) -> Target:
    point = event_point(db, event)
    operators = operator_points(db, event.id, now - OPERATORS_WINDOW)
    deps, source = event_departments(event.settings, point, operators)
    return Target(event.id, point, deps, source)


def load_targets(now: datetime) -> list[Target]:
    db = SessionLocal()
    try:
        out = []
        for event in db.scalars(select(EventRow).where(EventRow.ended_at.is_(None), EventRow.status != "closed")):
            last = last_position_at(db, event.id) if event.status == "draft" else None
            if is_active(event.status, event.ended_at, event.created_at, last, now):
                out.append(target_of(db, event, now))
        return out
    finally:
        db.close()


def shared_point(point: tuple[float, float]) -> tuple[float, float]:
    """Point rounded to 0.1° (about 10 km), shared by the events (choice 3)."""
    return round(round(point[0] / GRID) * GRID, 1), round(round(point[1] / GRID) * GRID, 1)


def point_key(point: tuple[float, float]) -> str:
    lat, lon = shared_point(point)
    return f"forecast:{lat:.1f},{lon:.1f}"


_failed_at: dict[str, datetime] = {}


async def refresh_forecasts(targets: list[Target], now: datetime, client: httpx.AsyncClient | None = None,
                            force: bool = False) -> set[str]:
    """One forecast per shared point and per hour; copied to each event
    (``event:<id>``, read by the applications). Returns the events updated."""
    groups: dict[str, tuple[tuple[float, float], list[str]]] = {}
    for t in targets:
        if t.point is not None:
            groups.setdefault(point_key(t.point), (shared_point(t.point), []))[1].append(t.event_id)
    updated: set[str] = set()
    if not groups:
        return updated
    db = SessionLocal()
    try:
        for key, (pt, ids) in groups.items():
            row = cache_get(db, key)
            at = _aware(row.fetched_at) if row is not None else None
            data = row.data if row is not None else None
            due = force or at is None or now - at >= FORECAST_INTERVAL
            failed = _failed_at.get(key)
            if due and (force or failed is None or now - failed >= RETRY_AFTER_FAILURE):
                try:
                    data, at = await fetch_forecast(pt[0], pt[1], client, now), now
                    cache_put(db, key, data, now)
                    _failed_at.pop(key, None)
                except WeatherUnavailable as exc:
                    _failed_at[key] = now
                    log.info("forecast not refreshed for %s: %s", key, exc)
            if data is None:
                continue
            for event_id in ids:
                mine = cache_get(db, f"event:{event_id}")
                if mine is None or _aware(mine.fetched_at) < at:
                    cache_put(db, f"event:{event_id}", data, at)
                    updated.add(event_id)
        db.commit()
    finally:
        db.close()
    return updated


def _alert_summary(v: dict) -> str:
    name = v.get("department_name")
    where = f"{v['department']} ({name})" if name else v["department"]
    return (f"Vigilance {v['color']} Météo-France sur le département {where}"
            + (f" : {', '.join(v['phenomena'])}" if v["phenomena"] else ""))


_COMPARED = ("color_id", "phenomena", "department", "departments", "begin", "end")


async def refresh_vigilance(targets: list[Target], now: datetime,
                            client: httpx.AsyncClient | None = None) -> tuple[set[str], list[dict]]:
    """Reads each event's departments in the national map; alerts the PCO
    when the vigilance turns orange or red. Returns (events changed, alerts)."""
    need = [t for t in targets if t.departments]
    changed: set[str] = set()
    alerts: list[dict] = []
    if not need:
        return changed, alerts
    data = await national_map(now, client)
    if data is None:
        return changed, alerts
    db = SessionLocal()
    try:
        for t in need:
            v = vg.highest(data, t.departments)
            if v is None:
                continue
            prev = cache_get(db, f"vigilance:{t.event_id}")
            if prev is None or any((prev.data or {}).get(k) != v.get(k) for k in _COMPARED):
                changed.add(t.event_id)
            cache_put(db, f"vigilance:{t.event_id}", v, now)
            previous = cache_get(db, f"alert:{t.event_id}")
            previous_color = (previous.data or {}).get("color_id", 1) if previous else 1
            if vg.is_alert(v) and v["color_id"] > previous_color:
                summary = _alert_summary(v)
                db.add(LogbookRow(event_id=t.event_id, kind="weather", object_id=None, actor_id="METEO",
                                  summary=summary, time=now))
                alerts.append({"event_id": t.event_id, "summary": summary, "vigilance": v})
            cache_put(db, f"alert:{t.event_id}", {"color_id": v["color_id"]}, now)
        db.commit()
    finally:
        db.close()
    return changed, alerts


async def _announce(updated: set[str], alerts: list[dict], at: str | None) -> None:
    for event_id in sorted(updated):
        await manager.broadcast(event_id, {"type": "weather.updated", "data": {"fetched_at": at}})
    for alert in alerts:
        await manager.broadcast(alert["event_id"], {"type": "weather.alert", "data": alert})


async def tick(now: datetime | None = None, client: httpx.AsyncClient | None = None) -> list[Target]:
    """One pass: nothing at all without an active event (precision of the
    porteur, 10 Nov 2026)."""
    now = now or datetime.now(UTC)
    targets = load_targets(now)
    followed.clear()
    followed.update({t.event_id: {"departments": t.departments, "source": t.source} for t in targets})
    last_tick.update(at=_iso(now), active_events=len(targets),
                     points=len({point_key(t.point) for t in targets if t.point is not None}))
    if not targets:
        return targets
    updated = await refresh_forecasts(targets, now, client)
    changed, alerts = await refresh_vigilance(targets, now, client)
    await _announce(updated | changed, alerts, _iso(now))
    return targets


async def refresh_event(event_id: str, client: httpx.AsyncClient | None = None) -> dict:
    """« Actualiser » of one event: its shared forecast fetched now (unless
    paused), the vigilance read in the national map (fetched if older than
    5 minutes)."""
    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        event = db.get(EventRow, event_id)
        if event is None:
            raise LookupError("event_not_found")
        target = target_of(db, event, now)
    finally:
        db.close()
    if target.point is None:
        raise WeatherUnavailable("no_forecast_point")
    followed[event_id] = {"departments": target.departments, "source": target.source}
    updated = await refresh_forecasts([target], now, client, force=True)
    changed, alerts = await refresh_vigilance([target], now, client)
    if _failed_at.get(point_key(target.point)) == now:
        await _announce(changed, alerts, _iso(now))
        raise WeatherUnavailable("forecast_unavailable")
    db = SessionLocal()
    try:
        row = cache_get(db, f"event:{event_id}")
        forecast = row.data if row is not None else None
    finally:
        db.close()
    if forecast is None:
        raise WeatherUnavailable("no_forecast")
    await _announce(updated | changed | {event_id}, alerts, forecast.get("fetched_at"))
    return forecast


def status(now: datetime | None = None) -> dict:
    """Diagnostic › Serveur of the administrator: sources, pauses, counters
    of the day, active events and the national map."""
    now = now or datetime.now(UTC)
    return {**calls.snapshot(now), "key": bool(_env("SARCADE_METEOFRANCE_API_KEY")),
            "active_events": last_tick["active_events"], "points": last_tick["points"],
            "checked_at": last_tick["at"], "vigilance_map_at": _iso(national.ok_at)}


async def refresh_loop() -> None:
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001 - never crash the server
            log.warning("weather refresh failed: %s", exc)
        await asyncio.sleep(TICK_SECONDS)


def start(tasks: list) -> None:
    if _env("SARCADE_WEATHER", "1").lower() in ("1", "true", "yes"):
        tasks.append(asyncio.create_task(refresh_loop()))

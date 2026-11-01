"""Aircraft around an event (ADS-B, step 1: OpenSky Network over the
Internet; card « ADS-B : aéronefs sur la carte », 1 Nov 2026).

The server asks OpenSky only while someone shows the « Aéronefs » layer:
each application that shows it calls ``POST …/adsb/watch`` with the box of
its map every minute (:data:`WATCH_TTL_S`). Without a watcher, an event
costs no credit. One request per event and per interval (30 s at least:
about 2 900 credits a day for one event watched without a break, under the
4 000 of a Standard account).

Squawks 7700, 7600 and 7500 are written once in the logbook (alert for the
PCO) and flagged ``emergency`` in the broadcast.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
import os
import time

from sarcade import modules
from sarcade.db.models import EventRow, LogbookRow
from sarcade.db.session import SessionLocal
from sarcade.realtime.manager import manager

from . import opensky

log = logging.getLogger("sarcade.adsb")

MIN_INTERVAL_S = 30
WATCH_TTL_S = 120
MARGIN_DEG = 0.2
TRAIL_POINTS = 10
EMERGENCY_LABELS = {"emergency": "urgence (7700)", "radio_failure": "panne radio (7600)",
                    "unlawful_interference": "intervention illicite (7500)"}


def interval_s() -> int:
    try:
        return max(MIN_INTERVAL_S, int(os.getenv("SARCADE_ADSB_INTERVAL_S", str(MIN_INTERVAL_S))))
    except ValueError:
        return MIN_INTERVAL_S


@dataclass
class AdsbRuntime:
    enabled: bool = False
    connected: bool = False
    detail: str = ""
    last_ok_at: datetime | None = None
    requests_today: int = 0
    day: str = ""
    paused_until: float = 0.0
    # event_id -> device_id -> (bbox, expires monotonic)
    watchers: dict[str, dict[str, tuple[tuple[float, float, float, float], float]]] = field(default_factory=dict)
    # event_id -> {"time", "aircraft"}
    latest: dict[str, dict] = field(default_factory=dict)
    trails: dict[str, dict[str, deque]] = field(default_factory=dict)
    alerted: set[tuple[str, str, str]] = field(default_factory=set)


runtime = AdsbRuntime()


def watch(event_id: str, device_id: str, bbox: tuple[float, float, float, float], now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    runtime.watchers.setdefault(event_id, {})[device_id] = (bbox, now + WATCH_TTL_S)


def unwatch(event_id: str, device_id: str) -> None:
    runtime.watchers.get(event_id, {}).pop(device_id, None)


def watched_boxes(now: float | None = None) -> dict[str, tuple[float, float, float, float]]:
    """Live watchers only; one box per event (union of the maps, plus a
    margin, clamped to a 1-credit box)."""
    now = time.monotonic() if now is None else now
    out = {}
    for event_id, devices in list(runtime.watchers.items()):
        live = {d: v for d, v in devices.items() if v[1] > now}
        if not live:
            runtime.watchers.pop(event_id, None)
            continue
        runtime.watchers[event_id] = live
        boxes = [v[0] for v in live.values()]
        out[event_id] = opensky.clamp_bbox(min(b[0] for b in boxes) - MARGIN_DEG, min(b[1] for b in boxes) - MARGIN_DEG,
                                           max(b[2] for b in boxes) + MARGIN_DEG, max(b[3] for b in boxes) + MARGIN_DEG)
    return out


def with_trails(event_id: str, aircraft: list[dict]) -> list[dict]:
    trails = runtime.trails.setdefault(event_id, {})
    seen = set()
    for a in aircraft:
        key = a["icao24"]
        seen.add(key)
        trail = trails.setdefault(key, deque(maxlen=TRAIL_POINTS))
        if not trail or trail[-1] != (a["lat"], a["lon"]):
            trail.append((a["lat"], a["lon"]))
        a["trail"] = [list(p) for p in trail]
    for key in set(trails) - seen:
        trails.pop(key, None)
    return aircraft


def new_emergencies(event_id: str, aircraft: list[dict]) -> list[dict]:
    out = []
    for a in aircraft:
        if a.get("emergency"):
            key = (event_id, a["icao24"], a["squawk"])
            if key not in runtime.alerted:
                runtime.alerted.add(key)
                out.append(a)
    return out


def emergency_summary(a: dict) -> str:
    who = a.get("callsign") or a["icao24"].upper()
    alt = f", {a['alt_ft']} ft" if a.get("alt_ft") is not None else ""
    return f"ADS-B : aéronef {who} en {EMERGENCY_LABELS.get(a['emergency'], a['emergency'])}{alt} ({a['lat']:.4f}, {a['lon']:.4f})"


def _count_request() -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if runtime.day != today:
        runtime.day, runtime.requests_today = today, 0
    runtime.requests_today += 1


def _adrasec_events(event_ids) -> set[str]:
    with SessionLocal() as db:
        return {e for e in event_ids if (row := db.get(EventRow, e)) is not None
                and modules.enabled(row.settings, "adrasec")}


def _log_emergencies(event_id: str, found: list[dict]) -> None:
    with SessionLocal() as db:
        for a in found:
            db.add(LogbookRow(event_id=event_id, kind="adsb", object_id=a["icao24"], actor_id="ADS-B",
                              summary=emergency_summary(a), time=datetime.now(UTC)))
        db.commit()


async def poll_once(client: opensky.OpenSkyClient) -> None:
    boxes = watched_boxes()
    if not boxes:
        return
    for event_id in await asyncio.to_thread(_adrasec_events, list(boxes)):
        _count_request()
        aircraft = with_trails(event_id, await client.states(boxes[event_id]))
        data = {"time": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "aircraft": aircraft}
        runtime.latest[event_id] = data
        runtime.connected, runtime.detail, runtime.last_ok_at = True, "", datetime.now(UTC)
        await manager.broadcast(event_id, {"type": "adsb.updated", "data": data})
        found = new_emergencies(event_id, aircraft)
        if found:
            await asyncio.to_thread(_log_emergencies, event_id, found)
            for a in found:
                await manager.broadcast(event_id, {"type": "adsb.emergency",
                                                   "data": {**a, "summary": emergency_summary(a)}})


async def loop(client: opensky.OpenSkyClient) -> None:
    while True:
        if time.monotonic() >= runtime.paused_until:
            try:
                await poll_once(client)
            except opensky.QuotaExceeded as exc:
                runtime.connected, runtime.detail = False, "quota OpenSky atteint"
                runtime.paused_until = time.monotonic() + exc.retry_after_s
                log.warning("OpenSky quota reached, pause %s s", exc.retry_after_s)
            except Exception as exc:  # noqa: BLE001 - kept running, shown in the status
                runtime.connected, runtime.detail = False, type(exc).__name__
                log.warning("OpenSky request failed: %s", type(exc).__name__)
        await asyncio.sleep(interval_s())


def start(loop_tasks: list) -> None:
    creds = opensky.credentials()
    runtime.enabled = creds is not None
    if creds is None:
        runtime.detail = "identifiants OpenSky absents"
        return
    loop_tasks.append(asyncio.create_task(loop(opensky.OpenSkyClient(*creds))))

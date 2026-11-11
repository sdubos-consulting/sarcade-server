"""ADS-B routes of the ADRASEC module (step 1, OpenSky; card « ADS-B :
aéronefs sur la carte », 1 Nov 2026)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sarcade import modules
from sarcade.api.deps import get_db
from sarcade.db.models import EventRow

from .adsb import service

router = APIRouter(prefix="/api/v0.1")


class WatchIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    lamin: float = Field(ge=-90, le=90)
    lomin: float = Field(ge=-180, le=180)
    lamax: float = Field(ge=-90, le=90)
    lomax: float = Field(ge=-180, le=180)
    on: bool = True


def _adrasec_event(db: Session, event_id: str) -> EventRow:
    event = db.get(EventRow, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    if not modules.enabled(event.settings, "adrasec"):
        raise HTTPException(status_code=409, detail="module_inactive:adrasec")
    return event


@router.post("/events/{event_id}/adsb/watch")
def watch(event_id: str, payload: WatchIn, db: Session = Depends(get_db)):
    """The « Aéronefs » layer is shown (call again every minute) or hidden
    (``on: false``). The server asks OpenSky only while it is shown."""
    _adrasec_event(db, event_id)
    if payload.on:
        service.watch(event_id, payload.device_id, (payload.lamin, payload.lomin, payload.lamax, payload.lomax))
    else:
        service.unwatch(event_id, payload.device_id)
    return {"enabled": service.runtime.enabled, "interval_s": service.interval_s(),
            "ttl_s": service.WATCH_TTL_S, **_latest(event_id)}


def _latest(event_id: str) -> dict:
    """Last aircraft and the server clock (``now``, epoch seconds): the
    applications estimate the positions from the OpenSky time of each one."""
    return {**(service.runtime.latest.get(event_id) or {"time": None, "aircraft": []}), "now": round(time.time(), 1)}


@router.get("/events/{event_id}/adsb")
def latest(event_id: str, db: Session = Depends(get_db)):
    _adrasec_event(db, event_id)
    return _latest(event_id)


@router.get("/events/{event_id}/adsb/{icao24}/track")
def aircraft_track(event_id: str, icao24: str, db: Session = Depends(get_db)):
    """Where the selected aircraft comes from: up to 30 min of positions."""
    _adrasec_event(db, event_id)
    return {"icao24": icao24.lower(), "track": service.track(event_id, icao24)}


@router.get("/adsb/status")
def status():
    r = service.runtime
    return {"enabled": r.enabled, "connected": r.connected, "detail": r.detail,
            "last_ok_at": r.last_ok_at, "requests_today": r.requests_today, "credits_per_day": 4000,
            "paused": time.monotonic() < r.paused_until,
            "interval_s": service.interval_s(), "watched_events": len(service.watched_boxes())}

"""Weather API: forecast of an event, of a point, bulletin for the PCO."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sarcade.db.models import EventRow
from sarcade.events import settings as event_settings
from sarcade.weather import forecast as fc
from sarcade.weather import service as weather

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
POINT_CACHE = timedelta(hours=1)


def _answer(row, now: datetime, compact: bool) -> dict:
    if row is None:
        return {"forecast": None, "stale": True, "age_minutes": None}
    fetched = row.fetched_at.astimezone(UTC)
    data = fc.compact(row.data, now) if compact else row.data
    return {"forecast": data, "stale": fc.is_stale(fetched, now),
            "age_minutes": int((now - fetched).total_seconds() // 60)}


@router.get("/events/{event_id}/weather")
def event_weather(event_id: str, compact: bool = Query(False), db: Session = Depends(get_db)):
    """Last forecast of the event, even without Internet; ``stale`` beyond
    6 hours (shown greyed). ``compact`` for the low-bandwidth mode."""
    event = db.get(EventRow, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    now = datetime.now(UTC)
    result = _answer(weather.cache_get(db, f"event:{event_id}"), now, compact)
    vig = weather.cache_get(db, f"vigilance:{event_id}")
    result["vigilance"] = vig.data if vig else None
    # Card « Météo : limiter les appels à Météo-France », choices 9 to 11
    # (10 Nov 2026): departments followed and where they come from (event,
    # auto, server, none); before the first pass, from the settings only.
    s = event_settings.merged(event.settings)
    followed = weather.followed.get(event_id)
    if followed is None or s["department"] or followed.get("source") == "event":
        point = (s["weather_lat"], s["weather_lon"]) if s["weather_lat"] is not None and s["weather_lon"] is not None else None
        deps, source = weather.event_departments(event.settings, point)
        followed = {"departments": deps, "source": source}
    result["vigilance_departments"] = followed
    return result


@router.post("/events/{event_id}/weather/refresh")
async def refresh(event_id: str):
    try:
        forecast = await weather.refresh_event(event_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="event_not_found")
    except weather.WeatherUnavailable as exc:
        detail = "no_forecast_point" if str(exc) == "no_forecast_point" else "weather_unavailable"
        raise HTTPException(status_code=409 if detail == "no_forecast_point" else 503, detail=detail)
    return {"fetched_at": forecast["fetched_at"], "provider": forecast["provider"]}


@router.get("/events/{event_id}/weather/bulletin")
def event_bulletin(event_id: str, db: Session = Depends(get_db)):
    """Text the PCO can publish in « Diffusion PCO »."""
    row = weather.cache_get(db, f"event:{event_id}")
    if row is None:
        raise HTTPException(status_code=404, detail="no_forecast")
    vig = weather.cache_get(db, f"vigilance:{event_id}")
    try:
        text = fc.bulletin(row.data, datetime.now(UTC), vig.data if vig else None)
    except fc.InvalidForecast:
        raise HTTPException(status_code=409, detail="forecast_outdated")
    return {"text": text, "fetched_at": row.data.get("fetched_at")}


@router.get("/weather")
async def point_weather(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                        compact: bool = Query(False), db: Session = Depends(get_db)):
    """Forecast of a designated point (« Météo » button of a point sheet),
    cached one hour on a ~5 km grid; the last one is returned offline."""
    key = f"point:{round(lat * 20) / 20:.2f},{round(lon * 20) / 20:.2f}"
    now = datetime.now(UTC)
    row = weather.cache_get(db, key)
    if row is None or now - row.fetched_at.astimezone(UTC) > POINT_CACHE:
        try:
            data = await weather.fetch_forecast(lat, lon)
            weather.cache_put(db, key, data, now)
            db.commit()
            row = weather.cache_get(db, key)
        except weather.WeatherUnavailable:
            if row is None:
                raise HTTPException(status_code=503, detail="weather_unavailable")
    return _answer(row, now, compact)

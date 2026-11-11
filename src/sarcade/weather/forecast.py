"""Weather forecasts (note « Prévisions météo »): pure functions.

Sources, decided on 10 Oct 2026: Météo-France first (AROME and ARPEGE
models, here through Open-Meteo's Météo-France endpoint), Open-Meteo's
generic forecast as fallback. Both answer in the same format.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

STALE_AFTER = timedelta(hours=6)

PROVIDERS = {
    "meteofrance": {
        "label": "Météo-France (AROME, ARPEGE)",
        "path": "/v1/meteofrance",
        "models": "meteofrance_seamless",
        "days": 4,
        "hourly": ["temperature_2m", "apparent_temperature", "precipitation", "wind_speed_10m",
                   "wind_gusts_10m", "wind_direction_10m", "weather_code", "cloud_cover"],
    },
    "open-meteo": {
        "label": "Open-Meteo",
        "path": "/v1/forecast",
        "models": None,
        "days": 5,
        "hourly": ["temperature_2m", "apparent_temperature", "precipitation", "precipitation_probability",
                   "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "weather_code",
                   "cloud_cover", "visibility"],
    },
}
ORDER = ("meteofrance", "open-meteo")

_FIELDS = {
    "temperature_2m": "temperature_c", "apparent_temperature": "apparent_c",
    "precipitation": "precipitation_mm", "precipitation_probability": "precipitation_probability",
    "wind_speed_10m": "wind_kmh", "wind_gusts_10m": "gust_kmh", "wind_direction_10m": "wind_direction_deg",
    "weather_code": "weather_code", "cloud_cover": "cloud_cover_pct", "visibility": "visibility_m",
}

# WMO weather interpretation codes, in French.
WMO = {
    0: "Ciel clair", 1: "Peu nuageux", 2: "Partiellement nuageux", 3: "Couvert",
    45: "Brouillard", 48: "Brouillard givrant",
    51: "Bruine légère", 53: "Bruine", 55: "Bruine forte", 56: "Bruine verglaçante", 57: "Bruine verglaçante forte",
    61: "Pluie faible", 63: "Pluie", 65: "Pluie forte", 66: "Pluie verglaçante", 67: "Pluie verglaçante forte",
    71: "Neige faible", 73: "Neige", 75: "Neige forte", 77: "Grains de neige",
    80: "Averses faibles", 81: "Averses", 82: "Averses violentes", 85: "Averses de neige", 86: "Fortes averses de neige",
    95: "Orage", 96: "Orage avec grêle", 99: "Orage avec forte grêle",
}


def request_url(provider: str, lat: float, lon: float, base_url: str = "https://api.open-meteo.com",
                api_key: str | None = None) -> str:
    p = PROVIDERS[provider]
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(p["hourly"]),
        "daily": "sunrise,sunset", "timezone": "UTC", "forecast_days": p["days"], "wind_speed_unit": "kmh",
    }
    if p["models"]:
        params["models"] = p["models"]
    if api_key:
        params["apikey"] = api_key
    return f"{base_url.rstrip('/')}{p['path']}?{urlencode(params)}"


class InvalidForecast(ValueError):
    pass


def _utc(text: str) -> str:
    """Open-Meteo times are local to the requested zone (UTC here)."""
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse(provider: str, data: dict, fetched_at: datetime) -> dict:
    """Open-Meteo JSON → SARCADE forecast (hourly list, sun times)."""
    hourly = (data or {}).get("hourly") or {}
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        raise InvalidForecast("no_hourly_data")
    hours = []
    for i, t in enumerate(times):
        entry = {"time": _utc(t)}
        for src, dst in _FIELDS.items():
            values = hourly.get(src)
            entry[dst] = values[i] if isinstance(values, list) and i < len(values) else None
        code = entry.get("weather_code")
        entry["summary"] = WMO.get(int(code), "") if isinstance(code, (int, float)) else ""
        hours.append(entry)
    daily = (data or {}).get("daily") or {}
    days = [{"date": d, "sunrise": _utc(sr) if sr else None, "sunset": _utc(ss) if ss else None}
            for d, sr, ss in zip(daily.get("time") or [], daily.get("sunrise") or [], daily.get("sunset") or [])]
    return {
        "provider": provider, "provider_label": PROVIDERS[provider]["label"],
        "lat": data.get("latitude"), "lon": data.get("longitude"), "elevation_m": data.get("elevation"),
        "fetched_at": fetched_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "hourly": hours, "daily": days,
    }


def is_stale(fetched_at: datetime, now: datetime) -> bool:
    return now - fetched_at > STALE_AFTER


def upcoming(forecast: dict, now: datetime, hours: int = 48) -> list[dict]:
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=hours)
    result = []
    for h in forecast.get("hourly", []):
        t = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
        if start <= t < end:
            result.append(h)
    return result


def compact(forecast: dict, now: datetime) -> dict:
    """Short summary for the low-bandwidth mode: every 3 hours over 12 hours."""
    hours = upcoming(forecast, now, 12)[::3]
    keep = ("time", "temperature_c", "precipitation_mm", "wind_kmh", "gust_kmh", "weather_code", "summary")
    return {"provider": forecast.get("provider"), "fetched_at": forecast.get("fetched_at"),
            "hourly": [{k: h.get(k) for k in keep} for h in hours]}


def _fmt(value, unit: str, digits: int = 0) -> str:
    if value is None:
        return "?"
    return f"{value:.{digits}f}".replace(".", ",") + unit


def bulletin(forecast: dict, now: datetime, vigilance: dict | None = None) -> str:
    """French weather bulletin the PCO can publish in « Diffusion PCO »."""
    hours = upcoming(forecast, now, 12)
    if not hours:
        raise InvalidForecast("no_upcoming_hours")
    temps = [h["temperature_c"] for h in hours if h.get("temperature_c") is not None]
    gusts = [h["gust_kmh"] for h in hours if h.get("gust_kmh") is not None]
    rain = sum(h["precipitation_mm"] or 0 for h in hours)
    first = hours[0]
    lines = [f"Météo, prochaines 12 h ({forecast.get('provider_label', '')})"]
    lines.append(f"Actuellement : {first.get('summary') or 'conditions inconnues'}, "
                 f"{_fmt(first.get('temperature_c'), ' °C')}, vent {_fmt(first.get('wind_kmh'), ' km/h')}")
    if temps:
        lines.append(f"Températures : {_fmt(min(temps), ' °C')} à {_fmt(max(temps), ' °C')}")
    if gusts:
        lines.append(f"Rafales jusqu'à {_fmt(max(gusts), ' km/h')}")
    lines.append(f"Pluie cumulée : {_fmt(rain, ' mm', 1)}")
    events = sorted({h["summary"] for h in hours if h.get("weather_code") in (95, 96, 99, 65, 82, 75, 86, 66, 67)})
    if events:
        lines.append("À surveiller : " + ", ".join(events).lower())
    for day in forecast.get("daily", []):
        if day.get("sunset"):
            sunset = datetime.fromisoformat(day["sunset"].replace("Z", "+00:00"))
            if sunset > now:
                lines.append(f"Coucher du soleil : {sunset.strftime('%H:%M')} UTC")
                break
    if vigilance and vigilance.get("color_id", 1) >= 2:
        lines.append(f"Vigilance {vigilance['color']} : {', '.join(vigilance['phenomena']) or 'voir Météo-France'}")
    return "\n".join(lines)

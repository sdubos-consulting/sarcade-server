import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from sarcade.events import settings as event_settings
from sarcade.weather import forecast as fc
from sarcade.weather import service, vigilance as vg

NOW = datetime(2026, 10, 10, 6, 20, tzinfo=UTC)


def open_meteo(hours=48, start=datetime(2026, 10, 10, 0, 0), code=3, gust=40.0):
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    return {
        "latitude": 48.8, "longitude": 2.1, "elevation": 120.0,
        "hourly": {"time": times, "temperature_2m": [10 + i % 5 for i in range(hours)],
                   "apparent_temperature": [8.0] * hours, "precipitation": [0.5] * hours,
                   "wind_speed_10m": [15.0] * hours, "wind_gusts_10m": [gust] * hours,
                   "wind_direction_10m": [250] * hours, "weather_code": [code] * hours,
                   "cloud_cover": [80] * hours},
        "daily": {"time": ["2026-10-10", "2026-10-11"], "sunrise": ["2026-10-10T06:05", "2026-10-11T06:07"],
                  "sunset": ["2026-10-10T17:10", "2026-10-11T17:08"]},
    }


def test_request_urls_follow_the_open_meteo_api():
    url = fc.request_url("meteofrance", 48.80123, 2.13456)
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).path == "/v1/meteofrance" and q["models"] == ["meteofrance_seamless"]
    assert q["latitude"] == ["48.8012"] and q["timezone"] == ["UTC"] and q["daily"] == ["sunrise,sunset"]
    url = fc.request_url("open-meteo", 48.8, 2.1, "https://customer-api.open-meteo.com", api_key="k")
    assert url.startswith("https://customer-api.open-meteo.com/v1/forecast?") and "apikey=k" in url
    assert "precipitation_probability" in url and "models" not in url


def test_parse_normalises_hours_and_sun_times():
    f = fc.parse("meteofrance", open_meteo(), NOW)
    assert f["provider"] == "meteofrance" and len(f["hourly"]) == 48
    h = f["hourly"][6]
    assert h["time"] == "2026-10-10T06:00:00Z" and h["gust_kmh"] == 40.0 and h["summary"] == "Couvert"
    assert h["precipitation_probability"] is None, "not given by the Météo-France endpoint"
    assert f["daily"][0]["sunset"] == "2026-10-10T17:10:00Z"


def test_parse_rejects_empty_answers():
    with pytest.raises(fc.InvalidForecast):
        fc.parse("open-meteo", {"hourly": {}}, NOW)


def test_staleness_after_six_hours():
    assert not fc.is_stale(NOW - timedelta(hours=5), NOW)
    assert fc.is_stale(NOW - timedelta(hours=7), NOW)


def test_compact_summary_for_low_bandwidth():
    c = fc.compact(fc.parse("meteofrance", open_meteo(), NOW), NOW)
    assert [h["time"] for h in c["hourly"]] == ["2026-10-10T06:00:00Z", "2026-10-10T09:00:00Z",
                                                "2026-10-10T12:00:00Z", "2026-10-10T15:00:00Z"]
    assert set(c["hourly"][0]) == {"time", "temperature_c", "precipitation_mm", "wind_kmh", "gust_kmh",
                                   "weather_code", "summary"}


def test_bulletin_in_french():
    f = fc.parse("meteofrance", open_meteo(code=95, gust=72.5), NOW)
    text = fc.bulletin(f, NOW, {"color_id": 3, "color": "orange", "phenomena": ["orages"]})
    assert text.startswith("Météo, prochaines 12 h (Météo-France")
    assert "Rafales jusqu'à 72 km/h" in text or "Rafales jusqu'à 73 km/h" in text
    assert "Pluie cumulée : 6,0 mm" in text
    assert "À surveiller : orage" in text
    assert "Coucher du soleil : 17:10 UTC" in text
    assert "Vigilance orange : orages" in text


VIGILANCE = {"product": {"periods": [
    {"echeance": "J", "begin_validity_time": "2026-10-10T00:00:00Z", "end_validity_time": "2026-10-11T00:00:00Z",
     "timelaps": {"domain_ids": [
         {"domain_id": "78", "max_color_id": 3, "phenomenon_items": [
             {"phenomenon_id": "3", "phenomenon_max_color_id": 3},
             {"phenomenon_id": "1", "phenomenon_max_color_id": 2},
             {"phenomenon_id": "6", "phenomenon_max_color_id": 1}]},
         {"domain_id": "92", "max_color_id": 1, "phenomenon_items": []}]}},
    {"echeance": "J1", "timelaps": {"domain_ids": [{"domain_id": "78", "max_color_id": 4}]}}]}}


def test_department_vigilance():
    v = vg.department_vigilance(VIGILANCE, "78")
    assert v["color"] == "orange" and v["phenomena"] == ["orages", "vent violent"]
    assert vg.is_alert(v)
    assert not vg.is_alert(vg.department_vigilance(VIGILANCE, "92"))
    assert vg.department_vigilance(VIGILANCE, "01") is None
    assert vg.department_vigilance({}, "78") is None


def test_fallback_to_open_meteo_when_meteo_france_fails(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/meteofrance":
            return httpx.Response(503)
        return httpx.Response(200, json=open_meteo())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await service.fetch_forecast(48.8, 2.1, client)

    f = asyncio.run(run())
    assert calls == ["/v1/meteofrance", "/v1/forecast"] and f["provider"] == "open-meteo"


def test_unavailable_when_every_source_fails():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            await service.fetch_forecast(48.8, 2.1, client)

    with pytest.raises(service.WeatherUnavailable):
        asyncio.run(run())


def test_vigilance_needs_a_key(monkeypatch):
    monkeypatch.delenv("SARCADE_METEOFRANCE_API_KEY", raising=False)
    assert asyncio.run(service.fetch_vigilance("78")) is None
    monkeypatch.setenv("SARCADE_METEOFRANCE_API_KEY", "k")
    seen = {}

    def handler(request):
        seen["apikey"] = request.headers.get("apikey")
        return httpx.Response(200, json=VIGILANCE)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await service.fetch_vigilance("78", client)

    assert asyncio.run(run())["color_id"] == 3 and seen["apikey"] == "k"


def test_weather_settings():
    s = event_settings.apply_patch(None, {"weather_lat": 48.8, "weather_lon": 2.1, "department": " 78 "})
    assert s["department"] == "78" and s["weather_lat"] == 48.8
    with pytest.raises(event_settings.InvalidSettings):
        event_settings.apply_patch(None, {"weather_lat": 95})
    with pytest.raises(event_settings.InvalidSettings):
        event_settings.apply_patch(None, {"department": "Yvelines"})

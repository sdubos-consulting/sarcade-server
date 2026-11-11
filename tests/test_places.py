import httpx
import pytest
from fastapi.testclient import TestClient

from sarcade import places
from sarcade.api.app import app


def test_sectors_8_and_16():
    assert places.sector(0) == ("nord", "north")
    assert places.sector(359) == ("nord", "north")
    assert places.sector(44) == ("nord-est", "northeast")
    assert places.sector(90) == ("est", "east")
    assert places.sector(200) == ("sud", "south")
    assert places.sector(225) == ("sud-ouest", "southwest")
    assert places.sector(292) == ("ouest", "west")
    assert places.sector(293) == ("nord-ouest", "northwest")
    assert places.sector(22, 16) == ("nord-nord-est", "north-northeast")
    assert places.sector(250, 16) == ("ouest-sud-ouest", "west-southwest")
    assert places.sector(349, 16) == ("nord", "north")
    with pytest.raises(ValueError):
        places.sector(10, 4)


def test_distance_and_bearing():
    # 0.01° of latitude ≈ 1112 m, due north
    assert abs(places.distance_m(48.0, 2.0, 48.01, 2.0) - 1112) < 2
    assert abs(places.bearing_deg(48.0, 2.0, 48.01, 2.0)) < 0.01
    assert abs(places.bearing_deg(48.0, 2.0, 48.0, 2.01) - 90) < 0.1
    assert abs(places.bearing_deg(48.0, 2.0, 47.99, 1.99) - 213.8) < 1


def test_format_distance():
    assert places.format_distance(42) == "40 m"
    assert places.format_distance(812) == "800 m"
    assert places.format_distance(990) == "1,0 km"
    assert places.format_distance(1234) == "1,2 km"
    assert places.format_distance(1234, ".") == "1.2 km"
    assert places.format_distance(14321) == "14 km"


def test_french_articles():
    assert places._de("Chavenay") == "de Chavenay"
    assert places._de("Élancourt") == "d'Élancourt"
    assert places._de("Le Mesnil-Saint-Denis") == "du Mesnil-Saint-Denis"
    assert places._de("Les Clayes-sous-Bois") == "des Clayes-sous-Bois"
    assert places._au("ouest") == "à l'ouest" and places._au("sud-est") == "au sud-est"


def test_embedded_communes():
    grid = places._index()
    assert sum(len(v) for v in grid.values()) > 34000, "whole of France embedded"
    c = places.nearest_commune(48.81316, 1.94634)
    assert c and c.name == "Plaisir" and c.code == "78490"
    assert places.nearest_commune(45.0, -30.0) is None, "middle of the Atlantic"


def test_describe_offline_near_plaisir():
    plaisir = places.nearest_commune(48.81316, 1.94634)
    d = places.describe_offline(plaisir.lat + 0.0072, plaisir.lon)  # ~800 m north
    assert d["commune"]["name"] == "Plaisir"
    assert d["direction"] == "nord" and d["label"] == "800 m au nord de Plaisir"
    assert d["label_en"] == "800 m north of Plaisir"
    centre = places.describe_offline(plaisir.lat, plaisir.lon)
    assert centre["label"] == "au centre de Plaisir"
    assert places.describe_offline(45.0, -30.0) is None


def test_nearest_is_true_nearest():
    """The grid search gives the same answer as a full scan."""
    every = [c for v in places._index().values() for c in v]
    for lat, lon in [(48.82308, 1.90972), (43.2965, 5.3698), (42.70, 9.45), (48.0, -4.7), (50.95, 1.85)]:
        brute = min(every, key=lambda c: places.distance_m(lat, lon, c.lat, c.lon))
        assert places.nearest_commune(lat, lon) == brute


IGN = {"type": "FeatureCollection", "features": [
    {"geometry": {"type": "Point", "coordinates": [1.9097, 48.8232]},
     "properties": {"label": "Rue des Côteaux de Chatron 78640 Saint-Germain-de-la-Grange", "name": "Rue des Côteaux de Chatron",
                    "street": "Rue des Côteaux de Chatron", "city": "Saint-Germain-de-la-Grange",
                    "postcode": "78640", "type": "street"}}]}


@pytest.mark.asyncio
async def test_nearest_road_cached():
    places._road_cache.clear()
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=IGN)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = await places.nearest_road(48.82308, 1.90972, client)
    b = await places.nearest_road(48.82309, 1.90971, client)
    assert a == b and a["name"] == "Rue des Côteaux de Chatron" and a["distance_m"] < 20
    assert len(calls) == 1 and calls[0]["index"] == "address"


@pytest.mark.asyncio
async def test_nearest_road_silent_fallback(monkeypatch):
    places._road_cache.clear()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert await places.nearest_road(48.8, 1.9, client) is None

    def boom(r):
        raise httpx.ConnectTimeout("timeout")
    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    assert await places.nearest_road(48.8, 1.9, client) is None
    monkeypatch.setenv("SARCADE_REVERSE_GEOCODER_URL", "")
    assert await places.nearest_road(48.8, 1.9) is None


def test_to_altitude_and_department():
    assert places.to_altitude({"elevations": [112.43]}) == 112.4
    assert places.to_altitude([{"lon": 1.9, "lat": 48.8, "z": 98.0}]) == 98.0
    assert places.to_altitude({"elevations": [-99999]}) is None
    assert places.to_altitude({"elevations": []}) is None and places.to_altitude({}) is None
    assert places.department_of("78490") == "78" and places.department_of("2A004") == "2A"
    assert places.department_of("97411") == "974" and places.department_of(None) is None


@pytest.mark.asyncio
async def test_altitude_cached_and_silent(monkeypatch):
    places._alt_cache.clear()
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"elevations": [112.4]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await places.altitude(48.82308, 1.90972, client) == 112.4
    assert await places.altitude(48.82309, 1.90971, client) == 112.4
    assert len(calls) == 1 and calls[0]["zonly"] == "true"
    places._alt_cache.clear()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await places.altitude(48.8, 1.9, client) is None
    monkeypatch.setenv("SARCADE_ALTIMETRY_URL", "")
    assert await places.altitude(48.8, 1.9) is None


def test_api_describe(monkeypatch):
    monkeypatch.setenv("SARCADE_REVERSE_GEOCODER_URL", "")
    monkeypatch.setenv("SARCADE_ALTIMETRY_URL", "")
    with TestClient(app) as c:
        r = c.get("/api/v0.1/places/describe", params={"lat": 48.82308, "lon": 1.90972, "sectors": 16})
        assert r.status_code == 200
        body = r.json()
        assert body["commune"]["name"] and body["label"].endswith(body["commune"]["name"]) or "centre" in body["label"]
        assert body["road"] is None and body["direction"] in places.SECTORS_16
        assert body["altitude_m"] is None and body["department"] == "78"
        assert c.get("/api/v0.1/places/describe", params={"lat": 95, "lon": 0}).status_code == 422
        far = c.get("/api/v0.1/places/describe", params={"lat": 45.0, "lon": -30.0}).json()
        assert far["label"] is None and far["commune"] is None

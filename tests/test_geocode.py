import httpx
import pytest
from fastapi.testclient import TestClient

from sarcade import geocode
from sarcade.api.app import app

BAN = {"type": "FeatureCollection", "features": [
    {"geometry": {"type": "Point", "coordinates": [1.909, 48.823]},
     "properties": {"label": "Rue des Côteaux de Chatron 78640 Saint-Germain-de-la-Grange", "type": "street",
                    "city": "Saint-Germain-de-la-Grange", "postcode": "78640", "score": 0.9}},
    {"geometry": None, "properties": {}},
]}


def test_results_compact():
    r = geocode.to_results(BAN)
    assert r == [{"label": "Rue des Côteaux de Chatron 78640 Saint-Germain-de-la-Grange", "kind": "street",
                  "city": "Saint-Germain-de-la-Grange", "postcode": "78640", "score": 0.9, "lat": 48.823, "lon": 1.909}]


@pytest.mark.asyncio
async def test_search_cached_and_near_centre(monkeypatch):
    geocode._cache.clear()
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=BAN)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = await geocode.search("chatron", 48.82, 1.91, client=client)
    b = await geocode.search(" Chatron ", 48.83, 1.92, client=client)
    assert a == b and len(a) == 1
    assert len(calls) == 1, "second search served from the cache"
    assert calls[0]["lat"] == "48.82" and calls[0]["q"] == "chatron"


@pytest.mark.asyncio
async def test_unavailable(monkeypatch):
    geocode._cache.clear()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(502)))
    with pytest.raises(geocode.GeocoderUnavailable):
        await geocode.search("versailles", client=client)
    monkeypatch.setenv("SARCADE_GEOCODER_URL", "")
    with pytest.raises(geocode.GeocoderUnavailable):
        await geocode.search("versailles")


def test_api_503_when_disabled(monkeypatch):
    monkeypatch.setenv("SARCADE_GEOCODER_URL", "")
    r = TestClient(app).get("/api/v0.1/geocode", params={"q": "versailles"})
    assert r.status_code == 503
    assert TestClient(app).get("/api/v0.1/geocode", params={"q": "ab"}).status_code == 422

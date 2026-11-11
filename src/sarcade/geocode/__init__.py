"""Address search for the map (request of 26 Oct 2026): Base Adresse
Nationale (api-adresse.data.gouv.fr), free, French, no key. The clients ask
the SARCADE server, which keeps the answers for a day; the BAN only sees the
server, never the devices.

* ``SARCADE_GEOCODER_URL``: search endpoint (default the BAN), empty to
  switch the address search off (installation never online).
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict

import httpx

DEFAULT_URL = "https://api-adresse.data.gouv.fr/search/"
CACHE_SECONDS = 24 * 3600
CACHE_SIZE = 500
_cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()


class GeocoderUnavailable(Exception):
    pass


def geocoder_url() -> str:
    v = os.getenv("SARCADE_GEOCODER_URL")
    return DEFAULT_URL if v is None else v.strip()


def to_results(payload: dict) -> list[dict]:
    """GeoJSON of the BAN → compact results for the clients."""
    out = []
    for f in payload.get("features") or []:
        try:
            lon, lat = f["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        p = f.get("properties") or {}
        out.append({"label": p.get("label") or p.get("name") or "", "kind": p.get("type") or "",
                    "city": p.get("city") or "", "postcode": p.get("postcode") or "",
                    "score": p.get("score"), "lat": float(lat), "lon": float(lon)})
    return out


def _key(q: str, lat: float | None, lon: float | None) -> tuple:
    near = None if lat is None or lon is None else (round(lat, 1), round(lon, 1))
    return (q.strip().lower(), near)


async def search(q: str, lat: float | None = None, lon: float | None = None, limit: int = 8,
                 client: httpx.AsyncClient | None = None) -> list[dict]:
    url = geocoder_url()
    if not url:
        raise GeocoderUnavailable("disabled")
    key = _key(q, lat, lon)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_SECONDS:
        _cache.move_to_end(key)
        return hit[1][:limit]
    params: dict = {"q": q.strip(), "limit": 10}
    if lat is not None and lon is not None:
        params.update(lat=lat, lon=lon)  # results near the map centre first
    own = client is None
    client = client or httpx.AsyncClient(timeout=8)
    try:
        r = await client.get(url, params=params, headers={"User-Agent": "SARCADE"})
        r.raise_for_status()
        results = to_results(r.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise GeocoderUnavailable(str(exc) or type(exc).__name__)
    finally:
        if own:
            await client.aclose()
    _cache[key] = (now, results)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return results[:limit]

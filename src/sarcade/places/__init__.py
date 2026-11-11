"""Readable location (decision of 29 Oct 2026, option A): distance and
direction from the nearest commune, computed offline from the embedded list
of French communes, plus, when the server is online, the nearest road from
the reverse geocoding of the IGN Géoplateforme (short timeout, silent
fallback).

Data: ``communes.csv.gz`` (code;name;lat;lon; 35 191 communes of
metropolitan France, Corsica included, no overseas departments), built by
``tool/places/build_communes.py`` from france-geojson (IGN Admin Express +
INSEE COG 2018), Licence Ouverte Etalab.

* ``SARCADE_REVERSE_GEOCODER_URL``: reverse geocoding endpoint (default the
  IGN Géoplateforme), empty to switch the road lookup off.
* ``SARCADE_ALTIMETRY_URL``: altimetry endpoint (default the IGN
  Géoplateforme, RGE ALTI), empty to switch the altitude off (lot 5,
  fiche « Infos du point », 30 Oct 2026).
"""
from __future__ import annotations

import csv
import gzip
import io
import math
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

DATA = Path(__file__).with_name("communes.csv.gz")
DEFAULT_REVERSE_URL = "https://data.geopf.fr/geocodage/reverse"
DEFAULT_ALTIMETRY_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
NO_DATA = -99999
REVERSE_TIMEOUT = 2.5
CACHE_SECONDS = 24 * 3600
CACHE_SIZE = 2000
EARTH_M = 6_371_008.8
CELL = 0.1  # degrees, grid of the nearest commune search

SECTORS_16 = ["nord", "nord-nord-est", "nord-est", "est-nord-est", "est", "est-sud-est", "sud-est", "sud-sud-est",
              "sud", "sud-sud-ouest", "sud-ouest", "ouest-sud-ouest", "ouest", "ouest-nord-ouest", "nord-ouest",
              "nord-nord-ouest"]
SECTORS_16_EN = ["north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast",
                 "south-southeast", "south", "south-southwest", "southwest", "west-southwest", "west",
                 "west-northwest", "northwest", "north-northwest"]
CENTRE_M = 150  # closer than that: "au centre de …"


@dataclass(frozen=True)
class Commune:
    code: str
    name: str
    lat: float
    lon: float


@lru_cache(maxsize=1)
def _index() -> dict[tuple[int, int], list[Commune]]:
    grid: dict[tuple[int, int], list[Commune]] = {}
    text = gzip.decompress(DATA.read_bytes()).decode("utf-8")
    for code, name, lat, lon in csv.reader(io.StringIO(text), delimiter=";"):
        c = Commune(code, name, float(lat), float(lon))
        grid.setdefault(_cell(c.lat, c.lon), []).append(c)
    return grid


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / CELL), math.floor(lon / CELL))


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def sector(bearing: float, sectors: int = 8) -> tuple[str, str]:
    """French and English name of the 8 or 16 point compass sector."""
    if sectors not in (8, 16):
        raise ValueError("sectors must be 8 or 16")
    step = 360 / sectors
    i = int(((bearing % 360) + step / 2) // step) % sectors
    j = i * (16 // sectors)
    return SECTORS_16[j], SECTORS_16_EN[j]


def nearest_commune(lat: float, lon: float, max_km: float = 60) -> Commune | None:
    grid = _index()
    ci, cj = _cell(lat, lon)
    best, best_d = None, math.inf
    rings = int(max_km / 111 / CELL / max(0.2, math.cos(math.radians(lat)))) + 1
    for r in range(rings + 1):
        for i in range(ci - r, ci + r + 1):
            for j in range(cj - r, cj + r + 1):
                if max(abs(i - ci), abs(j - cj)) != r:
                    continue
                for c in grid.get((i, j), ()):
                    d = distance_m(lat, lon, c.lat, c.lon)
                    if d < best_d:
                        best, best_d = c, d
        # Every cell further out is at least r * CELL degrees of latitude away.
        if best is not None and best_d < r * CELL * 111_000 * max(0.2, math.cos(math.radians(lat))):
            break
    return best if best is not None and best_d <= max_km * 1000 else None


def format_distance(m: float, decimal: str = ",") -> str:
    """800 m, 1,2 km, 14 km."""
    if m < 100:
        return f"{int(round(m / 10)) * 10} m"
    if m < 975:
        return f"{int(round(m / 50)) * 50} m"
    if m < 9950:
        return f"{m / 1000:.1f}".replace(".", decimal) + " km"
    return f"{round(m / 1000)} km"


def _de(name: str) -> str:
    """French "de" before a commune name: d'Élancourt, du Mesnil, des Clayes."""
    if name.startswith("Le "):
        return "du " + name[3:]
    if name.startswith("Les "):
        return "des " + name[4:]
    if name[:1].lower() in "aeiouyàâäéèêëîïôöùûü":
        return "d'" + name
    return "de " + name


def _au(direction: str) -> str:
    return ("à l'" if direction[0] in "eo" else "au ") + direction


def describe_offline(lat: float, lon: float, sectors: int = 8) -> dict | None:
    c = nearest_commune(lat, lon)
    if c is None:
        return None
    d = distance_m(c.lat, c.lon, lat, lon)
    b = bearing_deg(c.lat, c.lon, lat, lon)
    fr, en = sector(b, sectors)
    if d < CENTRE_M:
        label, label_en = f"au centre {_de(c.name)}", f"in the centre of {c.name}"
    else:
        label = f"{format_distance(d)} {_au(fr)} {_de(c.name)}"
        label_en = f"{format_distance(d, '.')} {en} of {c.name}"
    return {"commune": {"code": c.code, "name": c.name, "lat": c.lat, "lon": c.lon},
            "distance_m": round(d), "bearing_deg": round(b, 1), "direction": fr, "direction_en": en,
            "label": label, "label_en": label_en}


# --- nearest road (online) -------------------------------------------------
_road_cache: OrderedDict[tuple, tuple[float, dict | None]] = OrderedDict()


def reverse_url() -> str:
    v = os.getenv("SARCADE_REVERSE_GEOCODER_URL")
    return DEFAULT_REVERSE_URL if v is None else v.strip()


def to_road(payload: dict, lat: float, lon: float) -> dict | None:
    for f in payload.get("features") or []:
        p = f.get("properties") or {}
        name = p.get("street") or p.get("name")
        if not name:
            continue
        try:
            rlon, rlat = f["geometry"]["coordinates"][:2]
            dist = round(distance_m(lat, lon, float(rlat), float(rlon)))
        except (KeyError, TypeError, ValueError):
            dist = p.get("distance")
        return {"name": name, "city": p.get("city") or "", "postcode": p.get("postcode") or "",
                "label": p.get("label") or name, "distance_m": dist}
    return None


async def nearest_road(lat: float, lon: float, client: httpx.AsyncClient | None = None) -> dict | None:
    """Nearest street from the IGN Géoplateforme, None when unknown or offline."""
    url = reverse_url()
    if not url:
        return None
    key = (round(lat, 4), round(lon, 4))
    now = time.monotonic()
    hit = _road_cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        _road_cache.move_to_end(key)
        return hit[1]
    own = client is None
    client = client or httpx.AsyncClient(timeout=REVERSE_TIMEOUT)
    try:
        r = await client.get(url, params={"lat": lat, "lon": lon, "index": "address",
                                          "limit": 1}, headers={"User-Agent": "SARCADE"})
        r.raise_for_status()
        road = to_road(r.json(), lat, lon)
    except (httpx.HTTPError, ValueError):
        return None  # silent fallback, not cached: retried at the next request
    finally:
        if own:
            await client.aclose()
    _road_cache[key] = (now, road)
    while len(_road_cache) > CACHE_SIZE:
        _road_cache.popitem(last=False)
    return road


# --- altitude (online) -----------------------------------------------------
_alt_cache: OrderedDict[tuple, tuple[float, float | None]] = OrderedDict()


def altimetry_url() -> str:
    v = os.getenv("SARCADE_ALTIMETRY_URL")
    return DEFAULT_ALTIMETRY_URL if v is None else v.strip()


def to_altitude(payload) -> float | None:
    """``{"elevations": [123.45]}`` (zonly) or ``[{"z": 123.45}]``; the IGN
    answers -99999 outside its data (sea, abroad)."""
    values = payload.get("elevations") if isinstance(payload, dict) else payload
    if not values:
        return None
    v = values[0]
    if isinstance(v, dict):
        v = v.get("z")
    try:
        z = float(v)
    except (TypeError, ValueError):
        return None
    return None if z <= NO_DATA + 1 or math.isnan(z) else round(z, 1)


async def altitude(lat: float, lon: float, client: httpx.AsyncClient | None = None) -> float | None:
    """Ground altitude (m, NGF) from the IGN Géoplateforme, None when unknown
    or offline (silent fallback, not cached on failure)."""
    url = altimetry_url()
    if not url:
        return None
    key = (round(lat, 4), round(lon, 4))
    now = time.monotonic()
    hit = _alt_cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        _alt_cache.move_to_end(key)
        return hit[1]
    own = client is None
    client = client or httpx.AsyncClient(timeout=REVERSE_TIMEOUT)
    try:
        r = await client.get(url, params={"lon": lon, "lat": lat, "resource": "ign_rge_alti_wld",
                                          "zonly": "true"}, headers={"User-Agent": "SARCADE"})
        r.raise_for_status()
        z = to_altitude(r.json())
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if own:
            await client.aclose()
    _alt_cache[key] = (now, z)
    while len(_alt_cache) > CACHE_SIZE:
        _alt_cache.popitem(last=False)
    return z


def department_of(code: str | None) -> str | None:
    """Department of an INSEE commune code: 78490 → 78, 2A004 → 2A."""
    if not code or len(code) < 2:
        return None
    return code[:3] if code.startswith("97") else code[:2]


async def describe(lat: float, lon: float, sectors: int = 8, online: bool = True,
                   client: httpx.AsyncClient | None = None) -> dict:
    out: dict = {"lat": lat, "lon": lon, "road": None, "altitude_m": None, "department": None}
    base = describe_offline(lat, lon, sectors)
    out.update(base or {"commune": None, "label": None, "label_en": None})
    if base:
        out["department"] = department_of(base["commune"]["code"])
    if online:
        out["road"] = await nearest_road(lat, lon, client)
        out["altitude_m"] = await altitude(lat, lon, client)
    return out

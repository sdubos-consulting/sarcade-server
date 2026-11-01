"""OpenSky Network client (ADS-B, step 1; card « ADS-B : aéronefs sur la
carte », 1 Nov 2026).

OAuth2 client credentials (basic authentication ended in March 2026); the
token lasts 30 minutes and is reused until shortly before. Identifiers come
from the server environment only (``SARCADE_OPENSKY_CLIENT_ID`` and
``SARCADE_OPENSKY_CLIENT_SECRET``, NAS ``.env``): never stored elsewhere,
never logged, never returned by the API.

Quota (Standard account, 4 000 credits a day): one ``/states/all`` request
on an area under 25 square degrees costs 1 credit, hence the 30 s minimum
interval of :mod:`.service` and the clamp of :func:`clamp_bbox`.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time

import httpx

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
STATES_URL = "https://opensky-network.org/api/states/all"
# Largest box still billed 1 credit: 25 square degrees (here 5° × 5°).
MAX_SPAN_DEG = 5.0
EMERGENCY_SQUAWKS = {"7700": "emergency", "7600": "radio_failure", "7500": "unlawful_interference"}
_FT = 3.28084
_KT = 1.943844


class OpenSkyError(RuntimeError):
    pass


class QuotaExceeded(OpenSkyError):
    def __init__(self, retry_after_s: int):
        super().__init__(f"quota exceeded, retry after {retry_after_s} s")
        self.retry_after_s = retry_after_s


def credentials() -> tuple[str, str] | None:
    cid = os.getenv("SARCADE_OPENSKY_CLIENT_ID", "").strip()
    secret = os.getenv("SARCADE_OPENSKY_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else None


def clamp_bbox(lamin: float, lomin: float, lamax: float, lomax: float) -> tuple[float, float, float, float]:
    """Keeps the box inside the world and under MAX_SPAN_DEG per side,
    centred on the requested one."""
    lamin, lamax = sorted((max(-90.0, min(90.0, lamin)), max(-90.0, min(90.0, lamax))))
    lomin, lomax = sorted((max(-180.0, min(180.0, lomin)), max(-180.0, min(180.0, lomax))))
    if lamax - lamin > MAX_SPAN_DEG:
        c = (lamin + lamax) / 2
        lamin, lamax = c - MAX_SPAN_DEG / 2, c + MAX_SPAN_DEG / 2
    if lomax - lomin > MAX_SPAN_DEG:
        c = (lomin + lomax) / 2
        lomin, lomax = c - MAX_SPAN_DEG / 2, c + MAX_SPAN_DEG / 2
    return round(lamin, 4), round(lomin, 4), round(lamax, 4), round(lomax, 4)


def parse_states(payload: dict) -> list[dict]:
    """State vectors (``extended=1``) → aircraft shown by the applications.
    Aircraft without a position are dropped."""
    out = []
    for s in payload.get("states") or []:
        if not isinstance(s, list) or len(s) < 17:
            continue
        lon, lat = s[5], s[6]
        if lon is None or lat is None:
            continue
        alt_m = s[13] if s[13] is not None else s[7]
        squawk = (s[14] or "").strip() or None
        category = s[17] if len(s) > 17 else None
        out.append({
            "icao24": (s[0] or "").lower(),
            "callsign": (s[1] or "").strip() or None,
            "country": s[2] or None,
            "lat": round(float(lat), 5), "lon": round(float(lon), 5),
            "alt_m": None if alt_m is None else round(float(alt_m)),
            "alt_ft": None if alt_m is None else round(float(alt_m) * _FT),
            "on_ground": bool(s[8]),
            "speed_kt": None if s[9] is None else round(float(s[9]) * _KT),
            "track_deg": None if s[10] is None else round(float(s[10])),
            "vertical_rate_mps": None if s[11] is None else round(float(s[11]), 1),
            "squawk": squawk,
            "emergency": EMERGENCY_SQUAWKS.get(squawk or ""),
            # OpenSky category (0 or 1: not transmitted; 8 = rotorcraft): icon
            # by type in the applications and on the TV (1 Nov 2026, 12 h 04).
            "category": category if isinstance(category, int) and not isinstance(category, bool) else None,
            "rotorcraft": category == 8,
            "last_contact": s[4],
            # Time of the position itself (OpenSky reception), the reference
            # of the estimated position on both screens (1 Nov 2026, 11 h 55).
            "position_time": s[3] if isinstance(s[3], (int, float)) and not isinstance(s[3], bool) else s[4],
        })
    return out


@dataclass
class _Token:
    value: str = ""
    expires_at: float = 0.0


class OpenSkyClient:
    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient | None = None):
        self._id, self._secret = client_id, client_secret
        self._http = http
        self._token = _Token()

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=20)
        return self._http

    async def token(self) -> str:
        if self._token.value and time.monotonic() < self._token.expires_at - 60:
            return self._token.value
        http = await self._client()
        r = await http.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                             "client_id": self._id, "client_secret": self._secret})
        if r.status_code != 200:
            raise OpenSkyError(f"token refused ({r.status_code})")
        body = r.json()
        self._token = _Token(body["access_token"], time.monotonic() + float(body.get("expires_in", 1800)))
        return self._token.value

    async def states(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        lamin, lomin, lamax, lomax = clamp_bbox(*bbox)
        http = await self._client()
        params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax, "extended": 1}
        for attempt in range(2):
            r = await http.get(STATES_URL, params=params, headers={"Authorization": f"Bearer {await self.token()}"})
            if r.status_code == 401 and attempt == 0:
                self._token = _Token()  # expired early: one new token
                continue
            break
        if r.status_code == 429:
            raise QuotaExceeded(int(r.headers.get("X-Rate-Limit-Retry-After-Seconds", "3600") or 3600))
        if r.status_code != 200:
            raise OpenSkyError(f"states refused ({r.status_code})")
        return parse_states(r.json())

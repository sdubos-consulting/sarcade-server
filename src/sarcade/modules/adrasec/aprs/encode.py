"""APRS packets sent by the server: positions of consenting operators,
transmitted as objects under the ADRASEC callsign, on local radio only
(decision of 10 Oct 2026: never to the Internet without a PCO decision).
"""
from __future__ import annotations

from datetime import datetime

# Destination identifying the software (experimental range APZxxx).
TOCALL = "APZSAR"


def _lat(value: float) -> str:
    hemi = "N" if value >= 0 else "S"
    v = abs(value)
    deg = int(v)
    minutes = round((v - deg) * 60, 2)
    if minutes >= 60:
        deg, minutes = deg + 1, 0.0
    return f"{deg:02d}{minutes:05.2f}{hemi}"


def _lon(value: float) -> str:
    hemi = "E" if value >= 0 else "W"
    v = abs(value)
    deg = int(v)
    minutes = round((v - deg) * 60, 2)
    if minutes >= 60:
        deg, minutes = deg + 1, 0.0
    return f"{deg:03d}{minutes:05.2f}{hemi}"


def object_report(name: str, lat: float, lon: float, time: datetime, *,
                  symbol: str = "/[", comment: str = "") -> str:
    """``;NAME_____*DDHHMMzDDMM.mmN/DDDMM.mmE[comment`` (object, UTC time)."""
    if not 1 <= len(name) <= 9:
        raise ValueError("invalid_object_name")
    ts = time.strftime("%d%H%Mz")
    return f";{name:<9}*{ts}{_lat(lat)}{symbol[0]}{_lon(lon)}{symbol[1]}{comment[:43]}"

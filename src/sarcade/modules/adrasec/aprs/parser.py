"""Decoding of APRS position packets in TNC2 text form.

Supported: uncompressed and compressed positions (with or without
timestamp), Mic-E (most mobile radios), objects and third-party traffic.
Everything else (status, messages, telemetry, weather-only) is ignored: the
server only needs positions of the selected stations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math


class NotAPosition(ValueError):
    pass


@dataclass
class Packet:
    source: str
    destination: str
    path: list[str]
    info: str


@dataclass
class Position:
    callsign: str  # station (or object name) the position belongs to
    lat: float
    lon: float
    symbol: str = ""  # table + code, for instance "/>" (car)
    course_deg: float | None = None
    speed_mps: float | None = None
    alt_m: float | None = None
    comment: str = ""
    sender: str = ""  # station that sent the packet (differs for objects)
    extra: dict = field(default_factory=dict)


KNOT = 0.514444
FOOT = 0.3048


def parse_tnc2(line: str) -> Packet:
    """``SRC>DEST,PATH1,PATH2*:info``."""
    line = line.rstrip("\r\n")
    head, sep, info = line.partition(":")
    if not sep or ">" not in head:
        raise NotAPosition("not_tnc2")
    source, _, rest = head.partition(">")
    parts = rest.split(",")
    if not source or not parts[0]:
        raise NotAPosition("not_tnc2")
    return Packet(source.strip().upper(), parts[0].strip().upper(), [p.strip() for p in parts[1:] if p.strip()], info)


def _uncompressed(body: str) -> tuple[float, float, str, str]:
    """``4903.50N/07201.75W-comment`` → lat, lon, symbol, rest."""
    if len(body) < 19:
        raise NotAPosition("short_position")
    lat_s, table, lon_s, code = body[0:8], body[8], body[9:18], body[18]
    lat_s = lat_s.replace(" ", "0")  # position ambiguity
    lon_s = lon_s.replace(" ", "0")
    try:
        lat = int(lat_s[0:2]) + float(lat_s[2:7]) / 60
        lon = int(lon_s[0:3]) + float(lon_s[3:8]) / 60
    except ValueError as exc:
        raise NotAPosition("invalid_position") from exc
    if lat_s[7] not in "NS" or lon_s[8] not in "EW":
        raise NotAPosition("invalid_position")
    lat = -lat if lat_s[7] == "S" else lat
    lon = -lon if lon_s[8] == "W" else lon
    return lat, lon, table + code, body[19:]


def _base91(s: str) -> int:
    value = 0
    for ch in s:
        n = ord(ch) - 33
        if not 0 <= n < 91:
            raise NotAPosition("invalid_compressed")
        value = value * 91 + n
    return value


def _compressed(body: str) -> tuple[float, float, str, str, dict]:
    if len(body) < 13:
        raise NotAPosition("short_position")
    table = body[0]
    lat = 90 - _base91(body[1:5]) / 380926
    lon = -180 + _base91(body[5:9]) / 190463
    code = body[9]
    extra: dict = {}
    cs, t = body[10], body[11]
    if cs != " " and len(body) >= 13:
        c, s_ = ord(cs) - 33, ord(t) - 33
        comp_type = ord(body[12]) - 33
        if (comp_type >> 3) & 0b11 == 0b10:  # GGA source: altitude
            extra["alt_m"] = (1.002 ** (c * 91 + s_)) * FOOT
        elif 0 <= c <= 89:
            extra["course_deg"] = float(c * 4 or 360)  # 0 means north
            extra["speed_mps"] = (1.08 ** s_ - 1) * KNOT
    return lat, lon, table + code, body[13:], extra


def _course_speed(rest: str) -> tuple[float | None, float | None, str]:
    """``088/036`` data extension (course / speed in knots)."""
    if len(rest) >= 7 and rest[3] == "/" and rest[:3].isdigit() and rest[4:7].isdigit():
        course, speed = int(rest[:3]), int(rest[4:7])
        return (float(course) if 0 < course <= 360 else None), speed * KNOT, rest[7:]
    return None, None, rest


def _altitude(comment: str) -> tuple[float | None, str]:
    """``/A=001234`` in the comment, in feet."""
    i = comment.find("/A=")
    if i >= 0 and len(comment) >= i + 9 and comment[i + 3:i + 9].lstrip("-").isdigit():
        return int(comment[i + 3:i + 9]) * FOOT, (comment[:i] + comment[i + 9:])
    return None, comment


def _position_body(body: str) -> Position:
    if not body:
        raise NotAPosition("empty")
    if body[0].isdigit() or body[0] == " ":
        lat, lon, symbol, rest = _uncompressed(body)
        course, speed, rest = _course_speed(rest)
        pos = Position("", lat, lon, symbol, course, speed)
    else:
        lat, lon, symbol, rest, extra = _compressed(body)
        pos = Position("", lat, lon, symbol, extra.get("course_deg"), extra.get("speed_mps"), extra.get("alt_m"))
    alt, rest = _altitude(rest)
    if alt is not None:
        pos.alt_m = alt
    pos.comment = rest.strip()
    return pos


def _mic_e(packet: Packet) -> Position:
    dest = packet.destination.split("-", 1)[0]
    info = packet.info
    if len(dest) < 6 or len(info) < 9:
        raise NotAPosition("short_mic_e")
    digits = []
    for ch in dest[:6]:
        if "0" <= ch <= "9":
            digits.append(ord(ch) - 48)
        elif "A" <= ch <= "J":
            digits.append(ord(ch) - 65)
        elif "P" <= ch <= "Y":
            digits.append(ord(ch) - 80)
        elif ch in "KLZ":
            digits.append(0)  # ambiguity
        else:
            raise NotAPosition("invalid_mic_e")
    lat = digits[0] * 10 + digits[1] + (digits[2] * 10 + digits[3] + (digits[4] * 10 + digits[5]) / 100) / 60
    north = "P" <= dest[3] <= "Z"
    lon_offset = 100 if "P" <= dest[4] <= "Z" else 0
    west = "P" <= dest[5] <= "Z"

    d = ord(info[1]) - 28 + lon_offset
    if 180 <= d <= 189:
        d -= 80
    elif 190 <= d <= 199:
        d -= 190
    m = ord(info[2]) - 28
    if m >= 60:
        m -= 60
    h = ord(info[3]) - 28
    lon = d + (m + h / 100) / 60
    if not (0 <= lat <= 90 and 0 <= lon <= 180):
        raise NotAPosition("invalid_mic_e")

    sp, dc, se = ord(info[4]) - 28, ord(info[5]) - 28, ord(info[6]) - 28
    speed = sp * 10 + dc // 10
    if speed >= 800:
        speed -= 800
    course = (dc % 10) * 100 + se
    if course >= 400:
        course -= 400
    symbol = info[8] + info[7]
    comment = info[9:]
    alt = None
    # Altitude: three base-91 characters followed by "}" (offset 10000 m).
    for start in (0, 1):
        if len(comment) >= start + 4 and comment[start + 3] == "}":
            try:
                alt = _base91(comment[start:start + 3]) - 10000
                comment = comment[:start] + comment[start + 4:]
            except NotAPosition:
                pass
            break
    return Position(
        "", -lat if not north else lat, -lon if west else lon, symbol,
        float(course) if 0 < course <= 360 else None, speed * KNOT, float(alt) if alt is not None else None,
        comment.strip(),
    )


def decode(line: str) -> Position:
    """Position of a TNC2 line, or NotAPosition."""
    packet = parse_tnc2(line)
    info = packet.info
    if not info:
        raise NotAPosition("empty")
    dti = info[0]
    if dti == "}":  # third-party traffic: decode the inner packet
        inner = decode(info[1:])
        return inner
    if dti in "!=":
        pos = _position_body(info[1:])
    elif dti in "/@":
        pos = _position_body(info[8:])  # 7-character timestamp
    elif dti in "`'\x1c\x1d":
        pos = _mic_e(packet)
    elif dti == ";":  # object: ;NAME_____*DDHHMMz + position
        if len(info) < 18:
            raise NotAPosition("short_object")
        if info[10] != "*":
            raise NotAPosition("killed_object")
        pos = _position_body(info[18:])
        pos.callsign = info[1:10].strip().upper()
        pos.sender = packet.source
        pos.extra["object"] = True
        _check(pos)
        return pos
    else:
        raise NotAPosition("not_a_position")
    pos.callsign = packet.source
    pos.sender = packet.source
    _check(pos)
    return pos


def _check(pos: Position) -> None:
    if not (math.isfinite(pos.lat) and math.isfinite(pos.lon) and -90 <= pos.lat <= 90 and -180 <= pos.lon <= 180):
        raise NotAPosition("out_of_range")
    if pos.lat == 0 and pos.lon == 0:
        raise NotAPosition("null_island")

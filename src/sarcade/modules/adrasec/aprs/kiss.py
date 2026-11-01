"""KISS framing and AX.25 UI frames, to talk to a software modem such as
Dire Wolf over TCP (the radio side of the Gateway).
"""
from __future__ import annotations

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
CONTROL_UI, PID_NO_L3 = 0x03, 0xF0


class InvalidFrame(ValueError):
    pass


def escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == FEND:
            out += bytes((FESC, TFEND))
        elif b == FESC:
            out += bytes((FESC, TFESC))
        else:
            out.append(b)
    return bytes(out)


def unescape(data: bytes) -> bytes:
    out = bytearray()
    it = iter(data)
    for b in it:
        if b == FESC:
            nxt = next(it, None)
            if nxt == TFEND:
                out.append(FEND)
            elif nxt == TFESC:
                out.append(FESC)
            else:
                raise InvalidFrame("bad_escape")
        else:
            out.append(b)
    return bytes(out)


def kiss_frame(ax25: bytes, port: int = 0) -> bytes:
    return bytes((FEND, (port & 0x0F) << 4)) + escape(ax25) + bytes((FEND,))


class KissDecoder:
    """Accumulates bytes from the TCP stream and yields AX.25 frames."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        frames = []
        self._buf += data
        while True:
            try:
                start = self._buf.index(FEND)
            except ValueError:
                self._buf.clear()
                return frames
            try:
                end = self._buf.index(FEND, start + 1)
            except ValueError:
                del self._buf[:start]
                return frames
            raw = bytes(self._buf[start + 1:end])
            del self._buf[:end]  # keep the closing FEND as next opener
            if len(raw) < 2 or raw[0] & 0x0F != 0:  # data frames only
                continue
            try:
                frames.append(unescape(raw[1:]))
            except InvalidFrame:
                continue


def _encode_address(call: str, last: bool, repeated: bool = False) -> bytes:
    name, _, ssid = call.upper().partition("-")
    if not 1 <= len(name) <= 6 or not name.isalnum():
        raise InvalidFrame(f"invalid_ax25_callsign:{call}")
    n = int(ssid) if ssid else 0
    if not 0 <= n <= 15:
        raise InvalidFrame(f"invalid_ax25_ssid:{call}")
    out = bytes((ord(c) << 1) for c in name.ljust(6))
    flags = 0x60 | (n << 1) | (0x80 if repeated else 0) | (0x01 if last else 0)
    return out + bytes((flags,))


def encode_ui(source: str, destination: str, path: list[str], info: str) -> bytes:
    addresses = [destination, source] + list(path)
    out = bytearray()
    for i, call in enumerate(addresses):
        repeated = call.endswith("*")
        out += _encode_address(call.rstrip("*"), last=i == len(addresses) - 1, repeated=repeated)
    out += bytes((CONTROL_UI, PID_NO_L3))
    out += info.encode("latin-1", errors="replace")
    return bytes(out)


def _decode_address(chunk: bytes) -> tuple[str, bool, bool]:
    name = "".join(chr(b >> 1) for b in chunk[:6]).strip()
    ssid = (chunk[6] >> 1) & 0x0F
    call = f"{name}-{ssid}" if ssid else name
    return call, bool(chunk[6] & 0x80), bool(chunk[6] & 0x01)


def ax25_to_tnc2(frame: bytes) -> str:
    """AX.25 UI frame → ``SRC>DEST,PATH*:info`` text."""
    addresses = []
    i = 0
    while True:
        if i + 7 > len(frame):
            raise InvalidFrame("truncated_address")
        call, h_bit, last = _decode_address(frame[i:i + 7])
        addresses.append((call, h_bit))
        i += 7
        if last:
            break
        if len(addresses) > 10:
            raise InvalidFrame("too_many_addresses")
    if len(addresses) < 2 or i + 2 > len(frame):
        raise InvalidFrame("truncated_frame")
    if frame[i] != CONTROL_UI or frame[i + 1] != PID_NO_L3:
        raise InvalidFrame("not_aprs")
    info = frame[i + 2:].decode("latin-1")
    dest, source = addresses[0][0], addresses[1][0]
    path = [c + ("*" if h else "") for c, h in addresses[2:]]
    return f"{source}>{dest}{''.join(',' + p for p in path)}:{info}"

"""Callsigns and callsign groups (note « APRS » in the vault).

An entry without SSID covers every SSID of the callsign (F4JPO matches
F4JPO, F4JPO-7, F4JPO-9); an entry with SSID matches only that station.
"""
from __future__ import annotations

import re

# AX.25 callsigns are 1-6 characters with an SSID 0-15; APRS-IS also carries
# longer names, so up to 9 characters and an alphanumeric SSID are accepted.
_CALLSIGN = re.compile(r"^[A-Z0-9]{1,9}(-[A-Z0-9]{1,2})?$")

MAX_CALLSIGNS = 500


class InvalidCallsign(ValueError):
    pass


def normalize(value) -> str:
    if not isinstance(value, str):
        raise InvalidCallsign("invalid_callsign")
    call = value.strip().upper()
    if call.endswith("-0"):
        call = call[:-2]
    if not _CALLSIGN.match(call):
        raise InvalidCallsign(f"invalid_callsign:{value}")
    return call


def normalize_list(values) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_CALLSIGNS:
        raise InvalidCallsign("invalid_callsign_list")
    result: list[str] = []
    for v in values:
        call = normalize(v)
        if call not in result:
            result.append(call)
    return result


def base(call: str) -> str:
    return call.split("-", 1)[0]


def matches(call: str, entries) -> bool:
    """Whether a heard station belongs to the selected callsigns."""
    call = call.upper()
    for entry in entries:
        if "-" in entry:
            if call == entry:
                return True
        elif base(call) == entry:
            return True
    return False


def budlist_filter(entries) -> str:
    """APRS-IS server-side filter: only the selected stations are sent.

    ``b/`` is the budlist filter; ``F4JPO*`` covers every SSID. An empty list
    gives a filter that matches nothing, so the server receives nothing."""
    terms = []
    for entry in sorted(set(entries)):
        terms.append(entry if "-" in entry else f"{entry}*")
    if not terms:
        return "b/SARCADE-NONE"
    return "b/" + "/".join(terms)

"""Validation of the personal settings: only known keys, bounded values."""
import re

MAX_FAVOURITES = 500
MAX_COLOURS = 16


class InvalidPreferences(ValueError):
    pass


def _colour(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 0xFFFFFFFF:
        raise InvalidPreferences("invalid_value")
    return v


def _favourites(v) -> list[str]:
    if not isinstance(v, list) or len(v) > MAX_FAVOURITES:
        raise InvalidPreferences("invalid_value")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or not 0 < len(x) <= 64:
            raise InvalidPreferences("invalid_value")
        if x not in out:
            out.append(x)
    return out


def _history(v) -> list[int]:
    if not isinstance(v, list) or len(v) > MAX_COLOURS:
        raise InvalidPreferences("invalid_value")
    return [_colour(x) for x in v]


MAX_PINS = 6
_TOOL_ID = re.compile(r"^[a-z0-9_-]{1,32}$")
# Icon of an operator on the map, same meanings as the APRS symbols
# (1 Nov 2026); the one chosen in SARCADE wins over the APRS one.
OPERATOR_ICONS = ("walk", "bike", "moto", "car", "4x4", "truck", "ambulance", "fire", "boat",
                  "helicopter", "plane", "fixed", "relay", "pco")


def _pins(v) -> list[str]:
    """Tools pinned in the left bar (web, Windows, tablet), in order."""
    if not isinstance(v, list) or len(v) > MAX_PINS:
        raise InvalidPreferences("invalid_value")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or not _TOOL_ID.match(x):
            raise InvalidPreferences("invalid_value")
        if x not in out:
            out.append(x)
    return out


def _icon(v) -> str:
    if v not in OPERATOR_ICONS:
        raise InvalidPreferences("invalid_value")
    return v


KEYS = {
    # Stations and operators marked with a star (Équipes, carte).
    "favourites": _favourites,
    # Preferred colour (drawings, dot of the bearings), kept from one event
    # to the next; and the colours chosen before (« historisée »).
    "color": _colour,
    "color_history": _history,
    # Left bar: tools pinned under the four fixed buttons (1 Nov 2026).
    "rail_pins": _pins,
    # Icon of the operator on the map (1 Nov 2026).
    "operator_icon": _icon,
}


def apply_patch(current: dict | None, patch: dict) -> dict:
    if not isinstance(patch, dict) or not patch:
        raise InvalidPreferences("empty_patch")
    out = dict(current or {})
    for key, value in patch.items():
        check = KEYS.get(key)
        if check is None:
            raise InvalidPreferences("unknown_setting")
        if value is None:
            out.pop(key, None)
            continue
        out[key] = check(value)
    if "color" in patch and patch["color"] is not None:
        # Each colour chosen goes to the front of the history.
        history = [c for c in out.get("color_history", []) if c != out["color"]]
        out["color_history"] = [out["color"], *history][:MAX_COLOURS]
    return {k: v for k, v in out.items() if k in KEYS}


def normal_email(email: str) -> str:
    return email.strip().lower()

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


# Card « Carte : la trace d'un opérateur dans la couleur choisie dans son
# profil » (validated on 10 Nov 2026, choice 4): the red stays reserved for
# the SOS; a red profile colour is shared as this blue, as for the points of
# interest (poi_sheet.dart, ``isReservedRed`` / ``poiFallbackColor``).
RESERVED_RED_FALLBACK = 0xFF1E88E5


def is_reserved_red(argb: int) -> bool:
    """Same rule as the application: hue within 20° of 0, saturation above
    0.45 and value above 0.35."""
    r, g, b = (argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF
    hi, lo = max(r, g, b), min(r, g, b)
    if hi == 0:
        return False
    saturation, value = (hi - lo) / hi, hi / 255
    if hi == lo:
        hue = 0.0
    elif hi == r:
        hue = (60 * (g - b) / (hi - lo)) % 360
    elif hi == g:
        hue = 60 * (b - r) / (hi - lo) + 120
    else:
        hue = 60 * (r - g) / (hi - lo) + 240
    return saturation > 0.45 and value > 0.35 and (hue <= 20 or hue >= 340)


def operator_colour(v) -> int | None:
    """Colour an operator shares with the others (10 Nov 2026): his profile
    colour, or the automatic one of the event; 0 removes it; opaque; a red
    becomes ``RESERVED_RED_FALLBACK``. Raises InvalidPreferences."""
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 0xFFFFFFFF:
        raise InvalidPreferences("invalid_value")
    if v == 0:
        return None
    v |= 0xFF000000
    return RESERVED_RED_FALLBACK if is_reserved_red(v) else v


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


MAX_IMPORTANT = 500
_IMPORTANT_ID = re.compile(r"^[^/\s]{1,64}/[^/\s]{1,64}$")


def _important(v) -> list[str]:
    """« Messages importants » (lot 29, group 7): « event/message », most
    recent first, found again on every device of the person."""
    if not isinstance(v, list) or len(v) > MAX_IMPORTANT:
        raise InvalidPreferences("invalid_value")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or not _IMPORTANT_ID.match(x):
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
    # « Messages importants » (lot 29, group 7).
    "important_messages": _important,
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

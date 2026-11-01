"""Operational settings of an event, set by the PCO.

Covers the low-bandwidth mode (see « Synchronisation client-serveur » in the
vault) and the position tracking policy (« Suivi de position », Beacon in
English). Pure functions: validation, logbook summaries and device status, so
that the rules are testable without a database.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Intervals offered to operators, in seconds (10 s to 10 min).
TRACKING_INTERVALS = (10, 30, 60, 120, 300, 600)

DEFAULT_SETTINGS: dict = {
    # Low-bandwidth mode, imposed by the PCO: grouped sending, no photos.
    "low_bandwidth": False,
    "low_bandwidth_interval_s": 60,
    # Alert when items wait in a device outbox, and stale device threshold.
    "sync_alert_minutes": 5,
    # Position tracking: operator choice, may be required by the PCO.
    "tracking_required": False,
    "tracking_default_interval_s": 30,
    "tracking_min_interval_s": 10,
    "tracking_max_interval_s": 600,
    # APRS (note « APRS »): callsign groups followed by the event, callsigns
    # added by the PCO for the event, local radio transmission of positions.
    "aprs_groups": [],
    "aprs_callsigns": [],
    "aprs_tx_rf": False,
    # Callsign of the local radio transmission, chosen by the PCO of the
    # event (decision of 28 Oct 2026: the ADRASEC has no callsign).
    "aprs_tx_callsign": None,
    # Stations without news for longer are « Anciens »: folded in the list,
    # hidden on the map (decision of 28 Oct 2026, 2 h by default).
    "position_old_min": 120,
    # Predefined messages sent in one touch (ATAK 6), set by the PCO;
    # empty: the applications offer their own list.
    "quick_messages": [],
    # Weather (note « Prévisions météo »): forecast point of the event and
    # department for the Météo-France vigilance.
    "weather_lat": None,
    "weather_lon": None,
    "department": None,
    # Base map shown by default for the event (note « Choix du fond de carte »).
    "basemap": "osm",
    # Modules enabled for the event (sarcade.modules, 31 Oct 2026); older
    # events keep ADRASEC, new ones get the modules of their event model.
    "modules": ["adrasec"],
    # Probable zone of each searched signal (card « Plusieurs zones »,
    # points 8 and 11, 1 Nov 2026): key of the signal (sarcade.tv.search_key)
    # → « multi » (Plusieurs balises) or « single » (Une seule balise);
    # absent: « auto ». Set by any operator of the event (zone_mode route).
    "zone_modes": {},
}

_BOOL_KEYS = {"low_bandwidth", "tracking_required", "aprs_tx_rf"}
_LIST_KEYS = {"aprs_groups", "aprs_callsigns", "quick_messages"}
_COORD_KEYS = {"weather_lat": 90, "weather_lon": 180}
_INT_RANGES = {
    "low_bandwidth_interval_s": (15, 3600),
    "sync_alert_minutes": (1, 120),
    "position_old_min": (15, 1440),
}
_INTERVAL_KEYS = {"tracking_default_interval_s", "tracking_min_interval_s", "tracking_max_interval_s"}


class InvalidSettings(ValueError):
    pass


def merged(stored: dict | None) -> dict:
    """Stored settings completed with the defaults (older events)."""
    result = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
              for k, v in DEFAULT_SETTINGS.items()}
    result.update({k: v for k, v in (stored or {}).items() if k in DEFAULT_SETTINGS})
    return result


def apply_patch(stored: dict | None, patch: dict) -> dict:
    """Returns the new settings, or raises InvalidSettings."""
    if not isinstance(patch, dict) or not patch:
        raise InvalidSettings("empty_patch")
    result = merged(stored)
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            raise InvalidSettings(f"unknown_setting:{key}")
        if key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise InvalidSettings(f"invalid_value:{key}")
        elif key in _LIST_KEYS:
            value = _clean_list(key, value)
        elif key in _COORD_KEYS:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not -_COORD_KEYS[key] <= value <= _COORD_KEYS[key]):
                raise InvalidSettings(f"invalid_value:{key}")
            value = float(value) if value is not None else None
        elif key == "modules":
            from sarcade.modules import InvalidModules, clean
            try:
                value = clean(value)
            except InvalidModules as exc:
                raise InvalidSettings(str(exc)) from exc
        elif key == "zone_modes":
            value = clean_zone_modes(value)
        elif key == "basemap":
            if not (isinstance(value, str) and 1 <= len(value) <= 40):
                raise InvalidSettings("invalid_value:basemap")
        elif key == "aprs_tx_callsign":
            if value is not None:
                from sarcade.domain.callsigns import InvalidCallsign, normalize
                try:
                    value = normalize(value)
                except (InvalidCallsign, TypeError) as exc:
                    raise InvalidSettings("invalid_value:aprs_tx_callsign") from exc
        elif key == "department":
            if value is not None and not (isinstance(value, str) and 2 <= len(value.strip()) <= 3
                                          and value.strip().isalnum()):
                raise InvalidSettings("invalid_value:department")
            value = value.strip().upper() if value else None
        elif key in _INTERVAL_KEYS:
            if isinstance(value, bool) or value not in TRACKING_INTERVALS:
                raise InvalidSettings(f"invalid_value:{key}")
        else:
            low, high = _INT_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise InvalidSettings(f"invalid_value:{key}")
        result[key] = value
    # APRS belongs to the ADRASEC module (31 Oct 2026): not set without it,
    # and the radio transmission stops when the module is turned off.
    if "adrasec" not in result["modules"]:
        if any(k.startswith("aprs_") and v not in (None, False, []) for k, v in patch.items()):
            raise InvalidSettings("module_inactive:adrasec")
        result["aprs_tx_rf"] = False
    if not (result["tracking_min_interval_s"] <= result["tracking_default_interval_s"]
            <= result["tracking_max_interval_s"]):
        raise InvalidSettings("inconsistent_tracking_bounds")
    return result


MAX_ZONE_MODES = 100
SEARCH_MAX = 120


def clean_zone_modes(value) -> dict:
    """{signal: mode}: keys normalised as sarcade.tv.search_key, « auto »
    left out (it is the default)."""
    from sarcade.tv import ZONE_MODES, search_key
    if not isinstance(value, dict) or len(value) > MAX_ZONE_MODES:
        raise InvalidSettings("invalid_value:zone_modes")
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or len(k) > SEARCH_MAX or v not in ZONE_MODES:
            raise InvalidSettings("invalid_value:zone_modes")
        if v != "auto":
            out[search_key(k)] = v
    return out


def set_zone_mode(stored: dict | None, search: str, mode: str) -> dict:
    """Settings with the mode of one searched signal changed."""
    from sarcade.tv import ZONE_MODES, search_key
    if not isinstance(search, str) or len(search) > SEARCH_MAX or mode not in ZONE_MODES:
        raise InvalidSettings("invalid_value:zone_modes")
    result = merged(stored)
    modes = dict(result["zone_modes"])
    key = search_key(search)
    if mode == "auto":
        modes.pop(key, None)
    else:
        modes[key] = mode
    if len(modes) > MAX_ZONE_MODES:
        raise InvalidSettings("invalid_value:zone_modes")
    result["zone_modes"] = modes
    return result


ZONE_MODE_NAMES = {"auto": "Auto", "multi": "Plusieurs balises", "single": "Une seule balise"}


def _clean_list(key: str, value) -> list[str]:
    if key == "aprs_callsigns":
        from sarcade.domain.callsigns import InvalidCallsign, normalize_list
        try:
            return normalize_list(value)
        except InvalidCallsign as exc:
            raise InvalidSettings(f"invalid_value:{key}") from exc
    if key == "quick_messages":
        if not isinstance(value, list) or len(value) > 20 or not all(
                isinstance(v, str) and 0 < len(v.strip()) <= 120 for v in value):
            raise InvalidSettings(f"invalid_value:{key}")
        return list(dict.fromkeys(v.strip() for v in value))
    if not isinstance(value, list) or len(value) > 50 or not all(
            isinstance(v, str) and 0 < len(v) <= 64 for v in value):
        raise InvalidSettings(f"invalid_value:{key}")
    return list(dict.fromkeys(value))


def clamp_interval(requested: int | None, settings: dict) -> int:
    """Interval actually used by a device: the operator's choice within the
    bounds set by the PCO, or the event default."""
    s = merged(settings)
    if requested is None:
        return s["tracking_default_interval_s"]
    return max(s["tracking_min_interval_s"], min(s["tracking_max_interval_s"], int(requested)))


def _duration(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} s"


def logbook_summaries(old: dict | None, new: dict) -> list[str]:
    """French logbook lines describing what the PCO changed."""
    before, after = merged(old), merged(new)
    lines = []
    if before["low_bandwidth"] != after["low_bandwidth"]:
        lines.append("Mode liaison faible activé par le PCO" if after["low_bandwidth"]
                     else "Mode liaison faible levé par le PCO")
    if after["low_bandwidth"] and before["low_bandwidth_interval_s"] != after["low_bandwidth_interval_s"]:
        lines.append(f"Liaison faible : envoi groupé toutes les {_duration(after['low_bandwidth_interval_s'])}")
    if before["tracking_required"] != after["tracking_required"]:
        lines.append("Suivi de position imposé par le PCO" if after["tracking_required"]
                     else "Suivi de position laissé au choix des opérateurs")
    bounds = ("tracking_min_interval_s", "tracking_default_interval_s", "tracking_max_interval_s")
    if any(before[k] != after[k] for k in bounds):
        lines.append(
            "Suivi de position : fréquence de "
            f"{_duration(after['tracking_min_interval_s'])} à {_duration(after['tracking_max_interval_s'])}, "
            f"{_duration(after['tracking_default_interval_s'])} par défaut")
    if before["aprs_groups"] != after["aprs_groups"] or before["aprs_callsigns"] != after["aprs_callsigns"]:
        lines.append(f"APRS : {len(after['aprs_groups'])} groupe(s) d'indicatifs et "
                     f"{len(after['aprs_callsigns'])} indicatif(s) ajouté(s) suivis")
    if before["aprs_tx_rf"] != after["aprs_tx_rf"]:
        lines.append("APRS : émission radio locale des positions activée par le PCO" if after["aprs_tx_rf"]
                     else "APRS : émission radio locale des positions arrêtée")
    if before["aprs_tx_callsign"] != after["aprs_tx_callsign"]:
        lines.append(f"APRS : émission radio locale sous l'indicatif {after['aprs_tx_callsign']}"
                     if after["aprs_tx_callsign"] else "APRS : indicatif d'émission radio locale retiré")
    if before["quick_messages"] != after["quick_messages"]:
        lines.append(f"Messages prédéfinis : {len(after['quick_messages'])} message(s) proposé(s)")
    if before["position_old_min"] != after["position_old_min"]:
        lines.append(f"Stations « anciennes » après {_duration(after['position_old_min'] * 60)} sans nouvelles")
    if (before["weather_lat"], before["weather_lon"]) != (after["weather_lat"], after["weather_lon"]):
        if after["weather_lat"] is not None and after["weather_lon"] is not None:
            lines.append(f"Météo : point de prévision {after['weather_lat']:.4f}, {after['weather_lon']:.4f}")
    if before["department"] != after["department"] and after["department"]:
        lines.append(f"Météo : vigilance suivie pour le département {after['department']}")
    if before["basemap"] != after["basemap"]:
        lines.append(f"Fond de carte par défaut : {after['basemap']}")
    if before["modules"] != after["modules"]:
        from sarcade.modules import MODULES
        names = ", ".join(MODULES[m]["label"] for m in after["modules"]) or "aucun (Core seul)"
        lines.append(f"Modules de l'événement : {names}")
    if before["zone_modes"] != after["zone_modes"]:
        for key in sorted(set(before["zone_modes"]) | set(after["zone_modes"])):
            mode = after["zone_modes"].get(key, "auto")
            if before["zone_modes"].get(key, "auto") != mode:
                lines.append(f"Zone probable « {key or 'sans signal'} » : {ZONE_MODE_NAMES[mode]}")
    if before["sync_alert_minutes"] != after["sync_alert_minutes"]:
        lines.append(f"Alerte de synchronisation après {after['sync_alert_minutes']} min")
    return lines


def device_status(last_contact_at: datetime | None, settings: dict, now: datetime) -> str:
    """« ok », « late » (beyond the alert threshold) or « unknown »."""
    if last_contact_at is None:
        return "unknown"
    limit = timedelta(minutes=merged(settings)["sync_alert_minutes"])
    return "late" if now - last_contact_at > limit else "ok"

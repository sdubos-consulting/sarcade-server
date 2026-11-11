"""Modules of SARCADE, enabled per event (card « Séparer le Core et les
modules », step 2, 31 Oct 2026; vault « Inventaire Core et modules »).

The Core (map, positions, messages, groups, points, drawings, SOS, offline,
TV, routes, weather, files, logbook) is always there. A module adds the
functions of one use: ``adrasec`` (bearings and probable zone, resection,
APRS, high points and relays, fixed stations), ``race`` (courses, posts,
runners; empty for now). Not to be confused with the Pro *licence* modules
(``sarcade.licensing``: siem, assist), which are per server.

Stored in the event settings (key ``modules``): events created before this
step keep ``["adrasec"]`` (default), so nothing changes for them.
"""
from __future__ import annotations

# Order = display order in the applications.
MODULES: dict[str, dict] = {
    "adrasec": {"label": "ADRASEC", "description": "Relevés, résection, APRS, points hauts et relais"},
    "race": {"label": "Course", "description": "Parcours, postes, coureurs"},
}

# Event models (« modèles d'événement »): kind -> modules enabled at creation.
# Free kinds (older clients, scripts) fall back to DEFAULT_MODULES.
TEMPLATES: dict[str, list[str]] = {
    "family": [],
    "friends": [],
    "exercise": ["adrasec"],
    "sater": ["adrasec"],
    "mission": ["adrasec"],
    "race": ["race"],
}

DEFAULT_MODULES: list[str] = ["adrasec"]


class InvalidModules(ValueError):
    pass


def clean(value) -> list[str]:
    """Known modules, without duplicates, in registry order."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidModules("invalid_value:modules")
    unknown = [v for v in value if v not in MODULES]
    if unknown:
        raise InvalidModules(f"unknown_module:{unknown[0]}")
    return [m for m in MODULES if m in value]


def for_kind(kind: str | None) -> list[str]:
    """Modules of a new event from its kind (event model)."""
    return list(TEMPLATES.get((kind or "").strip().lower(), DEFAULT_MODULES))


# Event models of a SATER mission or exercise (lot 24): the setting « sater »
# is on at creation. The models « mission SATER » / « exercice SATER » of
# the ADRASEC card, step 2, will join this set.
SATER_KINDS = {"sater"}


def is_sater_kind(kind: str | None) -> bool:
    return (kind or "").strip().lower() in SATER_KINDS


def enabled(settings: dict | None, module: str) -> bool:
    """True when [module] is on for an event (merged settings or stored)."""
    mods = (settings or {}).get("modules")
    return module in (DEFAULT_MODULES if mods is None else mods)


def catalogue() -> list[dict]:
    """Modules and event models, for the applications."""
    return [{"id": k, **v} for k, v in MODULES.items()]

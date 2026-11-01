"""Hooks between the Core and the modules (lot C, card « Séparer le Core et
les modules », 1 Nov 2026).

The Core never imports a module. A module fills these registries in its
``register()``, called once by :func:`sarcade.modules.loader.load`:

- ``routers``: FastAPI routers added to the application;
- ``startups``: ``start(background_tasks)`` run in the application lifespan;
- ``position_hooks``: ``hook(settings, device, lat, lon, at)`` after each
  position received from a terminal (APRS transmission, for instance);
- ``link_providers``: ``provider() -> list`` of link states shown by the
  diagnostics (each has ``enabled``, ``connected``... like the APRS links);
- ``tv_state_providers``: extra layers in the state of a TV.

Without any module the Core runs the same, with empty registries.
"""
from __future__ import annotations

from collections.abc import Callable
import logging

log = logging.getLogger("sarcade.modules")

routers: list = []
startups: list[Callable[[list], None]] = []
position_hooks: list[Callable] = []
link_providers: list[Callable[[], list]] = []
# ``provider(event, display, options) -> dict`` merged into the state of a
# TV (aircraft of the ADRASEC module, for instance).
tv_state_providers: list[Callable] = []


def start(background_tasks: list) -> None:
    for start_one in startups:
        start_one(background_tasks)


def position_received(settings: dict | None, device, lat: float, lon: float, at) -> None:
    """A module failing here never loses the position already stored."""
    for hook in position_hooks:
        try:
            hook(settings, device, lat, lon, at)
        except Exception:  # noqa: BLE001 - logged, the position is kept
            log.exception("position hook failed")


def tv_state(event, display: dict, options: dict) -> dict:
    out: dict = {}
    for provider in tv_state_providers:
        try:
            out.update(provider(event, display, options) or {})
        except Exception:  # noqa: BLE001 - the TV keeps its other layers
            log.exception("tv state provider failed")
    return out


def links() -> list:
    return [link for provider in link_providers for link in provider()]

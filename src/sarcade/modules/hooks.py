"""Hooks between the Core and the modules (lot C, card « Séparer le Core et
les modules », 1 Nov 2026).

The Core never imports a module. A module fills these registries in its
``register()``, called once by :func:`sarcade.modules.loader.load`:

- ``routers``: FastAPI routers added to the application;
- ``startups``: ``start(background_tasks)`` run in the application lifespan;
- ``position_hooks``: ``hook(settings, device, lat, lon, at)`` after each
  position received from a terminal (APRS transmission, for instance);
- ``link_providers``: ``provider() -> list`` of link states shown by the
  diagnostics (each has ``enabled``, ``connected``... like the APRS links).

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


def links() -> list:
    return [link for provider in link_providers for link in provider()]

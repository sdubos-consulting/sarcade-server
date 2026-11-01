"""ADRASEC module: APRS (radio and APRS-IS), ADS-B (aircraft, OpenSky), bearings settings, reference
sites (high points, relays). Licensed radio amateurs only: the rule of the
vault « Inventaire Core et modules » (1 Nov 2026) puts here what needs a
licence (APRS, Packet, Winlink); MeshCore and Meshtastic stay in the Core.

Whether an event uses it is per event (``sarcade.modules.enabled``); the
code below is registered once for the server.
"""
from __future__ import annotations


def register() -> None:
    from sarcade.modules import hooks

    from . import api_adsb, api_aprs, api_reference, api_signals
    from .adsb import service as adsb
    from .aprs import links, service

    hooks.routers.extend([api_aprs.router, api_reference.router, api_signals.router, api_adsb.router])
    hooks.startups.append(links.start)
    hooks.startups.append(adsb.start)
    hooks.position_hooks.append(service.queue_transmission)
    hooks.link_providers.append(lambda: [service.runtime.aprs_is, service.runtime.radio])

"""Base maps (note « Choix du fond de carte »).

Validated decisions: OpenStreetMap by default; first version with
OpenStreetMap, topographic, IGN map and IGN aerial photographs; offline
packages limited to the ADRASEC department; custom base maps in the Core.

Offline packages are MBTiles files produced for that purpose (bulk
download from the public OpenStreetMap tile server is forbidden), placed by
the administrator in ``SARCADE_TILES_ROOT`` or uploaded through the API.
"""
from __future__ import annotations

import re

IGN_WMTS = ("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER={layer}"
            "&STYLE=normal&TILEMATRIXSET=PM&FORMAT={fmt}&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}")

BUILT_IN = (
    {"id": "osm", "name": "OpenStreetMap", "kind": "raster", "default": True, "max_zoom": 19,
     "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
     "attribution": "© OpenStreetMap contributors", "licence": "ODbL",
     "usage": "Plan général, routes, bâtiments"},
    {"id": "topo", "name": "Topographique", "kind": "raster", "default": False, "max_zoom": 17,
     "url": "https://tile.opentopomap.org/{z}/{x}/{y}.png",
     "attribution": "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)", "licence": "CC BY-SA",
     "usage": "Relief, courbes de niveau, sentiers"},
    {"id": "ign-plan", "name": "Plan IGN", "kind": "raster", "default": False, "max_zoom": 19,
     "url": IGN_WMTS.format(layer="GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", fmt="image/png"),
     "attribution": "© IGN, Géoplateforme", "licence": "Licence Ouverte",
     "usage": "Cartographie française officielle"},
    {"id": "ign-photos", "name": "Photographies aériennes IGN", "kind": "raster", "default": False, "max_zoom": 19,
     "url": IGN_WMTS.format(layer="ORTHOIMAGERY.ORTHOPHOTOS", fmt="image/jpeg"),
     "attribution": "© IGN, Géoplateforme", "licence": "Licence Ouverte",
     "usage": "Repérage visuel : bâtiments, clairières, plans d'eau"},
)

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_TEMPLATE = re.compile(r"^https?://[^\s]+$")


class InvalidBasemap(ValueError):
    pass


def validate_custom(payload: dict) -> dict:
    """Custom base map added by the administrator: a tile URL template
    (``{z}``, ``{x}``, ``{y}``) or an offline package only."""
    bid = str(payload.get("id", "")).strip().lower()
    if not _ID.match(bid) or bid in {b["id"] for b in BUILT_IN}:
        raise InvalidBasemap("invalid_id")
    name = str(payload.get("name", "")).strip()
    if not 1 <= len(name) <= 80:
        raise InvalidBasemap("invalid_name")
    url = (payload.get("url") or "").strip() or None
    if url is not None and (not _TEMPLATE.match(url) or not all(k in url for k in ("{z}", "{x}", "{y}"))):
        raise InvalidBasemap("invalid_url")
    attribution = str(payload.get("attribution", "")).strip()
    if not 1 <= len(attribution) <= 200:
        raise InvalidBasemap("invalid_attribution")
    max_zoom = payload.get("max_zoom", 19)
    if isinstance(max_zoom, bool) or not isinstance(max_zoom, int) or not 1 <= max_zoom <= 22:
        raise InvalidBasemap("invalid_max_zoom")
    return {"id": bid, "name": name, "kind": "raster", "default": False, "max_zoom": max_zoom, "url": url,
            "attribution": attribution, "licence": str(payload.get("licence", "")).strip()[:80],
            "usage": str(payload.get("usage", "")).strip()[:160], "custom": True}

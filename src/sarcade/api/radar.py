# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Rain radar of Météo-France as map tiles (lot 29: card « Météo : radar de
pluie, aussi en calque sur la carte »)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from sarcade.weather.radar import radar

router = APIRouter(prefix="/api/v0.1")


@router.get("/weather/radar")
def radar_status():
    """Frames of the last hour, legend, source; asking it keeps the radar
    fetched for 15 minutes (nothing is fetched while nobody shows it)."""
    radar.watch()
    return radar.status()


@router.get("/weather/radar/{frame_id}/{z}/{x}/{y}.png")
def radar_tile(frame_id: str, z: int, x: int, y: int):
    if not (0 <= z <= 20 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(status_code=404, detail="tile_out_of_range")
    png = radar.tile(frame_id, z, x, y)
    if png is None:
        raise HTTPException(status_code=404, detail="frame_gone")
    # A frame never changes: kept by the devices for the hour it lives.
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3900, immutable"})

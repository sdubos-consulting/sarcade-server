"""Readable location of a point: nearest commune (offline) and road (online)."""
from fastapi import APIRouter, Query

from sarcade import places

router = APIRouter(prefix="/api/v0.1")


@router.get("/places/describe")
async def describe_place(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                         sectors: int = Query(8), online: bool = Query(True)):
    """``label`` = "1,2 km au sud-ouest de Chavenay" (``label_en`` in English),
    null beyond 60 km of any French commune; ``road`` = nearest street from
    the IGN Géoplateforme, null when unknown or offline; ``altitude_m`` =
    ground altitude from the IGN altimetry (null when unknown or offline);
    ``department`` = department code of the commune."""
    return await places.describe(lat, lon, 16 if sectors == 16 else 8, online)

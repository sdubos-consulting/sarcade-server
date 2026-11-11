"""Address search for the map search bar (Base Adresse Nationale)."""
from fastapi import APIRouter, HTTPException, Query

from sarcade import geocode

router = APIRouter(prefix="/api/v0.1")


@router.get("/geocode")
async def search_address(q: str = Query(min_length=3, max_length=200), lat: float | None = Query(None, ge=-90, le=90),
                         lon: float | None = Query(None, ge=-180, le=180), limit: int = Query(8, ge=1, le=10)):
    """Addresses and places matching ``q``, nearest to ``lat``/``lon`` first.
    503 when the address service cannot be reached (or is switched off):
    the clients still search their local data."""
    try:
        return {"results": await geocode.search(q, lat, lon, limit)}
    except geocode.GeocoderUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"geocoder_unavailable: {exc}")

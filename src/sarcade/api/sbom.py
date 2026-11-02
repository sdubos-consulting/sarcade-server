"""Inventory of the components of the running image (SBOM, CycloneDX JSON).

Written at the construction of the image (docker/make_sbom.py) and served at
``/sarcade-sbom.cdx.json``, next to ``/sarcade-web.sha256``: same exposure as
the web client (no SARCADE account; behind Cloudflare Access on the public
hosts). 404 when the build could not produce it.
"""
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

DEFAULT_SBOM_FILE = "/app/sbom/sarcade-image.json"
MEDIA_TYPE = "application/vnd.cyclonedx+json"

router = APIRouter()


def sbom_file() -> Path:
    return Path(os.getenv("SARCADE_SBOM_FILE") or DEFAULT_SBOM_FILE)


@router.get("/sarcade-sbom.cdx.json", include_in_schema=False)
def get_sbom():
    path = sbom_file()
    if not path.is_file():
        return JSONResponse({"detail": "sbom_not_available"}, status_code=404,
                            headers={"Cache-Control": "no-store"})
    # Changes with each new image: always re-checked (ETag, 304).
    return FileResponse(path, media_type=MEDIA_TYPE, headers={
        "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
    })

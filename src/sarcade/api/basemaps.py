"""Base maps: catalog, custom base maps, offline MBTiles packages."""
from __future__ import annotations

from datetime import UTC, datetime
import os
import re
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.basemaps import catalog, mbtiles
from sarcade.db.models import CustomBasemapRow

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
MAX_PACKAGE_BYTES = int(os.getenv("SARCADE_MAX_TILE_PACKAGE_BYTES", str(4 * 1024 ** 3)))
MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
         "pbf": "application/x-protobuf"}


def tiles_root() -> Path:
    return Path(os.getenv("SARCADE_TILES_ROOT", "/var/lib/sarcade/tiles"))


def image_tiles_root() -> Path:
    """Packages built on the i9 and baked into the image (card « Cartes hors
    connexion sur le téléphone », 9 Nov 2026): the same on the NAS and on
    a mobile server, without Internet. A package placed by the
    administrator (volume) wins."""
    return Path(os.getenv("SARCADE_TILES_IMAGE_ROOT", "/app/tiles"))


_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _path_in(root_dir: Path, basemap_id: str) -> Path:
    if not _SAFE_ID.match(basemap_id):
        raise HTTPException(status_code=404, detail="basemap_not_found")
    root = os.path.realpath(root_dir)
    path = os.path.realpath(os.path.join(root, f"{basemap_id}.mbtiles"))
    if not path.startswith(root + os.sep):
        raise HTTPException(status_code=404, detail="basemap_not_found")
    return Path(path)


def volume_path(basemap_id: str) -> Path:
    """Where an uploaded package is written."""
    return _path_in(tiles_root(), basemap_id)


def package_path(basemap_id: str) -> Path:
    """Package read: the one of the volume, else the one of the image."""
    placed = volume_path(basemap_id)
    if placed.is_file():
        return placed
    baked = _path_in(image_tiles_root(), basemap_id)
    return baked if baked.is_file() else placed


def _known(db: Session) -> dict[str, dict]:
    items = {b["id"]: dict(b, custom=False) for b in catalog.BUILT_IN}
    for row in db.scalars(select(CustomBasemapRow).order_by(CustomBasemapRow.created_at)):
        items[row.id] = dict(row.data)
    return items


def _with_package(item: dict) -> dict:
    path = package_path(item["id"])
    package = None
    if path.is_file():
        try:
            package = mbtiles.metadata(path)
        except mbtiles.InvalidPackage:
            package = None
    return dict(item, offline_package=package, available_offline=package is not None or item["id"] == "osm")


@router.get("/basemaps")
def list_basemaps(db: Session = Depends(get_db)):
    """Catalog shown in the selector, with the offline packages available on
    this server."""
    return [_with_package(i) for i in _known(db).values()]


@router.post("/basemaps", status_code=201)
def add_custom_basemap(payload: dict, db: Session = Depends(get_db)):
    actor = str(payload.get("actor_id", "")).strip()
    if not 1 <= len(actor) <= 64:
        raise HTTPException(status_code=422, detail="invalid_actor_id")
    try:
        data = catalog.validate_custom(payload)
    except catalog.InvalidBasemap as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if db.get(CustomBasemapRow, data["id"]) is not None:
        raise HTTPException(status_code=409, detail="basemap_exists")
    db.add(CustomBasemapRow(id=data["id"], data=data, created_at=datetime.now(UTC), updated_by=actor))
    db.commit()
    return _with_package(data)


@router.delete("/basemaps/{basemap_id}", status_code=204)
def delete_custom_basemap(basemap_id: str, db: Session = Depends(get_db)):
    row = db.get(CustomBasemapRow, basemap_id)
    if row is None:
        raise HTTPException(status_code=404, detail="custom_basemap_not_found")
    db.delete(row)
    db.commit()
    package_path(basemap_id).unlink(missing_ok=True)


@router.put("/basemaps/{basemap_id}/package")
async def upload_package(basemap_id: str, actor_id: str = Form(...), file: UploadFile = File(...),
                         db: Session = Depends(get_db)):
    """Offline package (MBTiles) of the department, produced for that use."""
    if basemap_id not in _known(db):
        raise HTTPException(status_code=404, detail="basemap_not_found")
    root = tiles_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".upload-{uuid.uuid4()}"
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_PACKAGE_BYTES:
                    raise HTTPException(status_code=413, detail="package_too_large")
                out.write(chunk)
        try:
            meta = mbtiles.metadata(tmp)
        except mbtiles.InvalidPackage:
            raise HTTPException(status_code=422, detail="not_mbtiles")
        shutil.move(tmp, volume_path(basemap_id))
    finally:
        tmp.unlink(missing_ok=True)
    return dict(meta, basemap_id=basemap_id, uploaded_by=actor_id)


@router.get("/basemaps/{basemap_id}/package")
def download_package(basemap_id: str):
    path = package_path(basemap_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no_offline_package")
    return FileResponse(path, media_type="application/x-sqlite3",
                        filename=f"sarcade-{basemap_id}.mbtiles")


@router.get("/basemaps/{basemap_id}/tiles/{z}/{x}/{y}")
def offline_tile(basemap_id: str, z: int, x: int, y: int):
    """Tile served from the offline package, on the local network."""
    path = package_path(basemap_id)
    if not path.is_file() or not (0 <= z <= 22 and 0 <= x < (1 << z) and 0 <= y < (1 << z)):
        raise HTTPException(status_code=404, detail="tile_not_found")
    data = mbtiles.tile(path, z, x, y)
    if data is None:
        raise HTTPException(status_code=404, detail="tile_not_found")
    fmt = mbtiles.metadata(path).get("format", "png")
    return Response(data, media_type=MEDIA.get(fmt, "application/octet-stream"),
                    headers={"Cache-Control": "public, max-age=86400"})

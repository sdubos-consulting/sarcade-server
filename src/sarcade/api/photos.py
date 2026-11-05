# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Profile photo in place of the initials (lot 29, group 7).

``PUT`` by the device itself, ``DELETE`` by itself or by a PCO or an
administrator: the operator is then told on his device (``removed_by``)."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from sarcade import roles
from sarcade.accounts.caller import caller_of
from sarcade.db.models import EventRow
from sarcade.profile import photo
from sarcade.realtime.manager import manager
from sarcade.security import journal

from . import roles as roles_api
from .deps import get_db

router = APIRouter()
_BASE = "/api/v0.1/events/{event_id}"


def _event(db: Session, event_id: str) -> EventRow:
    event = db.get(EventRow, event_id)
    if event is None:
        raise HTTPException(404, "événement inconnu")
    return event


def _is_self(request: Request, db: Session, device_id: str) -> bool:
    """The device itself; without identity (device without account) only
    while the transition lasts, as for the other rights."""
    caller = caller_of(request)
    if caller.device_id is not None:
        return caller.device_id == device_id
    if caller.email is None:
        from sarcade.directory import transition
        return not roles.directory_ready(db) or transition.active()
    return False


def _actor(request: Request) -> str | None:
    caller = caller_of(request)
    return caller.email or caller.device_id


def _notice(device_id: str, row, removed_by: str | None = None) -> dict:
    data = {"device_id": device_id,
            "updated_at": row.updated_at.isoformat().replace("+00:00", "Z") if row is not None else None}
    if removed_by:
        data["removed_by"] = removed_by
    return {"type": "photo.updated", "data": data}


@router.get(_BASE + "/photos")
def list_photos(event_id: str, db: Session = Depends(get_db)):
    """Who has a photo and since when; the images are fetched one by one."""
    _event(db, event_id)
    return photo.of_event(db, event_id)


@router.get(_BASE + "/devices/{device_id}/photo")
def get_photo(event_id: str, device_id: str, db: Session = Depends(get_db)):
    row = photo.get(db, event_id, device_id)
    if row is None:
        raise HTTPException(404, "pas de photo")
    return Response(row.data, media_type=row.mime, headers={"Cache-Control": "private, max-age=86400"})


@router.put(_BASE + "/devices/{device_id}/photo")
async def put_photo(event_id: str, device_id: str, request: Request, db: Session = Depends(get_db)):
    """Body: the image itself (JPEG, PNG or WebP, at most 64 KB)."""
    _event(db, event_id)
    if not _is_self(request, db, device_id):
        raise HTTPException(403, "seul l'opérateur change sa photo")
    data = await request.body()
    try:
        row = photo.store(db, event_id, device_id, data, datetime.now(UTC), _actor(request))
    except ValueError as exc:
        if str(exc) == "size":
            raise HTTPException(413, "photo trop lourde : 64 Ko au plus")
        raise HTTPException(415, "photo JPEG, PNG ou WebP attendue")
    db.commit()
    await manager.broadcast(event_id, _notice(device_id, row))
    return {"device_id": device_id, "updated_at": row.updated_at.isoformat().replace("+00:00", "Z")}


@router.delete(_BASE + "/devices/{device_id}/photo")
async def delete_photo(event_id: str, device_id: str, request: Request, db: Session = Depends(get_db)):
    """« Retirer la photo »: by the operator, or by a PCO or an administrator."""
    _event(db, event_id)
    removed_by = None
    if not _is_self(request, db, device_id):
        roles_api.require_pco(request, db)
        removed_by = "pco"
    if not photo.remove(db, event_id, device_id):
        raise HTTPException(404, "pas de photo")
    if removed_by:
        journal.record(db, "rights", "photo_removed", "success", actor=_actor(request),
                       device_id=device_id, event_id=event_id)
    db.commit()
    await manager.broadcast(event_id, _notice(device_id, None, removed_by))
    return {"device_id": device_id, "removed": True}

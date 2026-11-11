# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Subfolders of the event files (« Événement » tab of the Files screen).
Same rights as the upload of an event file: any operator of the event.
Every change is written in the logbook (kind « file »)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import EventRow, LogbookRow, SharedFileRow
from sarcade.files import folders
from sarcade.realtime.manager import manager

from .deps import get_db
from .roles import checked_email

router = APIRouter(prefix="/api/v0.1/events/{event_id}/files")
SCOPE = "event"


class FolderIn(BaseModel):
    path: str = Field(max_length=400)
    actor_id: str | None = Field(default=None, max_length=64)


class FolderMove(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from", max_length=400)
    to: str = Field(max_length=400)
    actor_id: str | None = Field(default=None, max_length=64)


class FilePatch(BaseModel):
    folder: str | None = Field(default=None, max_length=400)
    name: str | None = Field(default=None, max_length=400)
    actor_id: str | None = Field(default=None, max_length=64)


def _files(db: Session, event_id: str) -> list[SharedFileRow]:
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="unknown_event")
    return list(db.scalars(select(SharedFileRow).where(SharedFileRow.event_id == event_id)).all())


def listing(db: Session, event_id: str, files) -> list[str]:
    return folders.all_paths(db, SCOPE, event_id, files)


def _actor(request: Request, actor_id: str | None) -> str:
    return (actor_id or checked_email(request) or "opérateur")[:64]


def _log(db: Session, event_id: str, actor: str, summary: str, object_id: str | None = None) -> None:
    db.add(LogbookRow(event_id=event_id, kind="file", object_id=object_id, actor_id=actor,
                      summary=summary, time=datetime.now(UTC)))


def _fail(exc: folders.FolderError):
    raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


async def _done(db: Session, event_id: str, files) -> dict:
    db.commit()
    result = {"folders": listing(db, event_id, files)}
    await manager.broadcast(event_id, {"type": "file.folders.updated", "data": result})
    return result


@router.get("/folders")
def list_folders(event_id: str, db: Session = Depends(get_db)):
    return {"folders": listing(db, event_id, _files(db, event_id))}


@router.post("/folders", status_code=201)
async def create_folder(event_id: str, body: FolderIn, request: Request, db: Session = Depends(get_db)):
    files = _files(db, event_id)
    try:
        path = folders.create(db, SCOPE, event_id, files, body.path)
    except folders.FolderError as exc:
        _fail(exc)
    _log(db, event_id, _actor(request, body.actor_id), f"Dossier créé : {path}")
    return {"path": path, **await _done(db, event_id, files)}


@router.patch("/folders")
async def move_folder(event_id: str, body: FolderMove, request: Request, db: Session = Depends(get_db)):
    files = _files(db, event_id)
    try:
        src, dst = folders.rename(db, SCOPE, event_id, files, body.from_, body.to)
    except folders.FolderError as exc:
        _fail(exc)
    if src != dst:
        _log(db, event_id, _actor(request, body.actor_id), f"Dossier renommé : {src} → {dst}")
    return {"from": src, "to": dst, **await _done(db, event_id, files)}


@router.delete("/folders", status_code=204)
async def delete_folder(event_id: str, request: Request, path: str = Query(max_length=400),
                        actor_id: str | None = Query(None, max_length=64), db: Session = Depends(get_db)):
    files = _files(db, event_id)
    try:
        path = folders.delete(db, SCOPE, event_id, files, path)
    except folders.FolderError as exc:
        _fail(exc)
    _log(db, event_id, _actor(request, actor_id), f"Dossier supprimé : {path}")
    await _done(db, event_id, files)


@router.patch("/{file_id}")
async def move_file(event_id: str, file_id: str, body: FilePatch, request: Request, db: Session = Depends(get_db)):
    """Moves (``folder``) and/or renames (``name``) a file. Names may repeat,
    as on upload."""
    from .app import file_dict
    row = db.get(SharedFileRow, file_id)
    if row is None or row.event_id != event_id:
        raise HTTPException(status_code=404, detail="file_not_found")
    try:
        folder = folders.clean_path(body.folder) if body.folder is not None else row.folder or ""
        name = folders.clean_name(body.name) if body.name is not None else row.name
    except folders.FolderError as exc:
        _fail(exc)
    actor = _actor(request, body.actor_id)
    if name != row.name:
        _log(db, event_id, actor, f"Fichier renommé : {row.name} → {name}", row.id)
    if folder != (row.folder or ""):
        _log(db, event_id, actor, f"Fichier déplacé : {name} → {folder or 'racine'}", row.id)
    row.name, row.folder = name, folder
    folders.ensure(db, SCOPE, event_id, folder)
    db.commit()
    data = file_dict(row)
    await manager.broadcast(event_id, {"type": "file.updated", "data": data})
    return data

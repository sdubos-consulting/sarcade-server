"""APRS API: callsign groups, lines pushed by the Gateway, link status."""
from __future__ import annotations

from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.domain import callsigns as calls
from .aprs.links import handle_line
from sarcade import modules
from .aprs.service import event_callsigns, runtime
from sarcade.db.models import AprsGroupRow, AprsStationClassRow, EventRow
from sarcade.realtime.manager import manager

from sarcade.api.deps import get_db
from sarcade.api.roles import require_pco

router = APIRouter(prefix="/api/v0.1")


class GroupIn(BaseModel):
    actor_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    callsigns: list[str] = Field(default_factory=list)


class FramesIn(BaseModel):
    via: str = Field(pattern="^(rf|is)$")
    lines: list[str] = Field(max_length=500)


def group_dict(row: AprsGroupRow) -> dict:
    return {"id": row.id, "name": row.name, "callsigns": row.callsigns or [],
            "created_at": row.created_at.astimezone(UTC), "updated_at": row.updated_at.astimezone(UTC),
            "updated_by": row.updated_by}


def _clean(payload: GroupIn) -> list[str]:
    try:
        return calls.normalize_list(payload.callsigns)
    except calls.InvalidCallsign as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _check_unique_name(db: Session, name: str, own_id: str | None = None) -> str:
    """Two groups of the same name are refused (lot 5, 30 Oct 2026: two
    « ADRASEC 78 » groups, one inside the other); case and spaces ignored."""
    clean = " ".join(name.split())
    key = clean.casefold()
    for row in db.scalars(select(AprsGroupRow)):
        if row.id != own_id and " ".join((row.name or "").split()).casefold() == key:
            raise HTTPException(status_code=409, detail="aprs_group_name_taken")
    return clean


@router.get("/aprs/groups")
def list_groups(db: Session = Depends(get_db)):
    return [group_dict(r) for r in db.scalars(select(AprsGroupRow).order_by(AprsGroupRow.name))]


@router.post("/aprs/groups", status_code=201)
def create_group(payload: GroupIn, db: Session = Depends(get_db), _pco=Depends(require_pco)):
    now = datetime.now(UTC)
    name = _check_unique_name(db, payload.name)
    row = AprsGroupRow(id=str(uuid.uuid4()), name=name, callsigns=_clean(payload),
                       created_at=now, updated_at=now, updated_by=payload.actor_id)
    db.add(row)
    db.commit()
    return group_dict(row)


@router.put("/aprs/groups/{group_id}")
def update_group(group_id: str, payload: GroupIn, db: Session = Depends(get_db), _pco=Depends(require_pco)):
    row = db.get(AprsGroupRow, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="aprs_group_not_found")
    row.name, row.callsigns = _check_unique_name(db, payload.name, group_id), _clean(payload)
    row.updated_at, row.updated_by = datetime.now(UTC), payload.actor_id
    db.commit()
    return group_dict(row)


@router.delete("/aprs/groups/{group_id}", status_code=204)
def delete_group(group_id: str, db: Session = Depends(get_db), _pco=Depends(require_pco)):
    row = db.get(AprsGroupRow, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="aprs_group_not_found")
    db.delete(row)
    db.commit()


@router.get("/events/{event_id}/aprs/callsigns")
def followed_callsigns(event_id: str, db: Session = Depends(get_db)):
    event = db.get(EventRow, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    return {"callsigns": event_callsigns(db, event)}


class StationClassIn(BaseModel):
    actor_id: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern="^(fixed|mobile|auto)$")


def _event(db: Session, event_id: str) -> EventRow:
    event = db.get(EventRow, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    return event


@router.get("/events/{event_id}/aprs/classes")
def station_classes(event_id: str, db: Session = Depends(get_db)):
    """Stations classified by hand (fixed or mobile), shared by the event."""
    _event(db, event_id)
    rows = db.scalars(select(AprsStationClassRow).where(AprsStationClassRow.event_id == event_id))
    return {"classes": {r.device_id: r.kind for r in rows}}


def set_station_class(db: Session, event_id: str, device_id: str, payload: StationClassIn) -> dict:
    """Any participant of the event may classify (« auto » removes the
    choice and lets the application decide again)."""
    _event(db, event_id)
    row = db.get(AprsStationClassRow, (event_id, device_id))
    if payload.kind == "auto":
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(AprsStationClassRow(event_id=event_id, device_id=device_id, kind=payload.kind,
                                   updated_by=payload.actor_id, updated_at=datetime.now(UTC)))
    else:
        row.kind, row.updated_by, row.updated_at = payload.kind, payload.actor_id, datetime.now(UTC)
    db.commit()
    return {"device_id": device_id, "kind": payload.kind, "updated_by": payload.actor_id}


@router.put("/events/{event_id}/aprs/classes/{device_id}")
async def put_station_class(event_id: str, device_id: str, payload: StationClassIn, db: Session = Depends(get_db)):
    if not device_id or len(device_id) > 64:
        raise HTTPException(status_code=422, detail="invalid_device_id")
    event = _event(db, event_id)
    if not modules.enabled(event.settings, "adrasec"):
        raise HTTPException(status_code=409, detail="module_inactive:adrasec")
    data = set_station_class(db, event_id, device_id, payload)
    await manager.broadcast(event_id, {"type": "aprs.class.updated", "data": data})
    return data


@router.post("/aprs/frames")
async def push_frames(payload: FramesIn):
    """Decoded TNC2 lines pushed by the Gateway (for instance from a radio
    modem without KISS TCP). Only selected stations are stored."""
    stored = 0
    for line in payload.lines:
        stored += await handle_line(line, payload.via)
    return {"received": len(payload.lines), "stored": stored}


@router.get("/aprs/status")
def status():
    def link(s):
        return {"enabled": s.enabled, "connected": s.connected, "detail": s.detail,
                "last_packet_at": s.last_packet_at, "packets": s.packets}
    return {"aprs_is": link(runtime.aprs_is), "radio": link(runtime.radio),
            "filter": runtime.filter, "stored_positions": runtime.stored}

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""PTT natif: channels of a device and access to their voice rooms (lot 29)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade import ptt
from sarcade.accounts.caller import caller_of
from sarcade.db.models import CommGroupRow, EventRow

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")


def _channels(db: Session, event_id: str, device_id: str) -> list[dict]:
    rows = db.scalars(select(CommGroupRow).where(CommGroupRow.event_id == event_id, CommGroupRow.deleted_at.is_(None))
                      .order_by(CommGroupRow.created_at, CommGroupRow.id)).all()
    return ptt.channels([{**(r.data or {}), "id": r.id} for r in rows], device_id)


@router.get("/events/{event_id}/ptt")
def ptt_channels(event_id: str, device_id: str, db: Session = Depends(get_db)):
    """« available » false while LiveKit is not set on the server."""
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    cfg = ptt.config()
    return {"available": cfg is not None, "channels": _channels(db, event_id, device_id) if cfg else []}


class TokenIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=80)


@router.post("/events/{event_id}/ptt/token")
def ptt_token(event_id: str, body: TokenIn, request: Request, db: Session = Depends(get_db)):
    cfg = ptt.config()
    # An enrolled device speaks only under its own name.
    caller = caller_of(request)
    if caller.device_id is not None and caller.device_id != body.device_id:
        raise HTTPException(status_code=403, detail="ptt_other_device")
    if cfg is None:
        raise HTTPException(status_code=503, detail="ptt_not_configured")
    if db.get(EventRow, event_id) is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    channel = next((c for c in _channels(db, event_id, body.device_id) if c["id"] == body.channel), None)
    if channel is None:
        raise HTTPException(status_code=403, detail="ptt_channel_refused")
    room = ptt.room_name(event_id, channel["id"])
    return {"url": cfg["url"], "room": room, "can_talk": channel["can_talk"],
            "token": ptt.token(cfg, identity=body.device_id, name=body.name or body.device_id, room=room, can_talk=channel["can_talk"])}

"""Wake-up of the devices (note « Réveil des appareils »): registration of
the tokens, test button, status for the PCO."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sarcade.db.models import EventRow
from sarcade.wakeup import service

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")


class WakeTokenIn(BaseModel):
    platform: str = Field(pattern="^(fcm|fcm_ios|webpush|apns)$")
    token: str = Field(min_length=1, max_length=4096)


def _event(db: Session, event_id: str) -> EventRow:
    event = db.get(EventRow, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="unknown_event")
    return event


@router.get("/wakeup/config")
def wakeup_config(db: Session = Depends(get_db)):
    """Public key for Web Push and the platforms the server can wake."""
    return service.config(db)


@router.put("/events/{event_id}/devices/{device_id}/wakeup")
def register_token(event_id: str, device_id: str, body: WakeTokenIn, db: Session = Depends(get_db)):
    _event(db, event_id)
    try:
        service.register(db, event_id, device_id[:64], body.platform, body.token, datetime.now(UTC))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"registered": True}


@router.delete("/events/{event_id}/devices/{device_id}/wakeup")
def unregister_token(event_id: str, device_id: str, platform: str | None = None, db: Session = Depends(get_db)):
    return {"removed": service.unregister(db, event_id, device_id, platform)}


@router.post("/events/{event_id}/devices/{device_id}/wakeup/test")
async def test_wakeup(event_id: str, device_id: str, db: Session = Depends(get_db)):
    """« Tester le réveil »: an immediate signal to this device only."""
    _event(db, event_id)
    return await service.wake(event_id, [device_id], "immediate", kind="test", force=True)


@router.get("/events/{event_id}/wakeup/devices")
def wakeable_devices(event_id: str, db: Session = Depends(get_db)):
    """Devices that can be woken (PCO: « réveil possible »)."""
    return {"devices": sorted(service.wakeable(db, event_id))}

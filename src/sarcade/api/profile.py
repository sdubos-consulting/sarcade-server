"""« Mes réglages »: personal settings of the person signed in through
Cloudflare Access. Without a checked email (local network, no Access) the
settings are not available and the applications keep them on the device."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from sarcade.accounts.caller import caller_of
from sarcade.db.models import UserPreferencesRow
from sarcade.profile.preferences import InvalidPreferences, apply_patch, normal_email

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")


def _email(request: Request) -> str | None:
    # Account of an enrolled device (lot 26) or Cloudflare Access.
    email = caller_of(request).email
    return normal_email(email) if email else None


@router.get("/me/preferences")
def read_preferences(request: Request, db: Session = Depends(get_db)):
    email = _email(request)
    if email is None:
        return {"available": False, "preferences": {}}
    row = db.get(UserPreferencesRow, email)
    return {"available": True, "email": email, "preferences": (row.data if row else None) or {},
            "updated_at": row.updated_at if row else None}


@router.patch("/me/preferences")
def change_preferences(request: Request, patch: dict, db: Session = Depends(get_db)):
    email = _email(request)
    if email is None:
        raise HTTPException(status_code=409, detail="no_identity")
    row = db.get(UserPreferencesRow, email)
    try:
        data = apply_patch(row.data if row else None, patch)
    except InvalidPreferences as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    now = datetime.now(UTC)
    if row is None:
        row = UserPreferencesRow(email=email, data=data, updated_at=now)
        db.add(row)
    else:
        row.data = data
        row.updated_at = now
    db.commit()
    return {"available": True, "email": email, "preferences": data, "updated_at": now}

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Default signals of the bearing tool (frequencies offered when taking a
bearing), edited by the administrator, kept in the server settings."""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sarcade.security import journal
from sarcade.updates.service import get_value, set_value

from sarcade.api.deps import get_db
from sarcade.api.roles import require_admin_email

router = APIRouter(prefix="/api/v0.1")
KEY = "signal_defaults"
MAX_SIGNALS = 20
MAX_LABEL = 60
INITIAL = [
    {"label": "Balise de détresse réelle", "frequency_mhz": 121.5, "exercise": False, "hidden": False},
    {"label": "Balise d’exercice", "frequency_mhz": 121.375, "exercise": True, "hidden": False},
    {"label": "Cospas-Sarsat", "frequency_mhz": 406.0, "exercise": False, "hidden": False},
]


class InvalidSignals(ValueError):
    pass


def clean(signals) -> list[dict]:
    """Validated list, order preserved."""
    if not isinstance(signals, list) or len(signals) > MAX_SIGNALS:
        raise InvalidSignals("invalid_signals")
    out = []
    for s in signals:
        if not isinstance(s, dict):
            raise InvalidSignals("invalid_signal")
        label = s.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > MAX_LABEL:
            raise InvalidSignals("invalid_label")
        f = s.get("frequency_mhz")
        if isinstance(f, bool) or not isinstance(f, (int, float)) or not math.isfinite(f) or not 1 <= f <= 10_000:
            raise InvalidSignals("invalid_frequency")
        flags = {}
        for key in ("exercise", "hidden"):
            v = s.get(key, False)
            if not isinstance(v, bool):
                raise InvalidSignals(f"invalid_{key}")
            flags[key] = v
        out.append({"label": label.strip(), "frequency_mhz": float(f), **flags})
    return out


def stored(db: Session) -> list[dict]:
    value = get_value(db, KEY, None)
    try:
        return clean(value["signals"]) if isinstance(value, dict) else [dict(s) for s in INITIAL]
    except (InvalidSignals, KeyError):
        return [dict(s) for s in INITIAL]


class SignalsIn(BaseModel):
    signals: list


@router.get("/signals/defaults")
def get_defaults(db: Session = Depends(get_db)):
    return {"signals": stored(db)}


@router.put("/signals/defaults")
def put_defaults(body: SignalsIn, admin: str = Depends(require_admin_email), db: Session = Depends(get_db)):
    try:
        signals = clean(body.signals)
    except InvalidSignals as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    set_value(db, KEY, {"signals": signals})
    journal.record(db, "admin", "signal_defaults_changed", "success", details={"by": admin, "count": len(signals)})
    db.commit()
    return {"signals": signals}

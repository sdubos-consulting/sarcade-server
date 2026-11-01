"""« Paramètres › Diagnostic », server block (lot 5, 30 Oct 2026): PCO and
administrators only (``require_pco``)."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from sarcade import diagnostics as diag
from sarcade.access.verify import verifier
from sarcade.modules import hooks as module_hooks
from sarcade.db.models import ServerUpdateRow, WeatherCacheRow
from sarcade.realtime.manager import manager
from sarcade.updates import service as updates
from sarcade.wakeup import fcm, service as wakeup

from .deps import get_db
from .roles import checked_email, require_pco

router = APIRouter(prefix="/api/v0.1")

WEATHER_STALE_S = 3 * 3600


def _link(s) -> str:
    if not s.enabled:
        return "off"
    return "green" if s.connected else "red"


def components(db: Session, now: datetime) -> tuple[dict[str, str], dict]:
    detail: dict = {}
    try:
        db.execute(text("SELECT 1"))
        base = "green"
    except Exception as exc:  # noqa: BLE001 - shown, never raised
        base, detail["database"] = "red", type(exc).__name__
    clients = sum(len(v) for v in manager._connections.values())  # noqa: SLF001 - read only
    detail["realtime_clients"] = clients
    # Tunnel: a request through Cloudflare in the last 15 minutes (cf-ray).
    cf = diag.last_cloudflare_at
    access_on = verifier().config.mode != "off"
    if cf is None:
        tunnel = "orange" if access_on else "off"
    else:
        tunnel = "green" if now.timestamp() - cf < 900 else "orange"
    detail["tunnel_last_seen"] = diag.iso(cf)
    android = fcm.load_account() is not None
    detail["wakeup"] = {"android": android, "iphone": android, "webpush": True,
                        "sent": wakeup.stats.sent, "failed": wakeup.stats.failed,
                        "last_error": wakeup.stats.last_error}
    wake = "green" if android and not wakeup.stats.last_error else ("orange" if android or wakeup.stats.sent else "red")
    links = [_link(link) for link in module_hooks.links()]
    aprs = "off" if all(v == "off" for v in links) else ("green" if "green" in links else "red")
    if aprs == "green" and "red" in links:
        aprs = "orange"
    weather = "off"
    try:
        last = db.scalar(select(func.max(WeatherCacheRow.fetched_at)))
    except Exception:  # noqa: BLE001
        last = None
    if last is not None:
        last = last if last.tzinfo else last.replace(tzinfo=UTC)
        age = (now - last).total_seconds()
        weather = "green" if age < WEATHER_STALE_S else "orange"
        detail["weather_fetched_at"] = last.isoformat().replace("+00:00", "Z")
    elif os.getenv("SARCADE_WEATHER", "1").lower() in ("1", "true", "yes"):
        weather = "orange"
    return ({"database": base, "realtime": "green", "tunnel": tunnel, "wakeup": wake, "aprs": aprs,
             "weather": weather}, detail)


def _last_update(db: Session) -> dict | None:
    try:
        row = db.scalars(select(ServerUpdateRow).order_by(ServerUpdateRow.at.desc()).limit(1)).first()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
    return {"at": at.isoformat().replace("+00:00", "Z"), "version": row.version, "status": row.status,
            "detail": row.detail}


def _last_backup(db: Session) -> dict | None:
    """The updater makes a backup before each installation: last report
    whose detail names it (« backup … »)."""
    try:
        rows = db.scalars(select(ServerUpdateRow).where(ServerUpdateRow.status == "succeeded")
                          .order_by(ServerUpdateRow.at.desc()).limit(20)).all()
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        if row.detail and "backup " in row.detail:
            at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
            return {"at": at.isoformat().replace("+00:00", "Z"), "file": row.detail.split("backup ", 1)[1].strip()}
    return None


@router.get("/diagnostics/client")
def client_diagnostics(request: Request):
    """Bloc « Cet appareil » and « Liaisons » (everyone): the public address
    of the caller as the server sees it (Cloudflare ``cf-connecting-ip``,
    else the first ``x-forwarded-for``, else the socket), whether the request
    came through Cloudflare, and the email checked by Access."""
    h = request.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else None)
    return {"ip": ip, "via_cloudflare": bool(h.get("cf-ray")), "email": checked_email(request),
            "version": updates.installed_version(), "at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}


@router.get("/diagnostics/server")
async def server_diagnostics(request: Request, db: Session = Depends(get_db), _pco=Depends(require_pco)):
    # Without a checked email the previous behaviour is kept by require_pco;
    # on a public host (Access on) an unknown caller is refused.
    if checked_email(request) is None and verifier().config.applies_to(request.headers.get("host")):
        raise HTTPException(status_code=403, detail="pco_required")
    now = datetime.now(UTC)
    comps, detail = components(db, now)
    res = await asyncio.to_thread(diag.resources, os.getenv("SARCADE_FILE_ROOT", "/var/lib/sarcade/files")
                                  if os.path.isdir(os.getenv("SARCADE_FILE_ROOT", "/var/lib/sarcade/files")) else "/")
    return {"status": diag.overall(comps), "version": updates.installed_version(),
            "started_at": diag.iso(diag.STARTED), "uptime_s": diag.uptime_s(),
            "components": comps, "detail": detail, "resources": res, "alert_pct": diag.ALERT_PCT,
            "last_update": _last_update(db), "last_backup": _last_backup(db),
            "update_status": updates.get_value(db, "update_status"), "at": now.isoformat().replace("+00:00", "Z")}

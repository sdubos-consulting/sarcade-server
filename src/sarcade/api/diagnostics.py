"""« Paramètres › Diagnostic », server block (lot 5, 30 Oct 2026).

Lot 28 (card « Diagnostic : trois niveaux de vue selon les droits, et test
de Brevo », decided on 4 Nov 2026, 7 h 19): the server computes the level
from the rights of lot 26 and sends only what the level allows:

- ``user``: general state, version of the server;
- ``organizer`` (PCO, organiser of events): in addition the services useful
  to the event (APRS, weather, itineraries, notifications) and the number
  of devices connected;
- ``admin`` (administrator or auditor of the server): everything
  (processor, memory, disk, addresses, backup, versions, test of Brevo);
- ``transition``: device without account during the transition period,
  the view of before (choice by default).
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from sarcade import deadlines, diagnostics as diag, roles, version as server_version
from sarcade.access.verify import verifier
from sarcade.accounts import mailer
from sarcade.accounts.caller import caller_of
from sarcade.modules import hooks as module_hooks
from sarcade.db.models import ServerUpdateRow, WeatherCacheRow
from sarcade.realtime.manager import manager
from sarcade.security import siem
from sarcade.updates import service as updates
from sarcade.wakeup import fcm, service as wakeup

from .authz import ADMIN_TOKEN_ACTOR, admin_token_used, client_ip, require_server_admin
from .deps import get_db

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
    return {"ip": ip, "via_cloudflare": bool(h.get("cf-ray")), "email": caller_of(request).email,
            "version": updates.installed_version(), "at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}


def level_of(request: Request, db: Session) -> tuple[str, str | None]:
    """(level, email) of the caller, from the rights of lot 26."""
    if admin_token_used(request):
        return "admin", None
    email = caller_of(request).email
    ready = roles.directory_ready(db)
    if email is None:
        # Device without account: the view of before during the transition
        # (never on a host protected by Cloudflare Access, which names everyone).
        from sarcade.directory import transition
        if not verifier().config.applies_to(request.headers.get("host")) and (not ready or transition.active()):
            return "transition", None
        return "user", None
    mine = roles.roles_of(db, email)
    if "admin" in mine:
        return "admin", email
    if ready:
        from sarcade.directory import rights
        caller = caller_of(request)
        if any(g.right == "auditor" and g.scope_group_id is None for g in rights.of(db, caller.user_id, email).grants):
            return "admin", email
    return ("organizer" if "pco" in mine else "user"), email


async def _routing_ok() -> bool:
    import httpx
    from sarcade.routing import valhalla
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            return (await client.get(f"{valhalla.valhalla_url()}/status")).status_code == 200
    except httpx.HTTPError:
        return False


def addresses() -> list[str]:
    """Addresses of the server (container) on its networks."""
    try:
        return sorted({a for a in socket.gethostbyname_ex(socket.gethostname())[2] if not a.startswith("127.")})
    except OSError:
        return []


PROBE_TTL_S = 60
_probe: dict = {"key": None, "at": 0.0, "result": None}


def _probe_smtp(cfg) -> dict:
    """Connection with identification, without sending; kept 60 s."""
    key = (cfg.host, cfg.port, cfg.user)
    if _probe["key"] == key and time.monotonic() - _probe["at"] < PROBE_TTL_S:
        return _probe["result"]
    try:
        mailer.probe(cfg)
        result = {"ok": True, "error": None}
    except mailer.MailError as exc:
        result = {"ok": False, "error": str(exc)}
    result["at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _probe.update(key=key, at=time.monotonic(), result=result)
    return result


async def mail_block(db: Session) -> dict:
    """Brevo (lot 28): configuration, connection test, last sendings."""
    out = {**mailer.state(), **mailer.stats(db)}
    cfg = mailer.config()
    out["probe"] = await asyncio.to_thread(_probe_smtp, cfg) if cfg is not None else None
    return out


@router.get("/diagnostics/server")
async def server_diagnostics(request: Request, db: Session = Depends(get_db)):
    level, _email = level_of(request, db)
    now = datetime.now(UTC)
    comps, detail = components(db, now)
    info = server_version.info()
    base = {"level": level, "status": diag.overall(comps), "version": info["version"],
            "reference": info["reference"], "at": now.isoformat().replace("+00:00", "Z")}
    if level == "user":
        return base
    if level == "organizer":
        services = {k: comps[k] for k in ("aprs", "weather", "wakeup")}
        services["routes"] = "green" if await _routing_ok() else "red"
        return {**base, "services": services, "devices_connected": detail.get("realtime_clients", 0)}
    root = os.getenv("SARCADE_FILE_ROOT", "/var/lib/sarcade/files")
    res = await asyncio.to_thread(diag.resources, root if os.path.isdir(root) else "/")
    out = {**base, "started_at": diag.iso(diag.STARTED), "uptime_s": diag.uptime_s(),
           "components": comps, "detail": detail, "resources": res, "alert_pct": diag.ALERT_PCT,
           "last_update": _last_update(db), "last_backup": _last_backup(db),
           "update_status": updates.get_value(db, "update_status")}
    if level == "admin":
        from sarcade.weather import service as weather_service
        # Card « Météo : limiter les appels à Météo-France » (10 Nov 2026,
        # choices 4 and 5): state, pauses and calls of the day per source.
        out.update(build=info, addresses=addresses(), mail=await mail_block(db), deadlines=deadlines.view(db, now),
                   siem=siem.status(db, now), weather_sources=weather_service.status(now))
    return out


# --- Échéances (lot 28) -----------------------------------------------------------------

def _deadline_reader(request: Request, db: Session = Depends(get_db)) -> str:
    from .admin import require_journal_reader
    return require_journal_reader(request, db)


@router.get("/admin/deadlines")
def get_deadlines(db: Session = Depends(get_db), _reader: str = Depends(_deadline_reader)):
    """Everything that expires, with the manual items as the administrator typed them."""
    return {**deadlines.view(db), "manual": deadlines.manual(db)}


class DeadlinesIn(BaseModel):
    items: list[dict]


@router.put("/admin/deadlines")
def put_deadlines(body: DeadlinesIn, request: Request, db: Session = Depends(get_db),
                  admin: str = Depends(require_server_admin)):
    from sarcade.security import journal
    try:
        clean = deadlines.save(db, body.items, admin)
    except deadlines.InvalidDeadlines as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    journal.record(db, "admin", "deadlines_changed", "success", actor=admin, source_ip=client_ip(request),
                   details={"items": len(clean)})
    db.commit()
    return {**deadlines.view(db), "manual": deadlines.manual(db)}


@router.post("/admin/mail/test")
async def mail_test(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_server_admin)):
    """« Envoyer un e-mail d'essai » to the address of the administrator."""
    if admin == ADMIN_TOKEN_ACTOR or "@" not in admin:
        raise HTTPException(status_code=422, detail="admin_email_required")
    from sarcade.security import journal
    now = datetime.now(UTC)
    cfg = mailer.config()
    if cfg is None:
        journal.record(db, "admin", "mail_test", "info", actor=admin, source_ip=client_ip(request),
                       details={"delivery": "simulated"})
        db.commit()
        return {"status": "simulated", "to": admin}
    try:
        await asyncio.to_thread(mailer.send, cfg, mailer.test_message(cfg, admin, request.headers.get("host") or "SARCADE"))
    except mailer.MailError as exc:
        mailer.record(db, False, now, str(exc))
        journal.record(db, "admin", "mail_test", "failure", actor=admin, source_ip=client_ip(request),
                       details={"reason": str(exc)})
        db.commit()
        raise HTTPException(status_code=503, detail={"error": "mail_failed", "reason": str(exc)})
    mailer.record(db, True, now)
    journal.record(db, "admin", "mail_test", "success", actor=admin, source_ip=client_ip(request))
    db.commit()
    _probe.update(key=None)
    return {"status": "sent", "to": admin, "at": now.isoformat().replace("+00:00", "Z")}


@router.post("/admin/siem/test")
def siem_test(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_server_admin)):
    """« Envoyer un événement d'essai » (lot 29, group 8): an entry of the
    security journal, forwarded to the SIEM like the others."""
    from sarcade.security import journal
    row = journal.record(db, "admin", "siem_test", "info", actor=admin, source_ip=client_ip(request))
    db.commit()
    return {"status": "queued", "seq": row.seq, "siem": siem.status(db)}


@router.get("/version")
def api_version():
    return server_version.info()

"""Administration tool: security journal, maintenance windows, server
updates, minimal client version and client packages distributed by the
local server (note « Mises à jour et sécurité »)."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade import licensing, tls
from sarcade.licensing import core as licence_core
from sarcade.db.models import SecurityEventRow, ServerUpdateRow
from sarcade.security import journal, siem
from sarcade.updates import policy
from sarcade.updates import service as updates

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
MAX_CLIENT_PACKAGE_BYTES = int(os.getenv("SARCADE_MAX_CLIENT_PACKAGE_BYTES", str(512 * 1024 ** 2)))
# File names are constants: the platform given in the URL only selects one.
CLIENT_FILES = {"windows": "sarcade-windows-setup.exe", "appimage": "sarcade-appimage.AppImage",
                "apk": "sarcade-apk.apk"}


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Bearer token of the administrator (``SARCADE_ADMIN_TOKEN``, placed by
    the operator of the server). Without token configured the tool is open,
    as the rest of the v0.1 API, until accounts arrive (ADR-002, ADR-003)."""
    expected = os.getenv("SARCADE_ADMIN_TOKEN", "")
    if not expected:
        return "admin"
    # An administrator signed in through Cloudflare Access (email checked).
    from sarcade import roles
    from .roles import checked_email
    email = checked_email(request)
    if email is not None and "admin" in roles.roles_of(db, email):
        return email
    given = request.headers.get("authorization", "")
    token = given[7:] if given.lower().startswith("bearer ") else ""
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        journal.record(db, "auth", "admin_token", "failure", source_ip=client_ip(request),
                       details={"path": request.url.path[:120], "token_given": bool(token)})
        db.commit()
        raise HTTPException(status_code=401, detail="admin_token_required")
    return "admin"


def require_journal_reader(request: Request, db: Session = Depends(get_db)) -> str:
    """Administrator, or auditor of the server (lot 26): reads the journal
    without changing anything."""
    from sarcade import roles
    from sarcade.accounts.caller import caller_of
    caller = caller_of(request)
    if caller.email is not None and roles.directory_ready(db):
        from sarcade.directory import rights
        r = rights.of(db, caller.user_id, caller.email)
        if any(g.right == "auditor" and g.scope_group_id is None for g in r.grants):
            return caller.email
    return require_admin(request, db)


# --- Security journal -------------------------------------------------------

@router.get("/admin/security-journal")
def security_journal(category: str | None = None, since: datetime | None = None,
                     before_seq: int | None = None, limit: int = Query(200, ge=1, le=1000),
                     _admin: str = Depends(require_journal_reader), db: Session = Depends(get_db)):
    stmt = select(SecurityEventRow).order_by(SecurityEventRow.seq.desc()).limit(limit)
    if category:
        stmt = stmt.where(SecurityEventRow.category == category)
    if since:
        stmt = stmt.where(SecurityEventRow.at >= since)
    if before_seq:
        stmt = stmt.where(SecurityEventRow.seq < before_seq)
    return [journal.as_dict(r) for r in db.scalars(stmt)]


@router.get("/admin/security-journal/verify")
def verify_journal(_admin: str = Depends(require_journal_reader), db: Session = Depends(get_db)):
    return journal.verify(db)


# --- Settings of the administration tool ------------------------------------

@router.get("/admin/settings")
def get_settings(_admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    return updates.admin_settings(db)


class SettingsPatch(BaseModel):
    settings: dict
    actor_id: str = Field(default="admin", max_length=64)


@router.patch("/admin/settings")
def patch_settings(payload: SettingsPatch, request: Request, _admin: str = Depends(require_admin),
                   db: Session = Depends(get_db)):
    current = updates.admin_settings(db)
    unknown = set(payload.settings) - set(policy.DEFAULTS)
    if unknown:
        raise HTTPException(status_code=422, detail="unknown_setting")
    try:
        new = policy.validate(updates.merge_patch(current, payload.settings))
    except policy.InvalidSettings as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    updates.set_value(db, "admin", new)
    changed = sorted(k for k in new if new[k] != current.get(k))
    for key in changed:
        journal.record(db, "admin", f"{key}_changed", actor=payload.actor_id, source_ip=client_ip(request),
                       details={"before": str(current.get(key))[:200], "after": str(new[key])[:200]})
    db.commit()
    return new


# --- Server updates ---------------------------------------------------------

def _update_state(db: Session, now: datetime) -> dict:
    settings = updates.admin_settings(db)
    status = updates.get_value(db, "update_status", {}) or {}
    requested = bool((updates.get_value(db, "install_request", {}) or {}).get("requested"))
    active = updates.active_events(db, now)
    install, reason = policy.install_decision(
        available=status.get("available"), installed=updates.installed_version(),
        maintenance=settings["maintenance"], active_events=len(active), install_requested=requested, now=now)
    nxt = policy.next_window(settings["maintenance"], now)
    return {"installed": updates.installed_version(), "available": status.get("available"),
            "release_url": status.get("url"), "checked_at": status.get("checked_at"),
            "check_error": status.get("error"), "install_requested": requested,
            "active_events": active, "in_maintenance_window": policy.in_window(settings["maintenance"], now),
            "next_window": journal.iso(nxt) if nxt else None,
            "auto_updates_suspended": settings["maintenance"]["auto_updates_suspended"],
            "install": install, "reason": reason}


@router.get("/admin/updates")
def update_status(_admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    state = _update_state(db, now)
    history = db.scalars(select(ServerUpdateRow).order_by(ServerUpdateRow.id.desc()).limit(50)).all()
    state["history"] = [{"at": journal.iso(h.at), "from_version": h.from_version, "version": h.version,
                         "status": h.status, "detail": h.detail} for h in history]
    return state


@router.post("/admin/updates/check")
async def check_now(_admin: str = Depends(require_admin)):
    return await updates.check_latest()


@router.get("/admin/updates/plan")
def update_plan(_admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Asked by the updater on the host (docker/updater.sh) every few
    minutes: install now or not, and why."""
    state = _update_state(db, datetime.now(UTC))
    return {k: state[k] for k in ("install", "reason", "installed", "available", "active_events")}


@router.post("/admin/updates/install-now")
def install_now(request: Request, actor_id: str = Query("admin", max_length=64),
                _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """« Installer maintenant » for an urgent fix, never during an active event."""
    now = datetime.now(UTC)
    active = updates.active_events(db, now)
    if active:
        journal.record(db, "update", "install_now", "refused", actor=actor_id, source_ip=client_ip(request),
                       details={"reason": "active_event", "active_events": len(active)})
        db.commit()
        raise HTTPException(status_code=409, detail="active_event")
    updates.set_value(db, "install_request", {"requested": True, "by": actor_id, "at": journal.iso(now)})
    journal.record(db, "update", "install_now", "success", actor=actor_id, source_ip=client_ip(request))
    db.commit()
    return _update_state(db, now)


class UpdateReport(BaseModel):
    version: str = Field(max_length=32)
    status: str = Field(pattern="^(started|succeeded|failed|rolled_back|signature_invalid|backup_failed)$")
    detail: str | None = Field(default=None, max_length=2000)


@router.post("/admin/updates/report", status_code=201)
def update_report(payload: UpdateReport, request: Request, _admin: str = Depends(require_admin),
                  db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    db.add(ServerUpdateRow(at=now, from_version=updates.installed_version(), version=payload.version,
                           status=payload.status, detail=payload.detail))
    outcome = {"started": "info", "succeeded": "success"}.get(payload.status, "failure")
    journal.record(db, "update", f"server_update_{payload.status}", outcome, actor="updater",
                   source_ip=client_ip(request), details={"version": payload.version,
                                                          "installed": updates.installed_version()})
    if payload.status in ("succeeded", "rolled_back", "failed", "signature_invalid", "backup_failed"):
        updates.set_value(db, "install_request", {"requested": False})
    db.commit()
    return {"recorded": True}


@router.get("/admin/status")
def admin_status(_admin: str = Depends(require_admin)):
    return {"version": updates.installed_version(), "server_id": journal.server_id(),
            "pro_modules": sorted(licensing.enabled_modules()), "licence": licensing.state().as_dict(),
            "siem": dict(siem.state),
            "tls": tls.state()}


# --- SARCADE Pro licence ------------------------------------------------------

MAX_LICENCE_BYTES = 64 * 1024


@router.get("/admin/licence")
def get_licence(_admin: str = Depends(require_admin)):
    """State of the Pro licence: organisation, modules, expiry, grace."""
    return licensing.state().as_dict()


@router.put("/admin/licence")
async def put_licence(request: Request, _admin: str = Depends(require_admin), db: Session = Depends(get_db)):
    """Upload of a licence file (JSON). Checked offline before installation;
    an invalid or expired licence never replaces the installed one."""
    body = await request.body()
    if len(body) > MAX_LICENCE_BYTES:
        raise HTTPException(status_code=413, detail="licence_too_large")
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        document = None
    result = licensing.install(document) if isinstance(document, dict) else licence_core.verify({"format": "?"})
    accepted = result.status not in ("invalid", "expired", "none")
    lic = result.licence or {}
    journal.record(db, "admin", "licence_uploaded", "success" if accepted else "refused",
                   source_ip=client_ip(request),
                   details={"licence_id": lic.get("id"), "status": result.status, "reason": result.reason,
                            "modules": ",".join(sorted(result.modules)), "test": result.test})
    db.commit()
    if not accepted:
        raise HTTPException(status_code=422, detail={"error": "licence_refused", "status": result.status,
                                                     "reason": result.reason})
    return result.as_dict()


@router.post("/admin/licence/check")
def check_licence(_admin: str = Depends(require_admin)):
    return licensing.check().as_dict()


# --- Client applications ------------------------------------------------------

class ClientCheck(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    platform: str | None = Field(default=None, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)


@router.post("/clients/check")
def client_check(payload: ClientCheck, request: Request, db: Session = Depends(get_db)):
    """Asked by a client at start and before joining an event."""
    result = updates.evaluate_client(db, payload.device_id, payload.platform, payload.app_version,
                                     datetime.now(UTC), source_ip=client_ip(request))
    result["organization"] = client_config(db)
    db.commit()
    return result


@router.get("/clients/config")
def client_config(db: Session = Depends(get_db)):
    """Settings of the organisation the clients apply (navigation app, HTTPS)."""
    return updates.client_organization(db)


def clients_root() -> Path:
    return Path(os.getenv("SARCADE_FILE_ROOT", "/var/lib/sarcade/files")) / "clients"


@router.get("/clients/latest")
def clients_latest(db: Session = Depends(get_db)):
    packages = updates.get_value(db, "client_packages", {}) or {}
    return {p: updates.package_info(p, pkg) for p, pkg in sorted(packages.items())}


@router.put("/admin/clients/{platform}/package")
async def upload_client(platform: str, request: Request, version: str = Form(..., max_length=32),
                        file: UploadFile = File(...), _admin: str = Depends(require_admin),
                        db: Session = Depends(get_db)):
    """Signed client package (Windows, AppImage, APK) distributed by the
    local server on a network without Internet. The client checks the
    platform signature before installing it."""
    filename = CLIENT_FILES.get(platform)
    if filename is None:
        raise HTTPException(status_code=404, detail="unknown_platform")
    if not policy.parse_version(version) or not policy._VERSION.match(version.strip()):
        raise HTTPException(status_code=422, detail="invalid_version")
    root = clients_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".upload-{uuid.uuid4()}"
    digest, size = hashlib.sha256(), 0
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CLIENT_PACKAGE_BYTES:
                    raise HTTPException(status_code=413, detail="package_too_large")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="empty_package")
        shutil.move(tmp, root / filename)
    finally:
        tmp.unlink(missing_ok=True)
    now = datetime.now(UTC)
    packages = dict(updates.get_value(db, "client_packages", {}) or {})
    key = next(p for p in CLIENT_FILES if p == platform)
    packages[key] = {"version": version.strip(), "sha256": digest.hexdigest(), "size": size,
                          "uploaded_at": journal.iso(now)}
    updates.set_value(db, "client_packages", packages, now)
    journal.record(db, "admin", "client_package_uploaded", source_ip=client_ip(request),
                   details={"platform": key, "version": version.strip(), "sha256": digest.hexdigest()})
    db.commit()
    return updates.package_info(key, packages[key])


@router.get("/clients/{platform}/package")
def download_client(platform: str):
    filename = CLIENT_FILES.get(platform)
    if filename is None:
        raise HTTPException(status_code=404, detail="unknown_platform")
    path = clients_root() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no_client_package")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)

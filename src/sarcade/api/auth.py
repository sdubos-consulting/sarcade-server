# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Connection by a code sent by email and enrolled devices (lot 26).
Contract: docs/accounts-v0.1.md."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade import accounts
from sarcade.accounts import mailer
from sarcade.accounts.caller import caller_of
from sarcade.db.models import AuthDeviceRow, UserRow
from sarcade.realtime.manager import broadcast_all
from sarcade.security import journal

from .authz import client_ip, require_server_admin
from .deps import get_db

router = APIRouter(prefix="/api/v0.1")


def _refuse(db: Session, exc: accounts.AccountError):
    db.commit()  # attempts counted, codes superseded
    detail = {"error": exc.detail, **exc.extra} if exc.extra else exc.detail
    headers = {"Retry-After": str(exc.extra["retry_after_s"])} if "retry_after_s" in exc.extra else None
    raise HTTPException(status_code=exc.status, detail=detail, headers=headers)


@router.get("/auth/state")
def auth_state():
    """What the application shows before « Se connecter »."""
    return {"methods": list(accounts.METHODS), "delivery": mailer.delivery(),
            "code_length": 6, "code_ttl_s": int(accounts.CODE_TTL.total_seconds()),
            "resend_after_s": int(accounts.RESEND_DELAY.total_seconds()), "max_attempts": accounts.MAX_ATTEMPTS}


class CodeRequest(BaseModel):
    email: str = Field(max_length=320)


@router.post("/auth/code/request", status_code=202)
def request_code(body: CodeRequest, request: Request, db: Session = Depends(get_db)):
    """Sends a code to the address. Same answer whether an account exists
    or not."""
    now = datetime.now(UTC)
    ip = client_ip(request)
    try:
        issued = accounts.issue_code(db, body.email, now, ip)
    except accounts.AccountError as exc:
        if exc.status == 429:
            journal.record(db, "auth", "login_code_refused", "refused", actor=body.email.strip().lower()[:64],
                           source_ip=ip, details={"reason": exc.detail})
        _refuse(db, exc)
    email = issued.row.email
    cfg = mailer.config()
    minutes = int(accounts.CODE_TTL.total_seconds() // 60)
    if cfg is None:
        # Simulated sending: the code is readable by the administrator in
        # the security journal only (never in the application logs).
        journal.record(db, "auth", "login_code_simulated", "info", actor=email, source_ip=ip,
                       details={"email": email, "code": issued.code, "expires_at": accounts.iso(issued.row.expires_at)})
    else:
        try:
            mailer.send(cfg, mailer.code_message(cfg, email, issued.code, minutes))
        except mailer.MailError as exc:
            issued.row.used_at = now
            journal.record(db, "auth", "login_code_sent", "failure", actor=email, source_ip=ip,
                           details={"email": email, "reason": str(exc)})
            db.commit()
            raise HTTPException(status_code=503, detail="mail_failed")
        journal.record(db, "auth", "login_code_sent", "success", actor=email, source_ip=ip,
                       details={"email": email})
    db.commit()
    return {"status": "sent", "delivery": "simulated" if cfg is None else "smtp",
            "expires_in_s": int(accounts.CODE_TTL.total_seconds()),
            "resend_after_s": int(accounts.RESEND_DELAY.total_seconds()), "code_length": 6}


class DeviceInfo(BaseModel):
    device_id: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=120)
    platform: str | None = Field(default=None, max_length=32)


class CodeVerify(DeviceInfo):
    email: str = Field(max_length=320)
    code: str = Field(max_length=16)


def _enrolled(db: Session, request: Request, user: UserRow, created: bool, method: str, info: DeviceInfo,
              now: datetime) -> dict:
    try:
        device, token = accounts.enroll(db, user, now, method, device_id=info.device_id, label=info.label,
                                        platform=info.platform)
    except accounts.AccountError as exc:
        journal.record(db, "auth", "login", "refused", actor=user.email, source_ip=client_ip(request),
                       details={"reason": exc.detail, "method": method})
        _refuse(db, exc)
    if created:
        journal.record(db, "auth", "account_created", "success", actor=user.email, source_ip=client_ip(request),
                       details={"user_id": user.id, "method": method})
    journal.record(db, "device", "device_enrolled", "success", actor=user.email, device_id=info.device_id,
                   source_ip=client_ip(request),
                   details={"auth_device_id": device.id, "method": method, "platform": info.platform,
                            "label": info.label})
    db.commit()
    return {"token": token, "device": accounts.device_dict(device), "account": accounts.user_dict(user),
            "created": created}


@router.post("/auth/code/verify")
def verify_code(body: CodeVerify, request: Request, db: Session = Depends(get_db)):
    """Checks the code; creates the account at the first connection and
    enrols the device. The token is given only in this answer."""
    now = datetime.now(UTC)
    try:
        email = accounts.normal_email(body.email)
        accounts.check_code(db, email, body.code, now)
    except accounts.AccountError as exc:
        journal.record(db, "auth", "login_code_checked", "failure", actor=body.email.strip().lower()[:64],
                       source_ip=client_ip(request), details={"reason": exc.detail})
        _refuse(db, exc)
    user, created = accounts.ensure_user(db, email, now, "code e-mail")
    return _enrolled(db, request, user, created, "email_code", body, now)


@router.post("/auth/access/enroll")
def access_enroll(body: DeviceInfo, request: Request, db: Session = Depends(get_db)):
    """Enrolment of a device whose email was checked by Cloudflare Access."""
    caller = caller_of(request)
    if caller.via != "access":
        raise HTTPException(status_code=403, detail="access_email_required")
    now = datetime.now(UTC)
    user, created = accounts.ensure_user(db, caller.email, now, "Cloudflare Access")
    return _enrolled(db, request, user, created, "access", body, now)


@router.post("/auth/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Forgets this device: its token is revoked."""
    caller = caller_of(request)
    if caller.via != "device":
        raise HTTPException(status_code=409, detail="no_device_token")
    row = db.get(AuthDeviceRow, caller.auth_device_id)
    await _revoke(db, request, row, caller.email, by_owner=True)
    return {"revoked": True}


# --- My devices -------------------------------------------------------------------

def _my_user(request: Request) -> str:
    caller = caller_of(request)
    if caller.user_id is None:
        raise HTTPException(status_code=401, detail="account_required")
    return caller.user_id


@router.get("/me/devices")
def my_devices(request: Request, db: Session = Depends(get_db)):
    user_id = _my_user(request)
    current = caller_of(request).auth_device_id
    rows = db.scalars(select(AuthDeviceRow).where(AuthDeviceRow.user_id == user_id, AuthDeviceRow.revoked_at.is_(None))
                      .order_by(AuthDeviceRow.created_at)).all()
    return [dict(accounts.device_dict(r), current=r.id == current) for r in rows]


@router.delete("/me/devices/{auth_device_id}")
async def revoke_my_device(auth_device_id: str, request: Request, db: Session = Depends(get_db)):
    user_id = _my_user(request)
    row = db.get(AuthDeviceRow, auth_device_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="device_not_found")
    return await _revoke(db, request, row, caller_of(request).email, by_owner=True)


async def _revoke(db: Session, request: Request, row: AuthDeviceRow, actor: str, by_owner: bool) -> dict:
    now = datetime.now(UTC)
    changed = accounts.revoke(db, row, actor, now)
    if changed:
        journal.record(db, "device", "device_revoked", "success", actor=actor, device_id=row.device_id,
                       source_ip=client_ip(request), details={"auth_device_id": row.id, "user_id": row.user_id,
                                                             "by_owner": by_owner})
    db.commit()
    if changed:
        await broadcast_all({"type": "device.revoked", "data": {"id": row.id, "user_id": row.user_id,
                                                                "device_id": row.device_id}})
    return accounts.device_dict(row)


# --- Administration: accounts and enrolled devices ----------------------------------

@router.get("/admin/users")
def admin_users(q: str | None = Query(None, max_length=320), limit: int = Query(500, ge=1, le=2000),
                _admin: str = Depends(require_server_admin), db: Session = Depends(get_db)):
    stmt = select(UserRow).order_by(UserRow.email).limit(limit)
    if q:
        stmt = stmt.where(UserRow.email.contains(q.strip().lower()))
    return [accounts.user_dict(r) for r in db.scalars(stmt)]


@router.get("/admin/devices")
def admin_devices(user_id: str | None = None, include_revoked: bool = Query(False),
                  _admin: str = Depends(require_server_admin), db: Session = Depends(get_db)):
    stmt = select(AuthDeviceRow, UserRow).join(UserRow, UserRow.id == AuthDeviceRow.user_id) \
        .order_by(AuthDeviceRow.created_at.desc())
    if user_id:
        stmt = stmt.where(AuthDeviceRow.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(AuthDeviceRow.revoked_at.is_(None))
    return [accounts.device_dict(d, u) for d, u in db.execute(stmt).all()]


@router.post("/admin/devices/{auth_device_id}/revoke")
async def admin_revoke_device(auth_device_id: str, request: Request, admin: str = Depends(require_server_admin),
                              db: Session = Depends(get_db)):
    row = db.get(AuthDeviceRow, auth_device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="device_not_found")
    return await _revoke(db, request, row, admin, by_owner=False)


@router.get("/admin/mail")
def admin_mail(_admin: str = Depends(require_server_admin)):
    """State of the sending of the codes (never the login nor the key)."""
    return mailer.state()

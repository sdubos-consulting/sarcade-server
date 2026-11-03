# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Accounts, connection by a code sent by email and enrolled devices
(lot 26, « Groupes d'organisation et habilitations », decisions of
3 Nov 2026).

- An account is identified by its email (unique key, lower case, no space)
  and keeps a fixed internal id.
- First connection of a device: the server sends a 6-digit code to the
  address; the code is checked, the account is created if needed, and the
  device is *enrolled*: it receives a long token (``sdt_…``), kept by the
  device and sent in ``Authorization: Bearer``. This server checks it
  locally, without Internet; an administrator or the owner revokes it.
- Cloudflare Access stays accepted as a proof of the email.
- Anti-abuse: code valid 10 minutes, 5 attempts, a new code at most every
  60 s and 5 per hour for an address, 20 requests per hour from one IP
  address; the answer never says whether an account exists.

Only hashes of codes and tokens are stored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sarcade.db.models import AuthDeviceRow, LoginCodeRow, UserRow

__all__ = ["UserRow", "AuthDeviceRow", "LoginCodeRow"]

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_DELAY = timedelta(seconds=60)
MAX_CODES_PER_HOUR = 5
MAX_REQUESTS_PER_IP_PER_HOUR = 20
TOKEN_PREFIX = "sdt_"
TOUCH_EVERY = timedelta(minutes=5)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
METHODS = ("email_code", "access")


class AccountError(Exception):
    """Refusal with an HTTP status and a stable detail code."""

    def __init__(self, status: int, detail: str, **extra):
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra


def normal_email(value) -> str:
    """Lower case, without spaces; raises AccountError(422, invalid_email)."""
    if not isinstance(value, str):
        raise AccountError(422, "invalid_email")
    email = value.strip().lower()
    if len(email) > 320 or not _EMAIL.match(email):
        raise AccountError(422, "invalid_email")
    return email


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def iso(t: datetime | None) -> str | None:
    t = _utc(t)
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code_hash(code_id: str, code: str) -> str:
    return hash_secret(f"{code_id}:{code}")


# --- Accounts ----------------------------------------------------------------

def user_by_email(db: Session, email: str) -> UserRow | None:
    return db.scalar(select(UserRow).where(UserRow.email == email))


def user_dict(row: UserRow) -> dict:
    return {"id": row.id, "email": row.email, "display_name": row.display_name, "status": row.status,
            "created_at": iso(row.created_at), "last_login_at": iso(row.last_login_at)}


# Called with (db, user) after an account is created: the directory turns
# the invitations of the address into memberships (sarcade.directory).
on_account_created: list = []


def ensure_user(db: Session, email: str, now: datetime, created_by: str) -> tuple[UserRow, bool]:
    """The account of [email], created at the first connection."""
    email = normal_email(email)
    row = user_by_email(db, email)
    if row is not None:
        return row, False
    row = UserRow(id=str(uuid.uuid4()), email=email, status="active", created_at=now, created_by=created_by[:64])
    db.add(row)
    db.flush()
    for hook in on_account_created:
        hook(db, row)
    return row, True


# --- Codes -------------------------------------------------------------------

@dataclass
class IssuedCode:
    row: LoginCodeRow
    code: str


def issue_code(db: Session, email: str, now: datetime, source_ip: str | None = None) -> IssuedCode:
    """A new code for [email], after the anti-abuse checks. The previous
    codes of the address stop being valid."""
    email = normal_email(email)
    hour = now - timedelta(hours=1)
    if source_ip:
        n_ip = db.scalar(select(func.count()).select_from(LoginCodeRow).where(
            LoginCodeRow.source_ip == source_ip, LoginCodeRow.created_at >= hour)) or 0
        if n_ip >= MAX_REQUESTS_PER_IP_PER_HOUR:
            raise AccountError(429, "too_many_requests", retry_after_s=3600)
    recent = db.scalars(select(LoginCodeRow).where(LoginCodeRow.email == email, LoginCodeRow.created_at >= hour)
                        .order_by(LoginCodeRow.created_at.desc())).all()
    if recent:
        wait = (_utc(recent[0].created_at) + RESEND_DELAY - now).total_seconds()
        if wait > 0:
            raise AccountError(429, "resend_too_soon", retry_after_s=int(wait) + 1)
    if len(recent) >= MAX_CODES_PER_HOUR:
        oldest = _utc(recent[-1].created_at)
        raise AccountError(429, "too_many_codes",
                           retry_after_s=max(1, int((oldest + timedelta(hours=1) - now).total_seconds()) + 1))
    for old in db.scalars(select(LoginCodeRow).where(LoginCodeRow.email == email, LoginCodeRow.used_at.is_(None))):
        old.used_at = now  # superseded
    code = f"{secrets.randbelow(10 ** 6):06d}"
    row = LoginCodeRow(id=str(uuid.uuid4()), email=email, created_at=now, expires_at=now + CODE_TTL,
                       attempts=0, source_ip=(source_ip or None) and source_ip[:64], code_hash="")
    row.code_hash = _code_hash(row.id, code)
    db.add(row)
    db.flush()
    return IssuedCode(row, code)


def check_code(db: Session, email: str, code, now: datetime) -> LoginCodeRow:
    """Checks the last code sent to [email]; raises AccountError
    (invalid_code with attempts_left, code_expired, too_many_attempts,
    no_code). The caller commits in every case (attempts are counted)."""
    email = normal_email(email)
    code = str(code or "").strip().replace(" ", "")
    row = db.scalars(select(LoginCodeRow).where(LoginCodeRow.email == email)
                     .order_by(LoginCodeRow.created_at.desc()).limit(1)).first()
    if row is None or (row.used_at is not None and row.attempts < MAX_ATTEMPTS):
        raise AccountError(400, "no_code")
    if row.attempts >= MAX_ATTEMPTS:
        raise AccountError(429, "too_many_attempts")
    if now >= _utc(row.expires_at):
        raise AccountError(410, "code_expired")
    row.attempts += 1
    if not re.fullmatch(r"\d{6}", code) or not hmac.compare_digest(_code_hash(row.id, code), row.code_hash):
        left = MAX_ATTEMPTS - row.attempts
        if left <= 0:
            row.used_at = now
            raise AccountError(429, "too_many_attempts")
        raise AccountError(401, "invalid_code", attempts_left=left)
    row.used_at = now
    return row


# --- Enrolled devices ------------------------------------------------------------

def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def enroll(db: Session, user: UserRow, now: datetime, method: str, *, device_id: str | None = None,
           label: str | None = None, platform: str | None = None) -> tuple[AuthDeviceRow, str]:
    """A new enrolled device for [user]; returns the row and the token,
    shown only once."""
    if user.status != "active":
        raise AccountError(403, "account_disabled")
    token = new_token()
    row = AuthDeviceRow(id=str(uuid.uuid4()), user_id=user.id, token_hash=hash_secret(token),
                        device_id=(device_id or None) and device_id.strip()[:64] or None,
                        label=(label or None) and label.strip()[:120] or None,
                        platform=(platform or None) and platform.strip()[:32] or None,
                        method=method, created_at=now, last_seen_at=now)
    db.add(row)
    user.last_login_at = now
    db.flush()
    return row, token


def device_dict(row: AuthDeviceRow, user: UserRow | None = None) -> dict:
    d = {"id": row.id, "user_id": row.user_id, "device_id": row.device_id, "label": row.label,
         "platform": row.platform, "method": row.method, "created_at": iso(row.created_at),
         "last_seen_at": iso(row.last_seen_at), "revoked_at": iso(row.revoked_at), "revoked_by": row.revoked_by,
         "active": row.revoked_at is None}
    if user is not None:
        d["email"] = user.email
    return d


def device_by_token(db: Session, token: str) -> AuthDeviceRow | None:
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    return db.scalar(select(AuthDeviceRow).where(AuthDeviceRow.token_hash == hash_secret(token)))


def revoke(db: Session, row: AuthDeviceRow, by: str, now: datetime) -> bool:
    if row.revoked_at is not None:
        return False
    row.revoked_at, row.revoked_by = now, by[:64]
    return True


def touch(row: AuthDeviceRow, now: datetime) -> bool:
    """Last use, written at most every 5 minutes."""
    last = _utc(row.last_seen_at)
    if last is None or now - last >= TOUCH_EVERY:
        row.last_seen_at = now
        return True
    return False

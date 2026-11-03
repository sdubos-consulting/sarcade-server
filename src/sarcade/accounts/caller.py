# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Who calls: an enrolled device (``Authorization: Bearer sdt_…``), a
person checked by Cloudflare Access, or nobody (device without account,
rights of the Core during the transition).

``resolve`` runs before every route (application dependency) and puts the
result in ``request.state.caller``; the routes read it with ``caller_of``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from sarcade import accounts
from sarcade.access.verify import identity_of
from sarcade.api.deps import get_db
from sarcade.db.models import UserRow


@dataclass(frozen=True)
class Caller:
    email: str | None = None
    user_id: str | None = None
    via: str | None = None            # "device", "access" or None
    auth_device_id: str | None = None
    device_id: str | None = None      # declared at the enrolment (TEL-01…)

    @property
    def identified(self) -> bool:
        return self.email is not None


ANONYMOUS = Caller()


def bearer(conn: HTTPConnection) -> str | None:
    given = conn.headers.get("authorization", "")
    token = given[7:].strip() if given.lower().startswith("bearer ") else ""
    return token if token.startswith(accounts.TOKEN_PREFIX) else None


def _access_email(conn) -> str | None:
    identity = identity_of(conn)
    return identity.email.strip().lower() if identity is not None and identity.email else None


def resolve(conn: HTTPConnection, db: Session = Depends(get_db)) -> Caller:
    """Application dependency: checks the device token if any. A token
    unknown or revoked is refused (401) so that the application forgets it
    and offers « Se connecter » again."""
    token = bearer(conn)
    caller = ANONYMOUS
    if token is not None:
        row = accounts.device_by_token(db, token)
        user = db.get(UserRow, row.user_id) if row is not None else None
        if row is None or row.revoked_at is not None or user is None or user.status != "active":
            reason = "device_revoked" if row is not None else "device_token_invalid"
            if conn.scope["type"] == "http":
                from sarcade.security import journal
                journal.record(db, "auth", "device_token_refused", "refused",
                               source_ip=conn.client.host if conn.client else None,
                               details={"reason": reason, "path": conn.url.path[:120]})
                db.commit()
                raise HTTPException(status_code=401, detail=reason,
                                    headers={"WWW-Authenticate": 'Bearer realm="SARCADE"'})
        else:
            if accounts.touch(row, datetime.now(UTC)):
                db.commit()
            caller = Caller(email=user.email, user_id=user.id, via="device", auth_device_id=row.id,
                            device_id=row.device_id)
    if caller is ANONYMOUS:
        email = _access_email(conn)
        if email is not None:
            user = _user_by_email(db, email)
            caller = Caller(email=email, user_id=user.id if user is not None else None, via="access")
    conn.state.caller = caller
    return caller


def _user_by_email(db: Session, email: str):
    """The account of a person checked by Cloudflare Access, if any. Some
    unit tests run routes without database: the caller stays without
    account then."""
    try:
        return accounts.user_by_email(db, email)
    except SQLAlchemyError:
        db.rollback()
        return None


def caller_of(conn) -> Caller:
    """The caller of a request already resolved, else from Cloudflare Access
    only (direct calls in tests)."""
    state = getattr(conn, "state", None)
    caller = getattr(state, "caller", None) if state is not None else None
    if isinstance(caller, Caller):
        return caller
    email = _access_email(conn) if state is not None else None
    return Caller(email=email, via="access") if email else ANONYMOUS

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Checks shared by the administration routes of the accounts and of the
directory (lot 26). The server is the judge of every call."""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from sarcade import roles
from sarcade.accounts.caller import bearer, caller_of
from sarcade.security import journal

from .deps import get_db

ADMIN_TOKEN_ACTOR = "jeton-admin (.env)"


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def admin_token_used(request: Request) -> bool:
    """Safety access by the ``.env`` of the server: ``SARCADE_ADMIN_TOKEN``."""
    expected = os.getenv("SARCADE_ADMIN_TOKEN", "")
    given = request.headers.get("authorization", "")
    token = given[7:].strip() if given.lower().startswith("bearer ") else ""
    if not expected or not token or bearer(request) is not None:
        return False
    return hmac.compare_digest(token.encode(), expected.encode())


def require_server_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Administrator of the server (account, ``SARCADE_ADMIN_EMAILS`` or
    ``SARCADE_ADMIN_TOKEN``); returns the author written in the journal."""
    if admin_token_used(request):
        return ADMIN_TOKEN_ACTOR
    caller = caller_of(request)
    if caller.email is not None and "admin" in roles.roles_of(db, caller.email):
        return caller.email
    journal.record(db, "rights", "admin_required", "refused", actor=caller.email, source_ip=client_ip(request),
                   details={"path": request.url.path[:120]})
    db.commit()
    raise HTTPException(status_code=403 if caller.email else 401, detail="admin_required")

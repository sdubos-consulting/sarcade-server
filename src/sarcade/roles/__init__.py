"""Rights checked by the server from the email verified by Cloudflare
Access (decision of 28 Oct 2026): the name of the terminal no longer gives
rights. First step: the lists « administrateurs » and « PCO », kept in the
server settings; rule: always at least one active administrator
(``SARCADE_ADMIN_EMAILS`` in the .env of the server as safety net)."""
from __future__ import annotations

import os
import weakref

from sqlalchemy.orm import Session

KEY = "roles"


class InvalidRoles(ValueError):
    pass


def _clean(emails) -> list[str]:
    if not isinstance(emails, list) or len(emails) > 500:
        raise InvalidRoles("invalid_value")
    out: list[str] = []
    for e in emails:
        if not isinstance(e, str):
            raise InvalidRoles("invalid_value")
        e = e.strip().lower()
        if not 3 <= len(e) <= 320 or "@" not in e or " " in e:
            raise InvalidRoles("invalid_email")
        if e not in out:
            out.append(e)
    return out


def env_admins() -> list[str]:
    return [e.strip().lower() for e in os.getenv("SARCADE_ADMIN_EMAILS", "").split(",") if e.strip()]


def stored(db: Session) -> dict:
    from sarcade.updates.service import get_value
    v = get_value(db, KEY, {}) or {}
    return {"admins": list(v.get("admins", [])), "pco": list(v.get("pco", []))}


def save(db: Session, admins, pco) -> dict:
    """New lists; refused if no administrator would remain."""
    from sarcade.updates.service import set_value
    a, p = _clean(admins), _clean(pco)
    if not a and not env_admins():
        raise InvalidRoles("last_admin")
    set_value(db, KEY, {"admins": a, "pco": p})
    return {"admins": a, "pco": p}


def directory_ready(db) -> bool:
    """The directory tables exist (always on PostgreSQL after the
    migrations; some unit tests run on a partial SQLite database)."""
    if not hasattr(db, "scalars"):
        return False
    try:
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            return True
        if bind not in _ready:
            from sqlalchemy import inspect
            # Through the connection of the session: never ends its transaction.
            insp = inspect(db.connection())
            _ready[bind] = insp.has_table("org_groups") and insp.has_table("users")
        return _ready[bind]
    except Exception:  # noqa: BLE001
        return False


_ready: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
PCO_RIGHTS = ("event_organizer", "group_admin")


def roles_of(db: Session, email: str | None) -> list[str]:
    """« admin » (administrator of the server) and « pco » (organiser of
    events), from the lists of 28 October, the .env, or the habilitations of
    the directory (lot 26)."""
    if not email:
        return []
    e = email.strip().lower()
    s = stored(db)
    admin = e in s["admins"] or e in env_admins()
    pco = e in s["pco"]
    if not admin and directory_ready(db):
        from sarcade.directory import rights
        r = rights.for_email(db, e)
        admin = r.server_admin
        pco = pco or any(g.right in PCO_RIGHTS for g in r.grants)
    out = []
    if admin:
        out.append("admin")
    if pco or admin:
        out.append("pco")
    return out

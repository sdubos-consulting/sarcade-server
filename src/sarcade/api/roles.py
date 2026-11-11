"""Who am I, and the lists of administrators and PCO (console)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sarcade import accounts, roles
from sarcade.accounts.caller import caller_of
from sarcade.security import journal

from .deps import get_db

router = APIRouter(prefix="/api/v0.1")


def checked_email(request: Request) -> str | None:
    """Email of the caller: account of an enrolled device (lot 26) or
    person checked by Cloudflare Access; None for a device without account."""
    return caller_of(request).email


def require_pco(request: Request, db: Session = Depends(get_db)) -> str | None:
    """PCO: organiser of the events of the group that organises the event
    of the route (lot 26), else organiser of some group, or administrator.
    Without identity (device without account) the previous behaviour is
    kept during the transition only."""
    email = checked_email(request)
    ready = roles.directory_ready(db)
    if email is None:
        from sarcade.directory import transition
        if not ready or transition.active():
            return None
        _refused(db, request, None)
    event_id = (getattr(request, "path_params", None) or {}).get("event_id")
    if event_id and ready:
        from sarcade.db.models import EventRow
        from sarcade.directory import events as dir_events
        event = db.get(EventRow, event_id)
        if event is not None and event.organizer_group_id:
            if dir_events.can_organize(db, caller_of(request), event) or email in roles.stored(db)["pco"]:
                return email
            _refused(db, request, email)
    if "pco" not in roles.roles_of(db, email):
        _refused(db, request, email)
    return email


def _refused(db: Session, request: Request, email: str | None):
    journal.record(db, "rights", "pco_required", "failure",
                   details={"path": request.url.path[:120], "email": email})
    db.commit()
    raise HTTPException(status_code=403, detail="pco_required")


def require_admin_email(request: Request, db: Session = Depends(get_db)) -> str:
    email = checked_email(request)
    if email is None or "admin" not in roles.roles_of(db, email):
        raise HTTPException(status_code=403, detail="admin_required")
    return email


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    """Who am I: email, account, enrolled device, rights (docs/accounts-v0.1.md).
    A person checked by Cloudflare Access gets an account at the first call."""
    caller = caller_of(request)
    email = caller.email
    out = {"email": email, "checked": email is not None, "roles": roles.roles_of(db, email),
           "via": caller.via, "account": None, "device": None}
    if roles.directory_ready(db):
        from sarcade.directory import transition
        out["transition"] = transition.state(db)
    if email is None or not hasattr(db, "scalar"):
        out.update(rights_view(db, None, None))
        return out
    from datetime import UTC, datetime
    from sarcade.db.models import AuthDeviceRow
    user = db.get(accounts.UserRow, caller.user_id) if caller.user_id else None
    if user is None and caller.via == "access":
        user, created = accounts.ensure_user(db, email, datetime.now(UTC), "Cloudflare Access")
        if created:
            journal.record(db, "auth", "account_created", "success", actor=email,
                           details={"user_id": user.id, "method": "access"})
        db.commit()
    if user is not None:
        out["account"] = accounts.user_dict(user)
    if caller.auth_device_id:
        out["device"] = accounts.device_dict(db.get(AuthDeviceRow, caller.auth_device_id))
    out.update(rights_view(db, user.id if user is not None else None, email))
    if "admin" in out["roles"]:
        # Red dot on Paramètres for the administrators (lot 28, Échéances).
        try:
            from sarcade import deadlines
            out["deadlines"] = deadlines.view(db)["summary"]
        except Exception:  # noqa: BLE001 - never blocks « who am I »
            pass
    return out


def rights_view(db: Session, user_id: str | None, email: str | None) -> dict:
    """Groups, administration rights and modules of the caller (lot 26):
    the applications show only the modules and menus they allow; the
    server still checks every call."""
    if not roles.directory_ready(db):
        return {}
    from sarcade.directory import graph as graphs, rights as R
    r = R.of(db, user_id, email)
    g = graphs.load(db)
    admin = r.server_admin or "admin" in roles.roles_of(db, email)
    return {
        "groups": [{"id": gid, "short_name": g.name(gid), "name": g.groups[gid].name, "direct": gid in r.direct}
                   for gid in r.reached if gid in g.groups],
        "admin_rights": [{"right": x.right, "scope_group_id": x.scope_group_id,
                          "scope_name": g.name(x.scope_group_id) if x.scope_group_id else None,
                          "until": R.iso(x.until)} for x in r.admin_grants()],
        "modules": r.modules(),
        "module_rights": [{"module": m, "licensed": m in r.modules()} for m in sorted(r.module_habilitations())],
        "is_server_admin": admin,
        "can_administer": admin or r.can_administer,
        "rights_version": r.version,
    }


class RolesIn(BaseModel):
    admins: list[str]
    pco: list[str]


def _bridge(db: Session) -> tuple[str, str] | None:
    """After the migration of lot 26 the two lists are two groups:
    « Administrateurs du serveur » and « PCO ADRASEC 78 »."""
    if not roles.directory_ready(db):
        return None
    from sarcade.db.models import OrgGroupRow
    from sarcade.directory import SERVER_ADMINS_ID
    from sarcade.updates.service import get_value
    pco = (get_value(db, "directory", {}) or {}).get("pco_group_id")
    if not pco or db.get(OrgGroupRow, pco) is None or db.get(OrgGroupRow, SERVER_ADMINS_ID) is None:
        return None
    return SERVER_ADMINS_ID, pco


def _emails(db: Session, gid: str) -> list[str]:
    from sarcade.directory import service
    m = service.members(db, gid)
    return sorted([u["email"] for u in m["users"]] + [i["email"] for i in m["invitations"]])


@router.get("/admin/roles")
def get_roles(_admin: str = Depends(require_admin_email), db: Session = Depends(get_db)):
    bridge = _bridge(db)
    if bridge is not None:
        return {"admins": _emails(db, bridge[0]), "pco": _emails(db, bridge[1]), "env_admins": roles.env_admins(),
                "groups": {"admins": bridge[0], "pco": bridge[1]}}
    return {**roles.stored(db), "env_admins": roles.env_admins()}


class RolesIn(BaseModel):
    admins: list[str]
    pco: list[str]


@router.put("/admin/roles")
async def put_roles(body: RolesIn, admin: str = Depends(require_admin_email), db: Session = Depends(get_db)):
    bridge = _bridge(db)
    if bridge is None:
        try:
            out = roles.save(db, body.admins, body.pco)
        except roles.InvalidRoles as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        journal.record(db, "admin", "roles_changed", "success",
                       details={"by": admin, "admins": len(out["admins"]), "pco": len(out["pco"])})
        db.commit()
        return {**out, "env_admins": roles.env_admins()}
    # Lists of the console written in the two groups (lot 26).
    from datetime import UTC, datetime
    from sarcade.directory import DirectoryError, rights as R, service
    from sarcade.realtime.manager import broadcast_all
    try:
        wanted = {"admins": roles._clean(body.admins), "pco": roles._clean(body.pco)}
    except roles.InvalidRoles as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    actor = R.Actor(label=admin, token=True)
    now = datetime.now(UTC)
    try:
        for key, gid in zip(("admins", "pco"), bridge):
            current = service.members(db, gid)
            for email in wanted[key]:
                service.add_email(db, actor, gid, email, now)
            for u in current["users"]:
                if u["email"] not in wanted[key]:
                    service.remove_user(db, actor, gid, u["user_id"], now)
            for i in current["invitations"]:
                if i["email"] not in wanted[key]:
                    service.remove_invitation(db, actor, gid, i["email"], now)
    except DirectoryError as exc:
        db.rollback()
        raise HTTPException(status_code=422 if exc.detail == "last_admin" else exc.status, detail=exc.detail)
    version = R.bump(db, now)
    journal.record(db, "admin", "roles_changed", "success",
                   details={"by": admin, "admins": len(wanted["admins"]), "pco": len(wanted["pco"]), "groups": True})
    db.commit()
    await broadcast_all({"type": "rights.changed", "data": {"version": version, "revocation": True,
                                                            "reason": "roles_changed"}})
    return {"admins": _emails(db, bridge[0]), "pco": _emails(db, bridge[1]), "env_admins": roles.env_admins(),
            "groups": {"admins": bridge[0], "pco": bridge[1]}}

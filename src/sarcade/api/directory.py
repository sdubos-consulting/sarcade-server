# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Administration of the directory: organisation groups, members (address,
CSV import), invitations, habilitations, effective rights and « Pourquoi ce
droit ? », licences of the organisations (lot 26). Contract:
docs/directory-v0.1.md. The server is the judge of every call."""
from __future__ import annotations

from datetime import UTC, datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade import accounts, roles
from sarcade.accounts.caller import caller_of
from sarcade.db.models import OrgGroupUserRow, OrgInvitationRow, UserRow
from sarcade.directory import DirectoryError, catalogue, graph as graphs, licences, rights as R, service
from sarcade.realtime.manager import broadcast_all
from sarcade.security import journal

from .authz import ADMIN_TOKEN_ACTOR, admin_token_used, client_ip
from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
MAX_CSV_BYTES = 256 * 1024
MAX_LICENCE_BYTES = 64 * 1024


def actor_of(request: Request, db: Session = Depends(get_db)) -> R.Actor:
    """The administrator calling (any administration right), else 401/403."""
    if admin_token_used(request):
        return R.Actor(label=ADMIN_TOKEN_ACTOR, token=True)
    caller = caller_of(request)
    if caller.email is None:
        raise HTTPException(status_code=401, detail="account_required")
    rights = R.of(db, caller.user_id, caller.email)
    # Lists of 28 October and SARCADE_ADMIN_EMAILS: administrators of the server.
    legacy = not rights.server_admin and "admin" in roles.roles_of(db, caller.email)
    actor = R.Actor(label=caller.email, rights=rights, token=legacy)
    if not (actor.server_admin or rights.can_administer):
        journal.record(db, "rights", "admin_required", "refused", actor=caller.email, source_ip=client_ip(request),
                       details={"path": request.url.path[:120]})
        db.commit()
        raise HTTPException(status_code=403, detail="admin_required")
    return actor


def _fail(db: Session, request: Request, actor: R.Actor, exc: DirectoryError):
    db.rollback()
    journal.record(db, "rights", "directory_refused", "refused", actor=actor.label, source_ip=client_ip(request),
                   details={"path": request.url.path[:120], "method": request.method, "reason": exc.detail,
                            "chain": exc.extra.get("path")})
    db.commit()
    detail = {"error": exc.detail, **exc.extra} if exc.extra else exc.detail
    raise HTTPException(status_code=exc.status, detail=detail)


async def _done(db: Session, change: service.Change | None):
    db.commit()
    if change is not None and change.reason != "unchanged":
        await broadcast_all({"type": "rights.changed", "data": {
            "version": change.version, "revocation": change.revocation, "reason": change.reason}})


def _visible(db: Session, actor: R.Actor, gid: str) -> graphs.Graph:
    g = graphs.load(db)
    if gid not in g.groups:
        raise HTTPException(status_code=404, detail="group_not_found")
    if gid not in R.visible_groups(g, actor):
        raise HTTPException(status_code=403, detail="group_not_visible")
    return g


# --- Catalogue and groups ----------------------------------------------------------------

@router.get("/admin/directory/catalogue")
def rights_catalogue(_actor: R.Actor = Depends(actor_of)):
    return {"rights": catalogue(), "max_depth": 8}


@router.get("/admin/groups")
def list_groups(actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    """Groups seen by this administrator (no other organisation), sorted
    by organisation then name."""
    g = graphs.load(db)
    visible = R.visible_groups(g, actor)
    c = service.counts(db)
    rows = [service.group_dict(g, g.groups[x], c) for x in visible]
    rows.sort(key=lambda d: (not d["system"], g.name(d["organization_id"] or d["id"]).lower(),
                             len(g.ancestors(d["id"])), d["short_name"].lower()))
    for d in rows:
        d["can_admin"] = R.can_admin_group(g, actor, d["id"])
        d["can_manage_members"] = R.can_manage_members(g, actor, d["id"])
    return rows


class GroupIn(BaseModel):
    short_name: str = Field(max_length=40)
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=9)
    parent_id: str | None = Field(default=None, max_length=64)
    member_of_parent: bool = True


@router.post("/admin/groups", status_code=201)
async def create_group(body: GroupIn, request: Request, actor: R.Actor = Depends(actor_of),
                       db: Session = Depends(get_db)):
    try:
        out, change = service.create_group(db, actor, body.model_dump(), datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


@router.get("/admin/groups/{gid}")
def get_group(gid: str, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    g = _visible(db, actor, gid)
    out = service.group_dict(g, g.groups[gid], service.counts(db))
    out.update(service.members(db, gid), habilitations=service.habilitations(db, gid),
               can_admin=R.can_admin_group(g, actor, gid), can_manage_members=R.can_manage_members(g, actor, gid),
               ancestors=[{"id": a, "short_name": g.name(a)} for a in g.ancestors(gid)])
    return out


@router.patch("/admin/groups/{gid}")
async def update_group(gid: str, body: dict, request: Request, actor: R.Actor = Depends(actor_of),
                       db: Session = Depends(get_db)):
    allowed = {"short_name", "name", "description", "color", "parent_id"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise HTTPException(status_code=422, detail="unknown_field")
    try:
        out, change = service.update_group(db, actor, gid, body, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


@router.delete("/admin/groups/{gid}")
async def delete_group(gid: str, request: Request, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    try:
        change = service.delete_group(db, actor, gid, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return {"deleted": True}


# --- Members ---------------------------------------------------------------------------------

@router.get("/admin/groups/{gid}/members")
def list_members(gid: str, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    _visible(db, actor, gid)
    return service.members(db, gid)


class MemberIn(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    group_id: str | None = Field(default=None, max_length=64)


@router.post("/admin/groups/{gid}/members", status_code=201)
async def add_member(gid: str, body: MemberIn, request: Request, actor: R.Actor = Depends(actor_of),
                     db: Session = Depends(get_db)):
    """A person by address (member, or invitation before the account) or a
    group (nested; refused with the path if it would make a cycle)."""
    if (body.email is None) == (body.group_id is None):
        raise HTTPException(status_code=422, detail="email_or_group_id")
    now = datetime.now(UTC)
    try:
        if body.email is not None:
            out, change = service.add_email(db, actor, gid, body.email, now)
        else:
            out, change = service.add_group_member(db, actor, gid, body.group_id, now)
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


@router.post("/admin/groups/{gid}/members/import")
async def import_members(gid: str, request: Request, actor: R.Actor = Depends(actor_of),
                         db: Session = Depends(get_db)):
    """CSV list of addresses (one per line), as the body (``text/csv``) or
    in JSON ``{"csv": "…"}``."""
    raw = await request.body()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="csv_too_large")
    text = raw.decode("utf-8-sig", errors="replace")
    if "json" in (request.headers.get("content-type") or ""):
        try:
            text = str(json.loads(text).get("csv") or "")
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="invalid_csv") from None
    try:
        out, change = service.import_addresses(db, actor, gid, text, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


@router.delete("/admin/groups/{gid}/members/users/{user_id}")
async def remove_user(gid: str, user_id: str, request: Request, actor: R.Actor = Depends(actor_of),
                      db: Session = Depends(get_db)):
    try:
        change = service.remove_user(db, actor, gid, user_id, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return {"removed": True}


@router.delete("/admin/groups/{gid}/members/groups/{member_id}")
async def remove_group(gid: str, member_id: str, request: Request, actor: R.Actor = Depends(actor_of),
                       db: Session = Depends(get_db)):
    try:
        change = service.remove_group_member(db, actor, gid, member_id, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return {"removed": True}


@router.delete("/admin/groups/{gid}/invitations/{email}")
async def remove_invitation(gid: str, email: str, request: Request, actor: R.Actor = Depends(actor_of),
                            db: Session = Depends(get_db)):
    try:
        service.remove_invitation(db, actor, gid, email, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    db.commit()
    return {"removed": True}


@router.get("/admin/invitations")
def list_invitations(actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    g = graphs.load(db)
    visible = R.visible_groups(g, actor)
    rows = db.scalars(select(OrgInvitationRow).order_by(OrgInvitationRow.email)).all()
    return [{"group_id": r.group_id, "short_name": g.name(r.group_id), "email": r.email,
             "invited_at": R.iso(r.invited_at), "invited_by": r.invited_by} for r in rows if r.group_id in visible]


# --- Habilitations ---------------------------------------------------------------------------

@router.get("/admin/groups/{gid}/habilitations")
def list_habilitations(gid: str, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    _visible(db, actor, gid)
    return service.habilitations(db, gid)


class HabilitationIn(BaseModel):
    right: str = Field(max_length=40)
    scope_group_id: str | None = Field(default=None, max_length=64)
    until: datetime | None = None


@router.post("/admin/groups/{gid}/habilitations", status_code=201)
async def grant(gid: str, body: HabilitationIn, request: Request, actor: R.Actor = Depends(actor_of),
                db: Session = Depends(get_db)):
    try:
        out, change = service.grant(db, actor, gid, body.model_dump(), datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


class UntilIn(BaseModel):
    until: datetime | None = None


@router.patch("/admin/habilitations/{hid}")
async def set_until(hid: str, body: UntilIn, request: Request, actor: R.Actor = Depends(actor_of),
                    db: Session = Depends(get_db)):
    try:
        out, change = service.set_until(db, actor, hid, body.until, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


@router.delete("/admin/habilitations/{hid}")
async def revoke(hid: str, request: Request, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    try:
        change = service.revoke(db, actor, hid, datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return {"revoked": True}


# --- Effective rights: « Pourquoi ce droit ? » -----------------------------------------------

def _explain_for(db: Session, actor: R.Actor, user: UserRow | None, email: str) -> dict:
    rights = R.of(db, user.id if user else None, email)
    g = graphs.load(db)
    if not R.can_audit(g, actor, None):
        visible = R.visible_groups(g, actor)
        if not (set(rights.reached) - {"public"}) & visible:
            raise HTTPException(status_code=403, detail="user_not_visible")
    out = R.explain(db, rights)
    out["account"] = accounts.user_dict(user) if user else None
    if user is None:
        out["invitations"] = [r.group_id for r in db.scalars(select(OrgInvitationRow).where(
            OrgInvitationRow.email == email))]
    return out


@router.get("/admin/users/{user_id}/rights")
def user_rights(user_id: str, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    user = db.get(UserRow, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return _explain_for(db, actor, user, user.email)


@router.get("/admin/rights")
def rights_by_email(email: str = Query(max_length=320), actor: R.Actor = Depends(actor_of),
                    db: Session = Depends(get_db)):
    try:
        email = accounts.normal_email(email)
    except accounts.AccountError:
        raise HTTPException(status_code=422, detail="invalid_email") from None
    return _explain_for(db, actor, accounts.user_by_email(db, email), email)


@router.get("/admin/users/{user_id}/groups")
def user_groups(user_id: str, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    """Direct groups of an account (those the administrator sees)."""
    g = graphs.load(db)
    visible = R.visible_groups(g, actor)
    ids = db.scalars(select(OrgGroupUserRow.group_id).where(OrgGroupUserRow.user_id == user_id)).all()
    return [{"id": x, "short_name": g.name(x)} for x in ids if x in visible]


class UserPatch(BaseModel):
    status: str | None = Field(default=None, max_length=16)
    display_name: str | None = Field(default=None, max_length=120)


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: str, body: UserPatch, request: Request, actor: R.Actor = Depends(actor_of),
                      db: Session = Depends(get_db)):
    try:
        out, change = service.set_user_status(db, actor, user_id, body.model_dump(exclude_unset=True),
                                              datetime.now(UTC))
    except DirectoryError as exc:
        _fail(db, request, actor, exc)
    await _done(db, change)
    return out


# --- Licence of an organisation -----------------------------------------------------------------

def _organization(db: Session, actor: R.Actor, gid: str) -> graphs.Graph:
    g = _visible(db, actor, gid)
    row = g.groups[gid]
    if row.parent_id is not None or row.system:
        raise HTTPException(status_code=422, detail="not_an_organization")
    return g


@router.get("/admin/groups/{gid}/licence")
def get_org_licence(gid: str, actor: R.Actor = Depends(actor_of), db: Session = Depends(get_db)):
    g = _organization(db, actor, gid)
    doc = licences.documents(db).get(gid)
    covered = licences.modules_by_organization(db, g).get(gid, set())
    return {"organization_id": gid, "licence": licences.state_of(doc).as_dict() if doc else None,
            "modules": sorted(covered)}


@router.put("/admin/groups/{gid}/licence")
async def put_org_licence(gid: str, request: Request, actor: R.Actor = Depends(actor_of),
                          db: Session = Depends(get_db)):
    """Signed licence file (JSON) of the organisation, checked offline;
    administrator of the server only."""
    _organization(db, actor, gid)
    if not actor.server_admin:
        raise HTTPException(status_code=403, detail="server_admin_required")
    body = await request.body()
    if len(body) > MAX_LICENCE_BYTES:
        raise HTTPException(status_code=413, detail="licence_too_large")
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        document = None
    state = licences.install(db, gid, document) if isinstance(document, dict) else licences.state_of({"format": "?"})
    lic = state.licence or {}
    ok = state.status in licences.ACTIVE
    journal.record(db, "admin", "organization_licence_uploaded", "success" if ok else "refused", actor=actor.label,
                   source_ip=client_ip(request),
                   details={"group_id": gid, "licence_id": lic.get("id"), "status": state.status,
                            "reason": state.reason, "modules": ",".join(sorted(state.modules))})
    if not ok:
        db.commit()
        raise HTTPException(status_code=422, detail={"error": "licence_refused", "status": state.status,
                                                     "reason": state.reason})
    change = service.Change(R.bump(db), False, "licence")
    await _done(db, change)
    return state.as_dict()


@router.delete("/admin/groups/{gid}/licence")
async def delete_org_licence(gid: str, request: Request, actor: R.Actor = Depends(actor_of),
                             db: Session = Depends(get_db)):
    _organization(db, actor, gid)
    if not actor.server_admin:
        raise HTTPException(status_code=403, detail="server_admin_required")
    if not licences.remove(db, gid):
        raise HTTPException(status_code=404, detail="no_licence")
    journal.record(db, "admin", "organization_licence_removed", actor=actor.label, source_ip=client_ip(request),
                   details={"group_id": gid})
    await _done(db, service.Change(R.bump(db), True, "licence"))
    return {"removed": True}

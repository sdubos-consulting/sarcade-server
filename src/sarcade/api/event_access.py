# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Events and the directory (lot 26): visibility of every event route,
organising and invited groups, communication group made from an
organisation group, transition period. Contract: docs/directory-v0.1.md."""
from __future__ import annotations

from datetime import UTC, datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocketException
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from sarcade import roles
from sarcade.accounts.caller import Caller, caller_of, resolve
from sarcade.db.models import EventRow, LogbookRow
from sarcade.directory import DirectoryError, events as dir_events, graph as graphs, rights as R, transition
from sarcade.groups import service as comm_groups
from sarcade.realtime.manager import broadcast_all, manager
from sarcade.security import journal

from .authz import client_ip, require_server_admin
from .deps import get_db

router = APIRouter(prefix="/api/v0.1")
_EXEMPT = ("/api/v0.1/tv/",)


def guard_event(conn: HTTPConnection, caller: Caller = Depends(resolve), db: Session = Depends(get_db)) -> None:
    """Application dependency: a route of an event the caller does not see
    answers « not found » (the server does not tell it exists)."""
    event_id = conn.path_params.get("event_id")
    if not roles.directory_ready(db):
        return
    try:
        transition.refresh(db)
        if not event_id or conn.url.path.startswith(_EXEMPT):
            return
        event = db.get(EventRow, event_id)
        if event is None or dir_events.visible(db, caller, event):
            return
    except OperationalError:
        # Database unreachable: the route fails on its own (the realtime
        # channel carries nothing without it).
        db.rollback()
        return
    if conn.scope["type"] == "websocket":
        raise WebSocketException(code=1008, reason="event_not_found")
    raise HTTPException(status_code=404, detail="event_not_found")


def _event(db: Session, event_id: str) -> EventRow:
    row = db.get(EventRow, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="event_not_found")
    return row


def _directory_error(exc: DirectoryError):
    detail = {"error": exc.detail, **exc.extra} if exc.extra else exc.detail
    raise HTTPException(status_code=exc.status, detail=detail)


# --- Creation of an event -----------------------------------------------------------------------

def organizer_for_new_event(db: Session, request: Request, asked: str | None) -> str | None:
    """Organising group of a new event: the one asked, else the only group
    the caller organises, else the default group of the server (ADRASEC 78
    after the migration). The caller must organise it."""
    if not roles.directory_ready(db):
        return asked
    caller = caller_of(request)
    gid = asked
    if gid is None and caller.email is not None:
        actor = dir_events.actor_for(db, caller)
        if not actor.server_admin:
            g = graphs.load(db)
            mine = [x for x in g.groups if not g.groups[x].system and R.can_organize(g, actor, x)]
            own = [x for x in mine if x in set(actor.scopes("event_organizer", "group_admin"))]
            if len(own) == 1:
                gid = own[0]
    gid = gid or dir_events.default_organizer(db)
    if gid is None:
        return None
    try:
        dir_events.check_organizer(db, caller, gid)
    except DirectoryError as exc:
        journal.record(db, "rights", "event_create_refused", "refused", actor=caller.email,
                       source_ip=client_ip(request), details={"organizer_group_id": gid, "reason": exc.detail})
        db.commit()
        _directory_error(exc)
    return gid


def invite_at_creation(db: Session, request: Request, event: EventRow, group_ids: list[str] | None,
                       now: datetime) -> None:
    if not group_ids or not roles.directory_ready(db):
        return
    try:
        dir_events.set_invited(db, event, group_ids, caller_of(request).email or "PCO", now)
    except DirectoryError as exc:
        db.rollback()
        _directory_error(exc)


# --- Synchronisation --------------------------------------------------------------------------------

class SyncAccess:
    """Checks of a synchronisation batch, computed once per event: event
    visible to the caller, PCO of this event, module ADRASEC usable for
    bearings and listening points (after the transition)."""
    MODULE_KINDS = {"bearing": "adrasec", "listening": "adrasec"}

    def __init__(self, db: Session, request: Request):
        self.db, self.caller = db, caller_of(request)
        self._events: dict[str, EventRow | None] = {}
        self._visible: dict[str, bool] = {}
        self._pco: dict[str, bool] = {}
        self._modules: dict[tuple[str, str], bool] = {}

    def _event(self, event_id: str) -> EventRow | None:
        if event_id not in self._events:
            self._events[event_id] = self.db.get(EventRow, event_id)
        return self._events[event_id]

    def refusal(self, op) -> str | None:
        event = self._event(op.event_id)
        if event is None:
            return None
        if op.event_id not in self._visible:
            self._visible[op.event_id] = dir_events.visible(self.db, self.caller, event)
        if not self._visible[op.event_id]:
            return "événement non visible pour ce compte"
        kind = (op.payload or {}).get("kind") if isinstance(op.payload, dict) else None
        module = self.MODULE_KINDS.get(kind) if op.object_type == "map_feature" and op.action != "delete" else None
        if module:
            key = (op.event_id, module)
            if key not in self._modules:
                self._modules[key] = dir_events.module_allowed(self.db, self.caller, event, module)
            if not self._modules[key]:
                return "module ADRASEC non autorisé pour ce compte"
        return None

    def pco(self, event_id: str, default: bool) -> bool:
        event = self._event(event_id)
        if event is None or not event.organizer_group_id or self.caller.email is None:
            return default
        if event_id not in self._pco:
            from sarcade import roles as roles_mod
            self._pco[event_id] = dir_events.can_organize(self.db, self.caller, event) or \
                self.caller.email in roles_mod.stored(self.db)["pco"]
        return self._pco[event_id]


# --- Organiser and invited groups ------------------------------------------------------------

@router.get("/events/{event_id}/access")
def get_access(event_id: str, request: Request, db: Session = Depends(get_db)):
    return dir_events.access_dict(db, _event(db, event_id), caller_of(request))


class AccessIn(BaseModel):
    organizer_group_id: str | None = Field(default=None, max_length=64)
    invited_group_ids: list[str] | None = Field(default=None, max_length=200)


@router.put("/events/{event_id}/access")
async def put_access(event_id: str, body: AccessIn, request: Request, db: Session = Depends(get_db)):
    """Organiser of the event (PCO) or administrator: who organises it and
    which groups are invited (they see it)."""
    caller = caller_of(request)
    event = _event(db, event_id)
    if not (dir_events.can_organize(db, caller, event) or (caller.email is None and transition.active())):
        raise HTTPException(status_code=403 if caller.email else 401, detail="event_organizer_required")
    now = datetime.now(UTC)
    by = caller.email or "PCO"
    try:
        if body.organizer_group_id and body.organizer_group_id != event.organizer_group_id:
            dir_events.check_organizer(db, caller, body.organizer_group_id)
            before = event.organizer_group_id
            event.organizer_group_id = body.organizer_group_id
            journal.record(db, "rights", "event_organizer_changed", actor=by, event_id=event_id,
                           source_ip=client_ip(request), details={"before": before, "after": body.organizer_group_id})
        added, removed = [], []
        if body.invited_group_ids is not None:
            added, removed = dir_events.set_invited(db, event, body.invited_group_ids, by, now)
            if added or removed:
                journal.record(db, "rights", "event_invitations_changed", actor=by, event_id=event_id,
                               source_ip=client_ip(request), details={"added": added, "removed": removed})
    except DirectoryError as exc:
        db.rollback()
        _directory_error(exc)
    g = graphs.load(db)
    names = lambda ids: ", ".join(g.name(x) for x in ids)  # noqa: E731
    if added:
        db.add(LogbookRow(event_id=event_id, kind="settings", object_id=event_id, actor_id=by[:64], time=now,
                          summary=f"Groupes invités : {names(added)}"))
    if removed:
        db.add(LogbookRow(event_id=event_id, kind="settings", object_id=event_id, actor_id=by[:64], time=now,
                          summary=f"Groupes retirés de l'événement : {names(removed)}"))
    event.version = (event.version or 0) + 1
    db.commit()
    data = dir_events.access_dict(db, event)
    await manager.broadcast(event_id, {"type": "event.access.updated", "data": dict(data, revocation=bool(removed))})
    return dir_events.access_dict(db, event, caller)


@router.get("/directory/groups")
def directory_groups(request: Request, db: Session = Depends(get_db)):
    """Light list of the groups (to invite them to an event, to make a
    communication group): organisers and administrators only."""
    caller = caller_of(request)
    if caller.email is None:
        raise HTTPException(status_code=401, detail="account_required")
    actor = dir_events.actor_for(db, caller)
    if not (actor.server_admin or (actor.rights and actor.rights.can_administer)):
        raise HTTPException(status_code=403, detail="event_organizer_required")
    g = graphs.load(db)
    rows = [{"id": x.id, "short_name": x.short_name, "name": x.name, "parent_id": x.parent_id, "system": x.system,
             "organization_id": None if x.system else g.organization_of(x.id), "color": x.color,
             "can_organize": R.can_organize(g, actor, x.id) and not x.system}
            for x in g.groups.values()]
    rows.sort(key=lambda d: (not d["system"], g.name(d["organization_id"] or d["id"]).lower(),
                             len(g.ancestors(d["id"])), d["short_name"].lower()))
    return rows


# --- Communication group made from an organisation group -------------------------------------

class FromDirectoryIn(BaseModel):
    group_id: str = Field(max_length=64)
    name: str | None = Field(default=None, max_length=comm_groups.MAX_NAME)
    mode: str = Field(default="discussion", max_length=16)


@router.post("/events/{event_id}/groups/from-directory", status_code=201)
async def comm_group_from_directory(event_id: str, body: FromDirectoryIn, request: Request,
                                    db: Session = Depends(get_db)):
    """PCO of the event: a communication group with the members of the
    moment of an organisation group (their enrolled devices); the two stay
    independent afterwards."""
    from .groups import ensure_groups, group_dict
    caller = caller_of(request)
    event = _event(db, event_id)
    if event.ended_at is not None:
        raise HTTPException(status_code=409, detail="event_closed")
    if not (dir_events.can_organize(db, caller, event) or (caller.email is None and transition.active())):
        raise HTTPException(status_code=403 if caller.email else 401, detail="event_organizer_required")
    if body.mode not in comm_groups.MODES:
        raise HTTPException(status_code=422, detail="invalid_mode")
    g = graphs.load(db)
    source = g.groups.get(body.group_id)
    if source is None or source.id == "public":
        raise HTTPException(status_code=422, detail="invalid_group")
    devices, people = dir_events.members_devices(db, source.id)
    devices = devices[:comm_groups.MAX_MEMBERS]
    now = datetime.now(UTC)
    by = (caller.email or "PCO")[:64]
    name = (body.name or source.short_name).strip()[:comm_groups.MAX_NAME] or source.short_name
    gid = str(uuid.uuid4())
    item = {"id": gid, "event_id": event_id, "name": name, "description": f"Membres de {source.name}"[:300],
            "color": 0xFF546E7A, "mode": body.mode, "kind": "custom", "members": devices, "roles": [],
            "senders": [], "managers": [by], "team_id": None, "radio_channel": "", "archived": False,
            "source_org_group_id": source.id, "created_by": by, "updated_by": by, "updated_at": now}
    row = ensure_groups(db, event_id, [item], now)[0]
    db.add(LogbookRow(event_id=event_id, kind="group", object_id=gid, actor_id=by, time=now,
                      summary=f"Groupe « {name} » créé à partir de {source.short_name} ({len(devices)} appareils)"))
    journal.record(db, "rights", "comm_group_from_directory", actor=by, event_id=event_id,
                   source_ip=client_ip(request),
                   details={"org_group_id": source.id, "devices": len(devices), "people": people})
    db.commit()
    data = group_dict(row)
    await manager.broadcast(event_id, {"type": "group.upserted", "data": data})
    return dict(data, people=people, devices=len(devices))


# --- Transition ---------------------------------------------------------------------------------

@router.get("/transition")
def get_transition(db: Session = Depends(get_db)):
    return transition.state(db)


class TransitionIn(BaseModel):
    until: datetime | None = None


@router.put("/admin/transition")
async def put_transition(body: TransitionIn, request: Request, admin: str = Depends(require_server_admin),
                         db: Session = Depends(get_db)):
    """End of the transition, chosen by the administrator (None: no end)."""
    until = body.until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    before = transition.state(db)
    transition.set_until(db, until, admin)
    after = transition.state(db)
    journal.record(db, "admin", "transition_changed", actor=admin, source_ip=client_ip(request),
                   details={"before": before["until"], "after": after["until"], "active": after["active"]})
    R.bump(db)
    db.commit()
    await broadcast_all({"type": "transition.changed", "data": after})
    return after

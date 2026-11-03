# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Events and the directory (lot 26, decision 10 of 23 Oct 2026): an event
belongs to an organising group and is visible to the invited groups only
(the organising group is always invited), plus its organisers (PCO) and the
administrators of the server. Inviting « Public » opens it to everybody.
During the transition a device without account still sees every event."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import AuthDeviceRow, EventInvitedGroupRow, EventRow, OrgGroupUserRow, UserRow
from sarcade.updates.service import get_value

from . import PUBLIC_ID, DirectoryError, graph as graphs, licences, rights as R, transition

SETTINGS_KEY = "directory"


def default_organizer(db: Session) -> str | None:
    v = get_value(db, SETTINGS_KEY, {}) or {}
    gid = v.get("default_organizer_group_id") if isinstance(v, dict) else None
    return gid


def invited(db: Session, event_id: str) -> list[str]:
    return list(db.scalars(select(EventInvitedGroupRow.group_id).where(EventInvitedGroupRow.event_id == event_id)
                           .order_by(EventInvitedGroupRow.invited_at, EventInvitedGroupRow.group_id)))


def audience(db: Session, event: EventRow) -> set[str]:
    out = set(invited(db, event.id))
    if event.organizer_group_id:
        out.add(event.organizer_group_id)
    return out


def actor_for(db: Session, caller) -> R.Actor:
    """Rights of a caller (sarcade.accounts.caller.Caller) as an actor."""
    from sarcade import roles
    rights = R.of(db, caller.user_id, caller.email)
    legacy = bool(caller.email) and not rights.server_admin and "admin" in roles.roles_of(db, caller.email)
    return R.Actor(label=caller.email or "appareil sans compte", rights=rights, token=legacy)


def can_organize(db: Session, caller, event: EventRow, g: graphs.Graph | None = None) -> bool:
    """PCO of the event: organiser of its group (or administrator of the
    group or of the server)."""
    if caller.email is None:
        return False
    actor = actor_for(db, caller)
    if actor.server_admin:
        return True
    if not event.organizer_group_id:
        return False
    g = g or graphs.load(db)
    return R.can_organize(g, actor, event.organizer_group_id)


def visible(db: Session, caller, event: EventRow, now: datetime | None = None) -> bool:
    if caller.email is None and transition.active(now):
        return True  # device without account during the transition: as before
    groups = audience(db, event)
    if not event.organizer_group_id and not groups:
        return True  # event outside the directory (created before it)
    if PUBLIC_ID in groups:
        return True
    rights = R.of(db, caller.user_id, caller.email) if caller.email else R.of(db, None, None)
    if set(rights.reached) & groups:
        return True
    return can_organize(db, caller, event)


def check_organizer(db: Session, caller, gid: str) -> None:
    """The caller may organise events of [gid]."""
    g = graphs.load(db)
    if gid not in g.groups or gid == PUBLIC_ID or g.groups[gid].system:
        raise DirectoryError(422, "invalid_organizer_group")
    if caller.email is None:
        if transition.active():
            return  # device without account during the transition (PCO by its name)
        raise DirectoryError(403, "pco_required")
    if not R.can_organize(g, actor_for(db, caller), gid):
        raise DirectoryError(403, "event_organizer_required")


def set_invited(db: Session, event: EventRow, group_ids: list[str], by: str, now: datetime) -> tuple[list, list]:
    """Replaces the invited groups; returns (added, removed)."""
    g = graphs.load(db)
    wanted = []
    for gid in group_ids:
        if gid not in g.groups:
            raise DirectoryError(404, "group_not_found", group_id=gid)
        if gid not in wanted:
            wanted.append(gid)
    current = invited(db, event.id)
    added = [x for x in wanted if x not in current]
    removed = [x for x in current if x not in wanted]
    for gid in removed:
        db.delete(db.get(EventInvitedGroupRow, (event.id, gid)))
    for i, gid in enumerate(added):
        # Staggered by a microsecond: the list keeps the order given.
        db.add(EventInvitedGroupRow(event_id=event.id, group_id=gid, invited_at=now + timedelta(microseconds=i),
                                    invited_by=by[:64]))
    db.flush()
    return added, removed


def access_dict(db: Session, event: EventRow, caller=None) -> dict:
    g = graphs.load(db)

    def item(gid):
        row = g.groups.get(gid)
        return {"id": gid, "short_name": row.short_name if row else gid, "name": row.name if row else None}
    out = {"event_id": event.id, "organizer_group": item(event.organizer_group_id) if event.organizer_group_id else None,
           "invited_groups": [item(x) for x in invited(db, event.id)],
           "organization_id": g.organization_of(event.organizer_group_id) if event.organizer_group_id in g.groups
           else None}
    if caller is not None:
        out["can_organize"] = can_organize(db, caller, event, g) or (caller.email is None and transition.active())
    return out


def organization_modules(db: Session, event: EventRow) -> set[str] | None:
    """Modules covered by the licence of the organising organisation, or
    None when the event has no organiser."""
    if not event.organizer_group_id:
        return None
    g = graphs.load(db)
    if event.organizer_group_id not in g.groups:
        return None
    org = g.organization_of(event.organizer_group_id)
    return licences.modules_by_organization(db, g).get(org, set())


def module_allowed(db: Session, caller, event: EventRow, module: str, now: datetime | None = None) -> bool:
    """Using [module] in [event]: habilitation of one of the caller's groups
    AND licence of the organisation that organises the event (decision 7).
    Not checked during the transition (behaviour of before)."""
    if transition.active(now):
        return True
    rights = R.of(db, caller.user_id, caller.email) if caller.email else R.of(db, None, None)
    if module not in rights.module_habilitations():
        return False
    covered = organization_modules(db, event)
    return module in (covered if covered is not None else set(rights.modules()))


def members_devices(db: Session, gid: str) -> tuple[list[str], int]:
    """Declared ids of the enrolled devices of the members of [gid] (direct
    or through nested groups), and the number of members: what a
    communication group copies from an organisation group."""
    g = graphs.load(db)
    groups = {gid} | g.contained(gid)
    users = set(db.scalars(select(OrgGroupUserRow.user_id).where(OrgGroupUserRow.group_id.in_(list(groups)))))
    if not users:
        return [], 0
    active = set(db.scalars(select(UserRow.id).where(UserRow.id.in_(list(users)), UserRow.status == "active")))
    rows = db.scalars(select(AuthDeviceRow.device_id).where(
        AuthDeviceRow.user_id.in_(list(active)), AuthDeviceRow.revoked_at.is_(None),
        AuthDeviceRow.device_id.is_not(None)).order_by(AuthDeviceRow.created_at)).all()
    devices = []
    for d in rows:
        if d not in devices:
            devices.append(d)
    return devices, len(active)


def refuse_delete_with_events(db: Session, gid: str) -> None:
    n = db.scalar(select(EventRow.id).where(EventRow.organizer_group_id == gid).limit(1))
    if n is not None:
        raise DirectoryError(409, "group_has_events")
    db.query(EventInvitedGroupRow).filter(EventInvitedGroupRow.group_id == gid).delete()


def now() -> datetime:
    return datetime.now(UTC)


from . import service as _service  # noqa: E402

_service.before_group_delete.append(refuse_delete_with_events)

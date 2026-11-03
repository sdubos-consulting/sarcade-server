# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Changes of the directory, each one checked (rights of the author, no
elevation, no cycle, depth, Public, last administrator), journaled in the
security journal with the author, and followed by a new rights version.
The caller commits."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import io
import re
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sarcade import accounts
from sarcade.db.models import (HabilitationRow, OrgGroupGroupRow, OrgGroupRow, OrgGroupUserRow, OrgInvitationRow,
                               UserRow)
from sarcade.security import journal

from . import ADMIN_RIGHTS, MODULE_RIGHTS, PUBLIC_ID, SYSTEM_IDS, DirectoryError, graph as graphs, rights as R

_SHORT = re.compile(r"^[^\s].{0,39}$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_IMPORT = 2000

# Hooks called with (db, group_id) before a group is deleted (events
# organised by the group refuse the deletion: sarcade.directory.events).
before_group_delete: list = []


@dataclass
class Change:
    """What the API broadcasts after the commit (« rights.changed »)."""
    version: int
    revocation: bool
    reason: str


def _rec(db: Session, actor: R.Actor, action: str, outcome: str = "success", category: str = "rights", **details):
    journal.record(db, category, action, outcome, actor=actor.label, details=details)


def _changed(db: Session, now: datetime, revocation: bool, reason: str) -> Change:
    return Change(version=R.bump(db, now), revocation=revocation, reason=reason)


def _guard_last_admin(db: Session, actor: R.Actor, before: set[str], action: str) -> None:  # noqa: ARG001
    """Refuses a change that leaves no active administrator account."""
    if not before:
        return
    db.flush()
    if not R.active_admins(db):
        raise DirectoryError(409, "last_admin",
                             message="Impossible : il faut toujours au moins un administrateur actif")


def group_dict(g: graphs.Graph, row: OrgGroupRow, counts: dict | None = None) -> dict:
    counts = counts or {}
    org = g.organization_of(row.id)
    return {"id": row.id, "short_name": row.short_name, "name": row.name, "description": row.description,
            "color": row.color, "parent_id": row.parent_id, "system": row.system,
            "organization": row.parent_id is None and not row.system,
            "organization_id": None if row.system else org,
            "member_groups": sorted(g.contains.get(row.id, ())), "member_of": sorted(g.member_of.get(row.id, ())),
            "children": sorted(g.children(row.id)),
            "users": counts.get("users", {}).get(row.id, 0), "invitations": counts.get("invitations", {}).get(row.id, 0),
            "created_at": R.iso(row.created_at), "updated_at": R.iso(row.updated_at)}


def counts(db: Session) -> dict:
    users = dict(db.execute(select(OrgGroupUserRow.group_id, func.count()).group_by(OrgGroupUserRow.group_id)).all())
    inv = dict(db.execute(select(OrgInvitationRow.group_id, func.count()).group_by(OrgInvitationRow.group_id)).all())
    return {"users": users, "invitations": inv}


def _clean_text(value, field: str, max_len: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise DirectoryError(422, f"invalid_{field}")
        return None
    if not isinstance(value, str):
        raise DirectoryError(422, f"invalid_{field}")
    value = value.strip()
    if (required and not value) or len(value) > max_len:
        raise DirectoryError(422, f"invalid_{field}")
    return value or None


def _group(g: graphs.Graph, gid: str) -> OrgGroupRow:
    row = g.groups.get(gid)
    if row is None:
        raise DirectoryError(404, "group_not_found")
    return row


def _short_name_free(db: Session, short: str, except_id: str | None = None) -> None:
    other = db.scalar(select(OrgGroupRow).where(func.lower(OrgGroupRow.short_name) == short.lower()))
    if other is not None and other.id != except_id:
        raise DirectoryError(409, "short_name_taken")


# --- Groups ---------------------------------------------------------------------------------

def create_group(db: Session, actor: R.Actor, data: dict, now: datetime) -> tuple[dict, Change]:
    g = graphs.load(db)
    short = _clean_text(data.get("short_name"), "short_name", 40, required=True)
    name = _clean_text(data.get("name"), "name", 160) or short
    description = _clean_text(data.get("description"), "description", 2000)
    color = data.get("color")
    if color is not None and (not isinstance(color, str) or not _COLOR.match(color)):
        raise DirectoryError(422, "invalid_color")
    parent = data.get("parent_id") or None
    if parent is None:
        if not actor.server_admin:
            raise DirectoryError(403, "server_admin_required")  # new organisation
    else:
        _group(g, parent)
        if parent == PUBLIC_ID:
            raise DirectoryError(422, "public_not_nested")
        if not can_admin(g, actor, parent):
            raise DirectoryError(403, "group_admin_required")
    _short_name_free(db, short)
    row = OrgGroupRow(id=str(uuid.uuid4()), short_name=short, name=name, description=description, color=color,
                      parent_id=parent, system=False, created_at=now, created_by=actor.label[:64], updated_at=now,
                      updated_by=actor.label[:64])
    g.groups[row.id] = row
    g.parent[row.id] = parent
    if parent is not None:
        g.check_parent(row.id, parent)
    db.add(row)
    db.flush()
    member = parent is not None and data.get("member_of_parent", True) is not False
    if member:
        g.check_membership(parent, row.id)
        db.add(OrgGroupGroupRow(group_id=parent, member_id=row.id, added_at=now, added_by=actor.label[:64]))
        g.contains.setdefault(parent, set()).add(row.id)
        g.member_of.setdefault(row.id, set()).add(parent)
    _rec(db, actor, "group_created", category="admin", group_id=row.id, short_name=short, parent_id=parent,
         member_of_parent=member)
    change = _changed(db, now, False, "group_created")
    return group_dict(g, row), change


def can_admin(g: graphs.Graph, actor: R.Actor, gid: str) -> bool:
    return R.can_admin_group(g, actor, gid)


def update_group(db: Session, actor: R.Actor, gid: str, data: dict, now: datetime) -> tuple[dict, Change]:
    g = graphs.load(db)
    row = _group(g, gid)
    if not can_admin(g, actor, gid):
        raise DirectoryError(403, "group_admin_required")
    changed: list[str] = []
    if "short_name" in data:
        if row.id in SYSTEM_IDS:
            raise DirectoryError(422, "system_group")
        short = _clean_text(data["short_name"], "short_name", 40, required=True)
        if short != row.short_name:
            _short_name_free(db, short, row.id)
            row.short_name = short
            changed.append("short_name")
    for key, max_len in (("name", 160), ("description", 2000)):
        if key in data:
            value = _clean_text(data[key], key, max_len, required=key == "name")
            if value != getattr(row, key):
                setattr(row, key, value)
                changed.append(key)
    if "color" in data:
        color = data["color"]
        if color is not None and (not isinstance(color, str) or not _COLOR.match(color)):
            raise DirectoryError(422, "invalid_color")
        if color != row.color:
            row.color = color
            changed.append("color")
    if "parent_id" in data:
        parent = data["parent_id"] or None
        if parent != row.parent_id:
            if row.system:
                raise DirectoryError(422, "system_group")
            if parent is None:
                if not actor.server_admin:
                    raise DirectoryError(403, "server_admin_required")
            else:
                _group(g, parent)
                if not can_admin(g, actor, parent):
                    raise DirectoryError(403, "group_admin_required")
            g.check_parent(gid, parent)
            row.parent_id = parent
            changed.append("parent_id")
    if not changed:
        return group_dict(g, row, counts(db)), Change(R.version(db), False, "unchanged")
    row.updated_at, row.updated_by = now, actor.label[:64]
    _rec(db, actor, "group_updated", category="admin", group_id=gid, short_name=row.short_name, fields=changed)
    change = _changed(db, now, "parent_id" in changed, "group_updated")
    return group_dict(graphs.load(db), row, counts(db)), change


def delete_group(db: Session, actor: R.Actor, gid: str, now: datetime) -> Change:
    g = graphs.load(db)
    row = _group(g, gid)
    if row.system:
        raise DirectoryError(422, "system_group")
    if not can_admin(g, actor, gid) or (row.parent_id is None and not actor.server_admin):
        raise DirectoryError(403, "group_admin_required")
    if g.children(gid):
        raise DirectoryError(409, "group_has_subgroups")
    for hook in before_group_delete:
        hook(db, gid)
    before = R.active_admins(db)
    db.execute(delete(OrgGroupUserRow).where(OrgGroupUserRow.group_id == gid))
    db.execute(delete(OrgGroupGroupRow).where((OrgGroupGroupRow.group_id == gid) | (OrgGroupGroupRow.member_id == gid)))
    db.execute(delete(OrgInvitationRow).where(OrgInvitationRow.group_id == gid))
    db.execute(delete(HabilitationRow).where((HabilitationRow.group_id == gid) | (HabilitationRow.scope_group_id == gid)))
    db.delete(row)
    _guard_last_admin(db, actor, before, "group_deleted")
    _rec(db, actor, "group_deleted", category="admin", group_id=gid, short_name=row.short_name)
    from . import licences
    licences.remove(db, gid)
    return _changed(db, now, True, "group_deleted")


# --- Members ----------------------------------------------------------------------------------

def members(db: Session, gid: str) -> dict:
    g = graphs.load(db)
    _group(g, gid)
    users = db.execute(select(UserRow, OrgGroupUserRow).join(OrgGroupUserRow, OrgGroupUserRow.user_id == UserRow.id)
                       .where(OrgGroupUserRow.group_id == gid).order_by(UserRow.email)).all()
    inv = db.scalars(select(OrgInvitationRow).where(OrgInvitationRow.group_id == gid)
                     .order_by(OrgInvitationRow.email)).all()
    return {"group_id": gid, "public": gid == PUBLIC_ID,
            "users": [{"user_id": u.id, "email": u.email, "display_name": u.display_name, "status": u.status,
                       "added_at": R.iso(m.added_at), "added_by": m.added_by} for u, m in users],
            "invitations": [{"email": i.email, "invited_at": R.iso(i.invited_at), "invited_by": i.invited_by}
                            for i in inv],
            "groups": [{"id": m, "short_name": g.name(m)} for m in sorted(g.contains.get(gid, ()))]}


def _members_allowed(g: graphs.Graph, actor: R.Actor, gid: str) -> None:
    if gid == PUBLIC_ID:
        raise DirectoryError(422, "public_has_no_member_list")
    if not R.can_manage_members(g, actor, gid):
        raise DirectoryError(403, "member_manager_required")


def add_email(db: Session, actor: R.Actor, gid: str, email: str, now: datetime) -> tuple[dict, Change | None]:
    """Adds a person by address: member if the account exists, else an
    invitation (the account is created at the first connection)."""
    g = graphs.load(db)
    _group(g, gid)
    _members_allowed(g, actor, gid)
    try:
        email = accounts.normal_email(email)
    except accounts.AccountError:
        raise DirectoryError(422, "invalid_email") from None
    out, change = _add_email(db, actor, gid, email, now)
    return out, change


def _add_email(db: Session, actor: R.Actor, gid: str, email: str, now: datetime) -> tuple[dict, Change | None]:
    user = accounts.user_by_email(db, email)
    if user is not None:
        if db.get(OrgGroupUserRow, (gid, user.id)) is not None:
            return {"kind": "user", "user_id": user.id, "email": email, "status": "already"}, None
        db.add(OrgGroupUserRow(group_id=gid, user_id=user.id, added_at=now, added_by=actor.label[:64]))
        _rec(db, actor, "member_added", group_id=gid, user_id=user.id, email=email)
        return {"kind": "user", "user_id": user.id, "email": email, "status": "added"}, \
            _changed(db, now, False, "member_added")
    if db.get(OrgInvitationRow, (gid, email)) is not None:
        return {"kind": "invitation", "email": email, "status": "already"}, None
    db.add(OrgInvitationRow(group_id=gid, email=email, invited_at=now, invited_by=actor.label[:64]))
    _rec(db, actor, "invitation_added", group_id=gid, email=email)
    return {"kind": "invitation", "email": email, "status": "invited"}, None


def parse_addresses(text: str) -> tuple[list[str], list[str]]:
    """Addresses of a CSV list (one per line; commas or semicolons also
    accepted; a header « email » ignored). Returns (valid, invalid)."""
    valid, invalid = [], []
    for row in csv.reader(io.StringIO(text.replace(";", ","))):
        for cell in row:
            cell = cell.strip().strip('"').strip()
            if not cell or cell.lower() in ("email", "e-mail", "adresse", "courriel"):
                continue
            try:
                e = accounts.normal_email(cell)
            except accounts.AccountError:
                invalid.append(cell[:120])
                continue
            if e not in valid:
                valid.append(e)
    return valid, invalid


def import_addresses(db: Session, actor: R.Actor, gid: str, text: str, now: datetime) -> tuple[dict, Change | None]:
    g = graphs.load(db)
    _group(g, gid)
    _members_allowed(g, actor, gid)
    valid, invalid = parse_addresses(text)
    if len(valid) > MAX_IMPORT:
        raise DirectoryError(413, "too_many_addresses", max=MAX_IMPORT)
    result = {"added": 0, "invited": 0, "already": 0, "invalid": invalid[:200]}
    change = None
    for email in valid:
        out, ch = _add_email(db, actor, gid, email, now)
        result[{"added": "added", "invited": "invited"}.get(out["status"], "already")] += 1
        change = ch or change
    db.flush()
    _rec(db, actor, "members_imported", group_id=gid, added=result["added"], invited=result["invited"],
         already=result["already"], invalid=len(invalid))
    if result["invited"] and change is None:
        change = Change(R.version(db), False, "invitations")
    return result, change


def remove_user(db: Session, actor: R.Actor, gid: str, user_id: str, now: datetime) -> Change:
    g = graphs.load(db)
    _group(g, gid)
    _members_allowed(g, actor, gid)
    row = db.get(OrgGroupUserRow, (gid, user_id))
    if row is None:
        raise DirectoryError(404, "member_not_found")
    before = R.active_admins(db)
    db.delete(row)
    _guard_last_admin(db, actor, before, "member_removed")
    user = db.get(UserRow, user_id)
    _rec(db, actor, "member_removed", group_id=gid, user_id=user_id, email=user.email if user else None)
    return _changed(db, now, True, "member_removed")


def remove_invitation(db: Session, actor: R.Actor, gid: str, email: str, now: datetime) -> None:
    g = graphs.load(db)
    _group(g, gid)
    _members_allowed(g, actor, gid)
    row = db.get(OrgInvitationRow, (gid, (email or "").strip().lower()))
    if row is None:
        raise DirectoryError(404, "invitation_not_found")
    db.delete(row)
    _rec(db, actor, "invitation_removed", group_id=gid, email=row.email)


def add_group_member(db: Session, actor: R.Actor, gid: str, member_id: str, now: datetime) -> tuple[dict, Change]:
    """[member_id] joins [gid]: its members inherit the rights of [gid]
    (the author administers [gid], not necessarily [member_id])."""
    g = graphs.load(db)
    _group(g, gid)
    _group(g, member_id)
    if not can_admin(g, actor, gid):
        raise DirectoryError(403, "group_admin_required")
    if member_id in g.contains.get(gid, ()):
        return {"kind": "group", "group_id": member_id, "status": "already"}, Change(R.version(db), False, "unchanged")
    g.check_membership(gid, member_id)
    db.add(OrgGroupGroupRow(group_id=gid, member_id=member_id, added_at=now, added_by=actor.label[:64]))
    _rec(db, actor, "group_member_added", group_id=gid, member_id=member_id, short_name=g.name(member_id))
    return {"kind": "group", "group_id": member_id, "status": "added"}, _changed(db, now, False, "group_member_added")


def remove_group_member(db: Session, actor: R.Actor, gid: str, member_id: str, now: datetime) -> Change:
    g = graphs.load(db)
    _group(g, gid)
    if not can_admin(g, actor, gid):
        raise DirectoryError(403, "group_admin_required")
    row = db.get(OrgGroupGroupRow, (gid, member_id))
    if row is None:
        raise DirectoryError(404, "member_not_found")
    before = R.active_admins(db)
    db.delete(row)
    _guard_last_admin(db, actor, before, "group_member_removed")
    _rec(db, actor, "group_member_removed", group_id=gid, member_id=member_id, short_name=g.name(member_id))
    return _changed(db, now, True, "group_member_removed")


def accept_invitations(db: Session, user: UserRow) -> None:
    """At the creation of an account: its invitations become memberships."""
    from sarcade.roles import directory_ready
    if not directory_ready(db):
        return
    now = datetime.now(UTC)
    rows = db.scalars(select(OrgInvitationRow).where(OrgInvitationRow.email == user.email)).all()
    for inv in rows:
        if db.get(OrgGroupUserRow, (inv.group_id, user.id)) is None:
            db.add(OrgGroupUserRow(group_id=inv.group_id, user_id=user.id, added_at=now, added_by=inv.invited_by))
        db.delete(inv)
        journal.record(db, "rights", "invitation_accepted", actor=user.email,
                       details={"group_id": inv.group_id, "user_id": user.id, "invited_by": inv.invited_by})
    if rows:
        R.bump(db, now)


# --- Habilitations -------------------------------------------------------------------------------

def habilitation_dict(g: graphs.Graph, h: HabilitationRow, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    until = R._utc(h.until)
    return {"id": h.id, "group_id": h.group_id, "right": h.right, "label": R.right_label(h.right),
            "scope_group_id": h.scope_group_id,
            "scope_name": g.name(h.scope_group_id) if h.scope_group_id else None,
            "until": R.iso(until), "expired": until is not None and until <= now,
            "granted_at": R.iso(h.granted_at), "granted_by": h.granted_by}


def habilitations(db: Session, gid: str) -> list[dict]:
    g = graphs.load(db)
    _group(g, gid)
    rows = db.scalars(select(HabilitationRow).where(HabilitationRow.group_id == gid)
                      .order_by(HabilitationRow.granted_at, HabilitationRow.id)).all()
    return [habilitation_dict(g, h) for h in rows]


def _until(value, now: datetime) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise DirectoryError(422, "invalid_until") from None
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    if t <= now:
        raise DirectoryError(422, "until_in_past")
    return t


def grant(db: Session, actor: R.Actor, gid: str, data: dict, now: datetime) -> tuple[dict, Change]:
    g = graphs.load(db)
    holder = _group(g, gid)
    right = data.get("right")
    if right not in ADMIN_RIGHTS and right not in MODULE_RIGHTS:
        raise DirectoryError(422, "unknown_right")
    if holder.id == PUBLIC_ID:
        raise DirectoryError(422, "public_rights_refused",
                             message="Public porte les droits du Core seulement")
    scope = data.get("scope_group_id") or None
    kind = ADMIN_RIGHTS.get(right, (None, "none"))[1]
    if kind == "server" and scope is not None or kind == "none" and scope is not None:
        raise DirectoryError(422, "scope_not_allowed")
    if kind == "group" and scope is None:
        scope = gid  # default: the holder group itself
    if scope is not None:
        _group(g, scope)
        if scope == PUBLIC_ID:
            raise DirectoryError(422, "public_rights_refused")
    until = _until(data.get("until"), now)
    R.check_grant(g, actor, gid, right, scope)
    existing = db.scalar(select(HabilitationRow).where(
        HabilitationRow.group_id == gid, HabilitationRow.right == right,
        HabilitationRow.scope_group_id.is_(None) if scope is None else HabilitationRow.scope_group_id == scope))
    if existing is not None:
        raise DirectoryError(409, "habilitation_exists", id=existing.id)
    h = HabilitationRow(id=str(uuid.uuid4()), group_id=gid, right=right, scope_group_id=scope, until=until,
                        granted_at=now, granted_by=actor.label[:64])
    db.add(h)
    db.flush()
    _rec(db, actor, "habilitation_granted", group_id=gid, short_name=holder.short_name, right=right,
         scope_group_id=scope, until=R.iso(until))
    return habilitation_dict(g, h, now), _changed(db, now, False, "habilitation_granted")


def _habilitation(db: Session, g: graphs.Graph, actor: R.Actor, hid: str) -> HabilitationRow:
    h = db.get(HabilitationRow, hid)
    if h is None:
        raise DirectoryError(404, "habilitation_not_found")
    # Changing or removing a right needs the same rights as giving it.
    R.check_grant(g, actor, h.group_id, h.right, h.scope_group_id)
    return h


def set_until(db: Session, actor: R.Actor, hid: str, value, now: datetime) -> tuple[dict, Change]:
    g = graphs.load(db)
    h = _habilitation(db, g, actor, hid)
    until = _until(value, now)
    shorter = until is not None and (h.until is None or until < R._utc(h.until))
    if h.right == "server_admin" and until is not None and R.active_admins(db):
        # An end date on the last administrators would remove them later:
        # refused like a removal.
        h.until = now
        db.flush()
        left = R.active_admins(db)
        if not left:
            raise DirectoryError(409, "last_admin",
                                 message="Impossible : il faut toujours au moins un administrateur actif")
    h.until = until
    _rec(db, actor, "habilitation_until_changed", group_id=h.group_id, right=h.right,
         scope_group_id=h.scope_group_id, until=R.iso(until))
    return habilitation_dict(g, h, now), _changed(db, now, shorter, "habilitation_until_changed")


def revoke(db: Session, actor: R.Actor, hid: str, now: datetime) -> Change:
    g = graphs.load(db)
    h = _habilitation(db, g, actor, hid)
    before = R.active_admins(db)
    db.delete(h)
    _guard_last_admin(db, actor, before, "habilitation_revoked")
    _rec(db, actor, "habilitation_revoked", group_id=h.group_id, right=h.right, scope_group_id=h.scope_group_id)
    return _changed(db, now, True, "habilitation_revoked")


# --- Accounts -------------------------------------------------------------------------------------

def set_user_status(db: Session, actor: R.Actor, user_id: str, data: dict, now: datetime) -> tuple[dict, Change]:
    if not actor.server_admin:
        raise DirectoryError(403, "server_admin_required")
    user = db.get(UserRow, user_id)
    if user is None:
        raise DirectoryError(404, "user_not_found")
    revocation = False
    if "display_name" in data:
        user.display_name = _clean_text(data["display_name"], "display_name", 120)
    if "status" in data:
        if data["status"] not in ("active", "disabled"):
            raise DirectoryError(422, "invalid_status")
        if data["status"] != user.status:
            before = R.active_admins(db)
            user.status = data["status"]
            revocation = user.status == "disabled"
            if revocation:
                _guard_last_admin(db, actor, before, "account_disabled")
    _rec(db, actor, "account_updated", user_id=user.id, email=user.email, status=user.status)
    return accounts.user_dict(user), _changed(db, now, revocation, "account_updated")


accounts.on_account_created.append(accept_invitations)

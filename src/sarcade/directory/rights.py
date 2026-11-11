# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Effective rights of a person: groups reached by membership (direct
groups and Public, then the groups containing them), union of their
habilitations still valid, modules crossed with the licence of the
organisation. Kept in a cache, recomputed when the rights version changes
(every change of group, membership or habilitation) or when an
habilitation reaches its end date.

Also the checks of the administration (no elevation of rights) and the
rule « always at least one active administrator »."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import HabilitationRow, OrgGroupUserRow, UserRow
from sarcade.roles import env_admins
from sarcade.updates.service import get_value, set_value

from . import ADMIN_RIGHTS, PUBLIC_ID, DirectoryError, graph as graphs, licences, module_of, right_label

VERSION_KEY = "rights_version"
ENV_HOLDER = "SARCADE_ADMIN_EMAILS (.env)"


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def iso(t: datetime | None) -> str | None:
    t = _utc(t)
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


def version(db: Session) -> int:
    v = get_value(db, VERSION_KEY, {}) or {}
    return int(v.get("version", 0)) if isinstance(v, dict) else 0


def bump(db: Session, now: datetime | None = None) -> int:
    """New rights version: every cached right is recomputed."""
    v = version(db) + 1
    set_value(db, VERSION_KEY, {"version": v}, now)
    db.flush()
    return v


@dataclass
class Grant:
    right: str
    scope_group_id: str | None
    until: datetime | None
    holder_group_id: str | None          # None: safety access by the .env
    habilitation_id: str | None
    path: list[str]                      # holder … direct group (group ids)
    licensed: bool = True                # modules: covered by the licence
    organization_id: str | None = None   # modules: organisation of the holder


@dataclass
class Rights:
    user_id: str | None
    email: str | None
    version: int
    direct: set[str] = field(default_factory=set)
    reached: dict[str, str | None] = field(default_factory=dict)
    grants: list[Grant] = field(default_factory=list)
    expires_at: datetime | None = None   # first end date still to come

    @property
    def server_admin(self) -> bool:
        return any(g.right == "server_admin" for g in self.grants)

    def admin_grants(self) -> list[Grant]:
        return [g for g in self.grants if g.right in ADMIN_RIGHTS]

    def module_habilitations(self) -> set[str]:
        """Modules given by a group, licence or not."""
        return {module_of(g.right) for g in self.grants if module_of(g.right)}

    def modules(self) -> list[str]:
        """Modules usable: habilitation AND licence of the organisation."""
        return sorted({module_of(g.right) for g in self.grants if module_of(g.right) and g.licensed})

    @property
    def can_administer(self) -> bool:
        return bool(self.admin_grants())


_cache: dict[str, Rights] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def graph(db: Session) -> graphs.Graph:
    return graphs.load(db)


def compute(db: Session, user_id: str | None, email: str | None, now: datetime | None = None) -> Rights:
    """Effective rights, without cache. A device without account has the
    rights of Public (none beyond the Core)."""
    now = now or datetime.now(UTC)
    v = version(db)
    g = graph(db)
    direct = {PUBLIC_ID}
    if user_id is not None:
        user = db.get(UserRow, user_id)
        if user is None or user.status != "active":
            user_id = None  # disabled account: rights of Public only
    if user_id:
        direct |= set(db.scalars(select(OrgGroupUserRow.group_id).where(OrgGroupUserRow.user_id == user_id)))
    reached = g.reached(direct) if user_id or email else {PUBLIC_ID: None}
    out = Rights(user_id=user_id, email=email, version=v, direct=direct, reached=reached)
    org_modules: dict[str, set[str]] | None = None
    if reached:
        rows = db.scalars(select(HabilitationRow).where(HabilitationRow.group_id.in_(list(reached)))
                          .order_by(HabilitationRow.granted_at, HabilitationRow.id)).all()
        for h in rows:
            until = _utc(h.until)
            if until is not None and until <= now:
                continue
            if until is not None and (out.expires_at is None or until < out.expires_at):
                out.expires_at = until
            grant = Grant(right=h.right, scope_group_id=h.scope_group_id, until=until, holder_group_id=h.group_id,
                          habilitation_id=h.id, path=g.path_to(reached, h.group_id))
            module = module_of(h.right)
            if module:
                if org_modules is None:
                    org_modules = licences.modules_by_organization(db, g, now)
                org = g.organization_of(h.group_id)
                grant.organization_id = org
                grant.licensed = module in org_modules.get(org, set())
            out.grants.append(grant)
    if email and email.lower() in env_admins():
        out.grants.append(Grant(right="server_admin", scope_group_id=None, until=None, holder_group_id=None,
                                habilitation_id=None, path=[]))
    return out


def of(db: Session, user_id: str | None, email: str | None, now: datetime | None = None) -> Rights:
    """Effective rights from the cache, recomputed after a change."""
    now = now or datetime.now(UTC)
    key = f"{user_id}|{(email or '').lower()}"
    v = version(db)
    cached = _cache.get(key)
    if cached is not None and cached.version == v and (cached.expires_at is None or now < cached.expires_at):
        return cached
    rights = compute(db, user_id, email, now)
    with _lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[key] = rights
    return rights


def for_email(db: Session, email: str | None, now: datetime | None = None) -> Rights:
    """Rights of an address (account if any)."""
    user = db.scalar(select(UserRow).where(UserRow.email == email.lower())) if email else None
    if user is not None and user.status != "active":
        user = None
    return of(db, user.id if user else None, email, now)


# --- Readable: « Pourquoi ce droit ? » --------------------------------------------------

def grant_dict(g: graphs.Graph, grant: Grant, email: str | None) -> dict:
    names = [g.name(x) for x in grant.path]
    if grant.holder_group_id is None:
        text = f"{right_label(grant.right)} ← {ENV_HOLDER}"
    else:
        text = " ← ".join([right_label(grant.right)] + names + ([email] if email else []))
    d = {"right": grant.right, "label": right_label(grant.right),
         "scope_group_id": grant.scope_group_id,
         "scope_name": g.name(grant.scope_group_id) if grant.scope_group_id else None,
         "until": iso(grant.until), "habilitation_id": grant.habilitation_id,
         "holder_group_id": grant.holder_group_id,
         "holder_name": g.name(grant.holder_group_id) if grant.holder_group_id else ENV_HOLDER,
         "path": [{"id": x, "short_name": g.name(x)} for x in grant.path], "path_text": text}
    if module_of(grant.right):
        d.update(module=module_of(grant.right), licensed=grant.licensed, organization_id=grant.organization_id,
                 organization_name=g.name(grant.organization_id) if grant.organization_id else None)
    return d


def explain(db: Session, rights: Rights) -> dict:
    g = graph(db)
    groups = [{"id": gid, "short_name": g.name(gid), "name": g.groups[gid].name, "direct": gid in rights.direct,
               "path": [g.name(x) for x in g.path_to(rights.reached, gid)]}
              for gid in rights.reached if gid in g.groups]
    return {"user_id": rights.user_id, "email": rights.email, "version": rights.version,
            "server_admin": rights.server_admin, "modules": rights.modules(),
            "groups": groups, "rights": [grant_dict(g, x, rights.email) for x in rights.grants]}


# --- Administration checks (no elevation) --------------------------------------------------

@dataclass
class Actor:
    """Who administers: an account, an address of the .env, or the admin
    token of the .env (server administrator)."""
    label: str
    rights: Rights | None = None
    token: bool = False

    @property
    def server_admin(self) -> bool:
        return self.token or (self.rights is not None and self.rights.server_admin)

    def scopes(self, *names: str) -> list[str | None]:
        if self.rights is None:
            return []
        return [x.scope_group_id for x in self.rights.grants if x.right in names]


def _covered(g: graphs.Graph, scopes, gid: str) -> bool:
    return any(s is not None and gid in g.subtree(s) for s in scopes)


def can_admin_group(g: graphs.Graph, actor: Actor, gid: str) -> bool:
    """Administrator of the server, or of a group above by attachment."""
    return actor.server_admin or _covered(g, actor.scopes("group_admin"), gid)


def can_manage_members(g: graphs.Graph, actor: Actor, gid: str) -> bool:
    return can_admin_group(g, actor, gid) or gid in actor.scopes("member_manager")


def can_organize(g: graphs.Graph, actor: Actor, gid: str) -> bool:
    return can_admin_group(g, actor, gid) or gid in actor.scopes("event_organizer")


def can_audit(g: graphs.Graph, actor: Actor, gid: str | None) -> bool:
    if actor.server_admin or (None in actor.scopes("auditor")):
        return True
    return gid is not None and (can_admin_group(g, actor, gid) or _covered(g, actor.scopes("auditor"), gid))


def visible_groups(g: graphs.Graph, actor: Actor) -> set[str]:
    """Groups an administrator sees in the console (no other organisation)."""
    if actor.server_admin or None in actor.scopes("auditor"):
        return set(g.groups)
    out: set[str] = set()
    for s in actor.scopes("group_admin", "auditor"):
        if s in g.groups:
            out |= g.subtree(s)
    out |= {s for s in actor.scopes("member_manager", "event_organizer") if s in g.groups}
    return out


def check_grant(g: graphs.Graph, actor: Actor, holder: str, right: str, scope: str | None) -> None:
    """Giving [right] on [scope] to the group [holder]: the actor administers
    the holder and holds the right himself (no elevation)."""
    if not can_admin_group(g, actor, holder):
        raise DirectoryError(403, "group_admin_required")
    if actor.server_admin:
        return
    if right == "server_admin" or (right == "auditor" and scope is None):
        raise DirectoryError(403, "no_elevation", right=right)
    if right in ADMIN_RIGHTS:
        if not _covered(g, actor.scopes("group_admin"), scope):
            raise DirectoryError(403, "no_elevation", right=right)
        return
    module = module_of(right)
    if module and module not in (actor.rights.module_habilitations() if actor.rights else set()):
        raise DirectoryError(403, "no_elevation", right=right)


# --- Always at least one active administrator -----------------------------------------------

def active_admins(db: Session) -> set[str]:
    """Accounts (active) administrators of the server by the directory."""
    now = datetime.now(UTC)
    g = graphs.load(db)
    holders = set()
    for h in db.scalars(select(HabilitationRow).where(HabilitationRow.right == "server_admin")):
        until = _utc(h.until)
        if until is None or until > now:
            holders.add(h.group_id)
    groups = set()
    for holder in holders:
        groups |= {holder} | g.contained(holder)
    groups.discard(PUBLIC_ID)
    if not groups:
        return set()
    rows = db.execute(select(UserRow.id).join(OrgGroupUserRow, OrgGroupUserRow.user_id == UserRow.id)
                      .where(OrgGroupUserRow.group_id.in_(list(groups)), UserRow.status == "active")).all()
    return {r[0] for r in rows}

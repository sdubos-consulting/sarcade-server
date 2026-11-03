# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Directory of the server: organisation groups and habilitations (lot 26,
note « Groupes d'organisation et habilitations », decisions of 23 Oct and
3 Nov 2026).

- Two relations between groups: attachment (``parent_id``, a tree: who
  administers) and membership (``org_group_groups``, nested groups without
  cycle: who inherits the rights). A subgroup is both by default.
- Groups without parent are the organisations (a licence attaches to one),
  except the system groups.
- « Public » is a system group: every account, without member list, rights
  of the Core only; it neither contains nor joins a group, and no right can
  be given to it.
- Cycles refused with the path; at most 8 levels.
- Habilitations attach to groups only, positive only, with an optional end
  date: five administration rights without elevation, access to modules
  crossed with the licence of the organisation.
"""
from __future__ import annotations

from sarcade.licensing.core import MODULES as LICENSED_MODULES

PUBLIC_ID = "public"
SERVER_ADMINS_ID = "server-admins"
SYSTEM_IDS = (PUBLIC_ID, SERVER_ADMINS_ID)
MAX_DEPTH = 8

# Administration rights: id -> (label, scope). Scope « server » has no
# group, « group » needs one, « either » accepts both (auditor).
ADMIN_RIGHTS: dict[str, tuple[str, str]] = {
    "server_admin": ("Administrateur du serveur", "server"),
    "group_admin": ("Administrateur de groupe", "group"),
    "member_manager": ("Gestionnaire des membres", "group"),
    "event_organizer": ("Organisateur d'événements", "group"),
    "auditor": ("Auditeur", "either"),
}

MODULE_LABELS = {"adrasec": "ADRASEC", "race": "Course (Race)", "assist": "Assist", "siem": "SIEM"}
MODULE_RIGHTS: dict[str, str] = {f"module.{m}": f"Module {MODULE_LABELS.get(m, m)}" for m in LICENSED_MODULES}


def right_label(right: str) -> str:
    if right in ADMIN_RIGHTS:
        return ADMIN_RIGHTS[right][0]
    return MODULE_RIGHTS.get(right, right)


def module_of(right: str) -> str | None:
    return right.split(".", 1)[1] if right.startswith("module.") else None


def catalogue() -> list[dict]:
    out = [{"right": r, "label": label, "kind": "admin", "scope": scope} for r, (label, scope) in ADMIN_RIGHTS.items()]
    out += [{"right": r, "label": label, "kind": "module", "scope": "none", "module": module_of(r)}
            for r, label in MODULE_RIGHTS.items()]
    return out


class DirectoryError(Exception):
    """Refusal with an HTTP status and a stable detail code."""

    def __init__(self, status: int, detail: str, **extra):
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra

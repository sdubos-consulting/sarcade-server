# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Licence of an organisation (group without parent): access to a module
is the habilitation of a group AND the licence of its organisation
(decision 7 of 23 Oct 2026). Same signed documents as the Pro licence
(sarcade.licensing, checked offline); the ADRASEC module is free for the
ADRASEC with an association licence (``kind: association``).

The licences are kept in the server settings (``org_licences``: group id
-> document). The licence of the server (Paramètres › Administration ›
Licence) also counts for the organisation it names."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from sarcade import licensing
from sarcade.licensing import core
from sarcade.updates.service import get_value, set_value

KEY = "org_licences"
ACTIVE = ("valid", "expiring", "grace")


def documents(db: Session) -> dict[str, dict]:
    return dict(get_value(db, KEY, {}) or {})


def state_of(document: dict | None, now: datetime | None = None) -> core.LicenceState:
    return core.verify(document, now)


def install(db: Session, group_id: str, document: dict, now: datetime | None = None) -> core.LicenceState:
    """Checks then keeps the licence of an organisation; an invalid or
    expired one never replaces the installed licence."""
    state = core.verify(document, now)
    if state.status in ACTIVE:
        docs = documents(db)
        docs[group_id] = document
        set_value(db, KEY, docs)
    return state


def remove(db: Session, group_id: str) -> bool:
    docs = documents(db)
    if docs.pop(group_id, None) is None:
        return False
    set_value(db, KEY, docs)
    return True


def _server_licence_for(org) -> set[str]:
    try:
        s = licensing.state()
    except Exception:  # noqa: BLE001 - unreadable file: no module
        return set()
    if s.status not in ACTIVE:
        return set()
    named = str((s.licence or {}).get("organisation") or "").strip().lower()
    if named and named in {(org.short_name or "").lower(), (org.name or "").lower()}:
        return set(s.modules)
    return set()


def modules_by_organization(db: Session, graph, now: datetime | None = None) -> dict[str, set[str]]:
    """Modules covered for each organisation of the graph."""
    now = now or datetime.now(UTC)
    docs = documents(db)
    out: dict[str, set[str]] = {}
    for gid, row in graph.groups.items():
        if row.parent_id is not None or row.system:
            continue
        mods = set(_server_licence_for(row))
        doc = docs.get(gid)
        if doc is not None:
            st = core.verify(doc, now)
            if st.status in ACTIVE:
                mods |= set(st.modules)
        out[gid] = mods
    return out

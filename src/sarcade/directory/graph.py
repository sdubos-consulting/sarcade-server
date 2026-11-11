# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""The groups in memory: attachment tree, membership graph, cycles, depth.
A server holds a few hundred groups at most: the whole graph is read at
once and kept with the rights version."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from sarcade.db.models import OrgGroupGroupRow, OrgGroupRow

from . import MAX_DEPTH, PUBLIC_ID, DirectoryError


@dataclass
class Graph:
    groups: dict[str, OrgGroupRow] = field(default_factory=dict)
    parent: dict[str, str | None] = field(default_factory=dict)
    contains: dict[str, set[str]] = field(default_factory=dict)    # group -> member groups
    member_of: dict[str, set[str]] = field(default_factory=dict)   # group -> containing groups

    def name(self, gid: str | None) -> str:
        g = self.groups.get(gid) if gid else None
        return g.short_name if g is not None else str(gid)

    # --- Attachment (tree) ---------------------------------------------------------

    def ancestors(self, gid: str) -> list[str]:
        """Parents of [gid], nearest first."""
        out, seen = [], {gid}
        cur = self.parent.get(gid)
        while cur and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.parent.get(cur)
        return out

    def organization_of(self, gid: str) -> str:
        """Top of the attachment tree (the organisation)."""
        chain = self.ancestors(gid)
        return chain[-1] if chain else gid

    def children(self, gid: str) -> list[str]:
        return [g for g, p in self.parent.items() if p == gid]

    def subtree(self, gid: str) -> set[str]:
        """[gid] and every group attached under it."""
        out, todo = {gid}, [gid]
        while todo:
            for child in self.children(todo.pop()):
                if child not in out:
                    out.add(child)
                    todo.append(child)
        return out

    def tree_height_up(self, gid: str) -> int:
        return 1 + len(self.ancestors(gid))

    def tree_height_down(self, gid: str) -> int:
        kids = self.children(gid)
        return 1 + max((self.tree_height_down(k) for k in kids), default=0)

    # --- Membership (graph without cycle) ---------------------------------------------

    def contained(self, gid: str) -> set[str]:
        """Groups contained by [gid], directly or nested."""
        out, todo = set(), [gid]
        while todo:
            for m in self.contains.get(todo.pop(), ()):
                if m not in out:
                    out.add(m)
                    todo.append(m)
        return out

    def containment_path(self, top: str, bottom: str) -> list[str] | None:
        """Shortest chain top ⊃ … ⊃ bottom by membership, or None."""
        prev: dict[str, str | None] = {top: None}
        queue = deque([top])
        while queue:
            g = queue.popleft()
            if g == bottom:
                path = [g]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            for m in sorted(self.contains.get(g, ())):
                if m not in prev:
                    prev[m] = g
                    queue.append(m)
        return None

    def depth_up(self, gid: str) -> int:
        """Longest chain of containing groups above [gid], [gid] included."""
        memo: dict[str, int] = {}

        def up(g: str, stack: frozenset) -> int:
            if g in memo:
                return memo[g]
            best = 1 + max((up(c, stack | {g}) for c in self.member_of.get(g, ()) if c not in stack), default=0)
            memo[g] = best
            return best
        return up(gid, frozenset())

    def depth_down(self, gid: str) -> int:
        memo: dict[str, int] = {}

        def down(g: str, stack: frozenset) -> int:
            if g in memo:
                return memo[g]
            best = 1 + max((down(m, stack | {g}) for m in self.contains.get(g, ()) if m not in stack), default=0)
            memo[g] = best
            return best
        return down(gid, frozenset())

    def reached(self, direct: set[str]) -> dict[str, str | None]:
        """Groups reached from the direct groups of a person by membership,
        each with the group it was reached from (None for a direct group):
        the « Pourquoi ce droit ? » path."""
        prev: dict[str, str | None] = {g: None for g in sorted(direct) if g in self.groups}
        queue = deque(prev)
        while queue:
            g = queue.popleft()
            for c in sorted(self.member_of.get(g, ())):
                if c not in prev:
                    prev[c] = g
                    queue.append(c)
        return prev

    @staticmethod
    def path_to(prev: dict[str, str | None], gid: str) -> list[str]:
        """[gid, …, direct group]."""
        path = [gid]
        while prev.get(path[-1]) is not None:
            path.append(prev[path[-1]])
        return path

    # --- Checks before a change -----------------------------------------------------------

    def check_membership(self, container: str, member: str) -> None:
        """Adding [member] into [container]: no Public, no cycle (with the
        path), at most MAX_DEPTH levels."""
        if PUBLIC_ID in (container, member):
            raise DirectoryError(422, "public_not_nested")
        if container == member:
            raise DirectoryError(409, "cycle", path=[self.name(member)],
                                 message=f"Impossible : {self.name(member)} ne peut pas se contenir")
        path = self.containment_path(member, container)
        if path is not None:
            names = [self.name(g) for g in path]
            text = f"Impossible : {names[0]} contient déjà " + ", qui contient ".join(names[1:])
            raise DirectoryError(409, "cycle", path=names, message=text)
        levels = self.depth_up(container) + self.depth_down(member)
        if levels > MAX_DEPTH:
            raise DirectoryError(409, "too_deep", levels=levels, max_depth=MAX_DEPTH,
                                 message=f"Impossible : {levels} niveaux, {MAX_DEPTH} au plus")

    def check_parent(self, gid: str, parent: str | None) -> None:
        """Attaching [gid] under [parent]: not under itself nor one of its
        descendants, at most MAX_DEPTH levels."""
        if parent is None:
            return
        if PUBLIC_ID in (gid, parent):
            raise DirectoryError(422, "public_not_nested")
        if parent == gid or parent in self.subtree(gid):
            chain = [parent] + self.ancestors(parent)
            names = [self.name(g) for g in reversed(chain[:chain.index(gid) + 1])]
            raise DirectoryError(409, "cycle", path=names,
                                 message=f"Impossible : {self.name(parent)} est déjà rattaché sous "
                                         f"{self.name(gid)} ({' › '.join(names)})")
        levels = self.tree_height_up(parent) + self.tree_height_down(gid)
        if levels > MAX_DEPTH:
            raise DirectoryError(409, "too_deep", levels=levels, max_depth=MAX_DEPTH,
                                 message=f"Impossible : {levels} niveaux, {MAX_DEPTH} au plus")


def load(db: Session) -> Graph:
    g = Graph()
    for row in db.scalars(select(OrgGroupRow)):
        g.groups[row.id] = row
        g.parent[row.id] = row.parent_id
    for edge in db.scalars(select(OrgGroupGroupRow)):
        g.contains.setdefault(edge.group_id, set()).add(edge.member_id)
        g.member_of.setdefault(edge.member_id, set()).add(edge.group_id)
    return g

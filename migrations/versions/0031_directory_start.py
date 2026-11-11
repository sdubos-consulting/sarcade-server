# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Starting organisations and transition (lot 26, decisions 4 and 5 of
3 Nov 2026):

- FNRASEC › ADRASEC 78 (module ADRASEC) › PCO ADRASEC 78 (organisers of the
  events of ADRASEC 78), VCMB, LGA;
- events: organising group, invited groups; existing events attached to
  ADRASEC 78;
- lists « administrateurs » and « PCO » of 28 October taken over: the
  administrators become members of « Administrateurs du serveur », the PCO
  members of « PCO ADRASEC 78 » (accounts created for their addresses,
  already checked by Cloudflare Access).

A group of the same short name created by hand in the meantime is reused.
"""
from datetime import UTC, datetime
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

# (id, short name, name, parent, member of parent, colour)
GROUPS = (
    ("fnrasec", "FNRASEC", "Fédération nationale des ADRASEC", None, False, "#1565C0"),
    ("adrasec-78", "ADRASEC 78", "ADRASEC des Yvelines", "fnrasec", True, "#1E88E5"),
    ("pco-adrasec-78", "PCO ADRASEC 78", "Chefs PCO de l'ADRASEC 78", "adrasec-78", True, "#C62828"),
    ("vcmb", "VCMB", "Vélo Club de Montigny", None, False, "#2E7D32"),
    ("lga", "LGA", "La Grange Animée", None, False, "#EF6C00"),
)


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def upgrade():
    op.add_column("events", sa.Column("organizer_group_id", sa.String(64), nullable=True))
    op.create_index("ix_events_organizer_group_id", "events", ["organizer_group_id"])
    op.create_table(
        "event_invited_groups",
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("group_id", sa.String(64), sa.ForeignKey("org_groups.id"), primary_key=True, index=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by", sa.String(64)),
    )
    bind = op.get_bind()
    now = datetime.now(UTC)
    ids: dict[str, str] = {}
    for gid, short, name, parent, member, color in GROUPS:
        found = bind.execute(sa.text("SELECT id FROM org_groups WHERE lower(short_name) = :s"),
                             {"s": short.lower()}).scalar()
        if found is None:
            bind.execute(sa.text(
                "INSERT INTO org_groups (id, short_name, name, color, parent_id, system, created_at, created_by,"
                " updated_at, updated_by) VALUES (:id, :s, :n, :c, :p, :f, :t, 'migration', :t, 'migration')"),
                {"id": gid, "s": short, "n": name, "c": color, "p": ids.get(parent) if parent else None,
                 "f": False, "t": now})
            found = gid
        ids[gid] = found
        if parent and member:
            exists = bind.execute(sa.text("SELECT 1 FROM org_group_groups WHERE group_id = :g AND member_id = :m"),
                                  {"g": ids[parent], "m": found}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO org_group_groups (group_id, member_id, added_at, added_by)"
                                     " VALUES (:g, :m, :t, 'migration')"), {"g": ids[parent], "m": found, "t": now})

    def grant(hid, group, right, scope):
        exists = bind.execute(sa.text("SELECT 1 FROM habilitations WHERE group_id = :g AND \"right\" = :r"),
                              {"g": group, "r": right}).scalar()
        if not exists:
            bind.execute(sa.text("INSERT INTO habilitations (id, group_id, \"right\", scope_group_id, until,"
                                 " granted_at, granted_by) VALUES (:id, :g, :r, :s, NULL, :t, 'migration')"),
                         {"id": hid, "g": group, "r": right, "s": scope, "t": now})

    grant("adrasec-78-module", ids["adrasec-78"], "module.adrasec", None)
    grant("pco-adrasec-78-events", ids["pco-adrasec-78"], "event_organizer", ids["adrasec-78"])

    # Existing events: organised by ADRASEC 78.
    bind.execute(sa.text("UPDATE events SET organizer_group_id = :g WHERE organizer_group_id IS NULL"),
                 {"g": ids["adrasec-78"]})

    # Lists of 28 October -> groups.
    row = bind.execute(sa.text("SELECT value FROM server_settings WHERE key = 'roles'")).scalar()
    lists = _json(row) or {}
    targets = (("admins", "server-admins"), ("pco", ids["pco-adrasec-78"]))
    for key, group in targets:
        for email in lists.get(key) or []:
            email = str(email).strip().lower()
            if not email or "@" not in email:
                continue
            uid = bind.execute(sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar()
            if uid is None:
                uid = str(uuid.uuid4())
                bind.execute(sa.text("INSERT INTO users (id, email, status, created_at, created_by)"
                                     " VALUES (:id, :e, 'active', :t, 'migration')"), {"id": uid, "e": email, "t": now})
            exists = bind.execute(sa.text("SELECT 1 FROM org_group_users WHERE group_id = :g AND user_id = :u"),
                                  {"g": group, "u": uid}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO org_group_users (group_id, user_id, added_at, added_by)"
                                     " VALUES (:g, :u, :t, 'migration')"), {"g": group, "u": uid, "t": now})
    if row is not None:
        moved = json.dumps({"admins": [], "pco": [], "moved_to_groups": now.isoformat(),
                            "before": {"admins": lists.get("admins") or [], "pco": lists.get("pco") or []}})
        bind.execute(sa.text("UPDATE server_settings SET value = CAST(:v AS JSON), updated_at = :t WHERE key = 'roles'"),
                     {"v": moved, "t": now})
    # The lists « administrateurs » and « PCO » of the console (PUT
    # /admin/roles) now write in these two groups.
    settings = json.dumps({"default_organizer_group_id": ids["adrasec-78"], "pco_group_id": ids["pco-adrasec-78"]})
    if bind.execute(sa.text("SELECT 1 FROM server_settings WHERE key = 'directory'")).scalar() is None:
        bind.execute(sa.text("INSERT INTO server_settings (key, value, updated_at) VALUES ('directory', CAST(:v AS JSON), :t)"),
                     {"v": settings, "t": now})
    # New rights version: every cache recomputed.
    version = json.dumps({"version": int(now.timestamp())})
    if bind.execute(sa.text("SELECT 1 FROM server_settings WHERE key = 'rights_version'")).scalar() is None:
        bind.execute(sa.text("INSERT INTO server_settings (key, value, updated_at) VALUES "
                             "('rights_version', CAST(:v AS JSON), :t)"), {"v": version, "t": now})
    else:
        bind.execute(sa.text("UPDATE server_settings SET value = CAST(:v AS JSON), updated_at = :t "
                             "WHERE key = 'rights_version'"), {"v": version, "t": now})


def downgrade():
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT value FROM server_settings WHERE key = 'roles'")).scalar()
    lists = _json(row) or {}
    if "before" in lists:
        bind.execute(sa.text("UPDATE server_settings SET value = CAST(:v AS JSON) WHERE key = 'roles'"),
                     {"v": json.dumps(lists["before"])})
    bind.execute(sa.text("DELETE FROM server_settings WHERE key = 'directory'"))
    op.drop_table("event_invited_groups")
    op.drop_index("ix_events_organizer_group_id", "events")
    op.drop_column("events", "organizer_group_id")

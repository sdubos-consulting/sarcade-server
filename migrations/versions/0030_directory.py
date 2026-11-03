# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Organisation groups (attachment and nested membership), invitations and
habilitations (lot 26). System groups: « Public » (every account, rights of
the Core) and « Administrateurs du serveur » (right « administrateur du
serveur »)."""
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "org_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("short_name", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("color", sa.String(9)),
        sa.Column("parent_id", sa.String(64), sa.ForeignKey("org_groups.id"), index=True),
        sa.Column("system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64)),
    )
    op.create_table(
        "org_group_users",
        sa.Column("group_id", sa.String(64), sa.ForeignKey("org_groups.id"), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), primary_key=True, index=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("added_by", sa.String(64)),
    )
    op.create_table(
        "org_group_groups",
        sa.Column("group_id", sa.String(64), sa.ForeignKey("org_groups.id"), primary_key=True),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("org_groups.id"), primary_key=True, index=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("added_by", sa.String(64)),
    )
    op.create_table(
        "org_invitations",
        sa.Column("group_id", sa.String(64), sa.ForeignKey("org_groups.id"), primary_key=True),
        sa.Column("email", sa.String(320), primary_key=True, index=True),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by", sa.String(64)),
    )
    op.create_table(
        "habilitations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("group_id", sa.String(64), sa.ForeignKey("org_groups.id"), nullable=False, index=True),
        sa.Column("right", sa.String(40), nullable=False),
        sa.Column("scope_group_id", sa.String(64), sa.ForeignKey("org_groups.id")),
        sa.Column("until", sa.DateTime(timezone=True)),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", sa.String(64)),
    )
    now = datetime.now(UTC)
    groups = sa.table("org_groups", sa.column("id"), sa.column("short_name"), sa.column("name"),
                      sa.column("description"), sa.column("system", sa.Boolean), sa.column("created_at"),
                      sa.column("created_by"), sa.column("updated_at"), sa.column("updated_by"))
    op.bulk_insert(groups, [
        {"id": "public", "short_name": "Public", "name": "Public (tous les comptes)",
         "description": "Groupe système : tous les comptes, droits du Core", "system": True,
         "created_at": now, "created_by": "SARCADE", "updated_at": now, "updated_by": "SARCADE"},
        {"id": "server-admins", "short_name": "Administrateurs", "name": "Administrateurs du serveur",
         "description": "Groupe système : administrateurs du serveur", "system": True,
         "created_at": now, "created_by": "SARCADE", "updated_at": now, "updated_by": "SARCADE"},
    ])
    habilitations = sa.table("habilitations", sa.column("id"), sa.column("group_id"), sa.column("right"),
                             sa.column("scope_group_id"), sa.column("until"), sa.column("granted_at"),
                             sa.column("granted_by"))
    op.bulk_insert(habilitations, [
        {"id": "server-admins-right", "group_id": "server-admins", "right": "server_admin", "scope_group_id": None,
         "until": None, "granted_at": now, "granted_by": "SARCADE"},
    ])


def downgrade():
    op.drop_table("habilitations")
    op.drop_table("org_invitations")
    op.drop_table("org_group_groups")
    op.drop_table("org_group_users")
    op.drop_table("org_groups")

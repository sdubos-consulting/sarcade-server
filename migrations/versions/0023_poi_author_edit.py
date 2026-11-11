# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Points of interest: author, colour, rename and deletion (29 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0023"
# 0022 is reserved by another subject: if it is merged first, chain on it.
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pois", sa.Column("created_by", sa.String(64)))
    op.add_column("pois", sa.Column("color", sa.BigInteger()))
    op.add_column("pois", sa.Column("updated_by", sa.String(64)))
    op.add_column("pois", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column("pois", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade():
    for name in ("deleted_at", "updated_at", "updated_by", "color", "created_by"):
        op.drop_column("pois", name)

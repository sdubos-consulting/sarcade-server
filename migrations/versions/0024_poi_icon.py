# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Points of interest: shape of the icon (30 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pois", sa.Column("icon", sa.String(32)))


def downgrade():
    op.drop_column("pois", "icon")

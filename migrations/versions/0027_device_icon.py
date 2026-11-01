# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Icon of the operator on the map (1 Nov 2026): walk, car, helicopter…,
same meanings as the APRS symbols; chosen in Paramètres › Profil."""

from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("icon", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("devices", "icon")

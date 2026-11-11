# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Colour of the operator, shared with the other devices (card « Carte : la
trace d'un opérateur dans la couleur choisie dans son profil », 10 Nov 2026)."""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("color", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "color")

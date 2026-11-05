# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Forwarded message: origin kept with the message (lot 29, group 7)."""
import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("forwarded_from", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "forwarded_from")

# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Changing a sent message within 15 min (lot 29, group 7): time of the
change and the versions before it."""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("versions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "versions")
    op.drop_column("messages", "edited_at")

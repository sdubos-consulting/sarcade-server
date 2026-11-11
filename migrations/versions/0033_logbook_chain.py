# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Tamper-evident Main courante (lot 29, group 8): each entry carries the
hash of the previous one of the event. Entries already written stay without
hash (« avant chaînage »)."""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("logbook", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("logbook", sa.Column("hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("logbook", "hash")
    op.drop_column("logbook", "prev_hash")

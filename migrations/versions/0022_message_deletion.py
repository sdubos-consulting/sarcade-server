# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Deletion of a message for everyone (29 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("deleted_by", sa.String(64), nullable=True))
    op.add_column("messages", sa.Column("deleted_by_pco", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("deleted_by_pco")
        batch.drop_column("deleted_by")
        batch.drop_column("deleted_at")

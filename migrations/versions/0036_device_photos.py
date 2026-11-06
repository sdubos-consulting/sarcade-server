# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Profile photo of an operator (lot 29, group 7)."""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_photos",
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(320)),
    )


def downgrade() -> None:
    op.drop_table("device_photos")

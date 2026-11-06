# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Reactions on a message (lot 29, group 7)."""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_reactions",
        sa.Column("message_id", sa.String(64), sa.ForeignKey("messages.id"), primary_key=True),
        sa.Column("actor_id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("emoji", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_message_reactions_event_id", "message_reactions", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_message_reactions_event_id", table_name="message_reactions")
    op.drop_table("message_reactions")

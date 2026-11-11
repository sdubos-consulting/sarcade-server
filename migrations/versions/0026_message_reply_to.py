# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Reply to a message (31 Oct 2026): optional id of the quoted message."""

from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("reply_to", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("messages", "reply_to")

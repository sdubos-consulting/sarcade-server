# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""Status of the operator in one touch (lot 24, 3 Nov 2026): en route, sur
place, disponible, pause, with the time it was chosen, kept per device of
the event."""

from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("operator_status", sa.String(16), nullable=True))
    op.add_column("devices", sa.Column("operator_status_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("devices", "operator_status_at")
    op.drop_column("devices", "operator_status")

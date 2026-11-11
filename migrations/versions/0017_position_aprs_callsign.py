"""Callsign of an APRS position, kept when the station is linked to a
terminal (« PCO-demo · F4JPO-5 », 28 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("positions", sa.Column("aprs_callsign", sa.String(12), nullable=True))


def downgrade():
    op.drop_column("positions", "aprs_callsign")

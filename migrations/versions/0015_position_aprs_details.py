"""APRS details of a position: symbol and path (interface V2, 27 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("positions", sa.Column("aprs_symbol", sa.String(2), nullable=True))
    op.add_column("positions", sa.Column("aprs_path", sa.String(160), nullable=True))


def downgrade():
    op.drop_column("positions", "aprs_path")
    op.drop_column("positions", "aprs_symbol")

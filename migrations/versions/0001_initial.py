"""Initial SARCADE schema."""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table("organizations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"))
    op.create_table("events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), sa.ForeignKey("organizations.id")),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("teams",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="available"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("positions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("point", Geography(geometry_type="POINT", srid=4326), nullable=False),
        sa.Column("alt_m", sa.Float()),
        sa.Column("accuracy_m", sa.Float()),
        sa.Column("heading_deg", sa.Float()),
        sa.Column("speed_mps", sa.Float()),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("positions")
    op.drop_table("teams")
    op.drop_table("events")
    op.drop_table("organizations")

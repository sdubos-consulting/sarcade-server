"""POI and operational indexes."""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_positions_event_device_time", "positions", ["event_id", "device_id", "time"])
    op.create_table(
        "pois",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("label", sa.String(160)),
        sa.Column("point", Geography(geometry_type="POINT", srid=4326), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_pois_event_id", "pois", ["event_id"])


def downgrade():
    op.drop_table("pois")
    op.drop_index("ix_positions_event_device_time", table_name="positions")

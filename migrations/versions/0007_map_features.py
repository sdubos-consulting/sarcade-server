"""Drawn map objects with last-writer-wins tombstones (ADR-001)."""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "map_features",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("geom", Geography(geometry_type="GEOMETRY", srid=4326, spatial_index=True), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_map_features_event_id", "map_features", ["event_id"])


def downgrade():
    op.drop_table("map_features")

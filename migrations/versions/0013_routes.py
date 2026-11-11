"""Routes, waypoints and passages (last writer wins per object)."""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "route_objects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("route_id", sa.String(64)),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_route_objects_event_id", "route_objects", ["event_id"])
    op.create_index("ix_route_objects_route_id", "route_objects", ["route_id"])


def downgrade():
    op.drop_table("route_objects")

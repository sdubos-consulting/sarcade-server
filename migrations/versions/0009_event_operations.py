"""Event settings set by the PCO, device last contact, position battery and source."""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("settings", sa.JSON(), nullable=True))
    op.add_column("events", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("positions", sa.Column("battery_pct", sa.Integer(), nullable=True))
    op.add_column("positions", sa.Column("source", sa.String(16), nullable=False, server_default="device"))
    op.create_table(
        "devices",
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("label", sa.String(120)),
        sa.Column("platform", sa.String(32)),
        sa.Column("app_version", sa.String(32)),
        sa.Column("battery_pct", sa.Integer()),
        sa.Column("pending_count", sa.Integer()),
        sa.Column("oldest_pending_at", sa.DateTime(timezone=True)),
        sa.Column("tracking_enabled", sa.Boolean()),
        sa.Column("tracking_interval_s", sa.Integer()),
        sa.Column("last_contact_at", sa.DateTime(timezone=True)),
        sa.Column("last_position_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("devices")
    op.drop_column("positions", "source")
    op.drop_column("positions", "battery_pct")
    op.drop_column("events", "ended_at")
    op.drop_column("events", "settings")

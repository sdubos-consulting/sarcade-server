"""Security journal, server settings, update history, client versions."""

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "security_events",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(64)),
        sa.Column("device_id", sa.String(64)),
        sa.Column("event_id", sa.String(64)),
        sa.Column("source_ip", sa.String(64)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_security_events_at", "security_events", ["at"])
    op.create_index("ix_security_events_category", "security_events", ["category"])
    op.create_table(
        "server_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "server_updates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_version", sa.String(32)),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("detail", sa.Text()),
    )
    op.create_table(
        "client_devices",
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("platform", sa.String(32)),
        sa.Column("app_version", sa.String(32)),
        sa.Column("invited_at", sa.DateTime(timezone=True)),
        sa.Column("invited_for", sa.String(32)),
        sa.Column("deferred_logged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("refused_logged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("client_devices")
    op.drop_table("server_updates")
    op.drop_table("server_settings")
    op.drop_index("ix_security_events_category", "security_events")
    op.drop_index("ix_security_events_at", "security_events")
    op.drop_table("security_events")

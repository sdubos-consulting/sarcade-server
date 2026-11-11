"""APRS: callsign groups, device callsign and consent, position channel."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aprs_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("callsigns", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
    )
    op.add_column("devices", sa.Column("callsign", sa.String(16), nullable=True))
    op.add_column("devices", sa.Column("aprs_tx_consent", sa.Boolean(), nullable=True))
    op.create_index("ix_devices_callsign", "devices", ["callsign"])
    op.add_column("positions", sa.Column("aprs_via", sa.String(8), nullable=True))


def downgrade():
    op.drop_column("positions", "aprs_via")
    op.drop_index("ix_devices_callsign", "devices")
    op.drop_column("devices", "aprs_tx_consent")
    op.drop_column("devices", "callsign")
    op.drop_table("aprs_groups")

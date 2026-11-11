# SPDX-License-Identifier: AGPL-3.0-only
# © SDUBOS Consulting et contributeurs SARCADE
"""APRS stations classified by hand as fixed or mobile (30 Oct 2026)."""

from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aprs_station_classes",
        sa.Column("event_id", sa.String(64), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("aprs_station_classes")

"""Reference sites imported from operational map sources."""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reference_sites",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("subtype", sa.String(64)),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("callsign", sa.String(64)),
        sa.Column("point", Geography(geometry_type="POINT", srid=4326), nullable=False),
        sa.Column("alt_m", sa.Float()),
        sa.Column("access", sa.Text()),
        sa.Column("clearance", sa.Text()),
        sa.Column("mode", sa.String(64)),
        sa.Column("rx_mhz", sa.Float()),
        sa.Column("tx_mhz", sa.Float()),
        sa.Column("ctcss_rx", sa.String(32)),
        sa.Column("ctcss_tx", sa.String(32)),
        sa.Column("offset", sa.String(32)),
        sa.Column("description", sa.Text()),
        sa.Column("verified_at", sa.String(32)),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_layer", sa.String(128), nullable=False),
        sa.Column("source_object_id", sa.String(128), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_properties", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source", "source_layer", "source_object_id",
            name="uq_reference_site_source_object",
        ),
    )
    op.create_index("ix_reference_sites_category", "reference_sites", ["category"])
    op.create_index("ix_reference_sites_status", "reference_sites", ["status"])
    op.create_index("ix_reference_sites_name", "reference_sites", ["name"])


def downgrade():
    op.drop_table("reference_sites")

"""Custom base maps added by the administrator."""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "custom_basemaps",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table("custom_basemaps")

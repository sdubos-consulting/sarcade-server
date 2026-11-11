"""Weather cache: forecasts and vigilance served without Internet."""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "weather_cache",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("weather_cache")
